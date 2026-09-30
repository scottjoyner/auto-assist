"""Fleet loadout recommendation.

A *circumstance* describes what work the fleet is being asked to do right now
(for example: production LLM traffic needs an endpoint, a soak drill is
scheduled, an interactive studio lane is idle). A *loadout* is a concrete set
of residents -- models or bound runtimes -- that could serve that
circumstance on the measured hardware.

This module answers one question: given the observed machine state and a
declared circumstance, which loadouts are even feasible, and which feasible
loadout is preferred. It is deliberately conservative:

* Feasibility is a hard constraint, never a score. A loadout that cannot fit
  in measured VRAM, or that breaks a declared exclusivity rule, is not a
  candidate.
* Preferences are declared in the checked-in policy file with an explicit
  ``provenance`` (``measured`` when we have an observation, ``declared`` when
  a human set it). Nothing is silently invented here.
* The recommender never mutates the fleet. It returns a recommendation with
  all-false authority, exactly like the inference policy recommender, and the
  operator applies it.

The decision slot is shaped for the my-jev decision model: every candidate is
an option, every need is a question, and outcomes attach per
(circumstance, loadout) pair. While the evidence set is empty the model stays
inactive and the declared preference decides; once measured outcomes exist the
scorer consumes them the same way the policy-training bundle consumes frozen
latency evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .inference_policy_experiment import DEFAULT_AUTHORITY

POLICY_SCHEMA = "assistx-fleet-loadout-policy-v1"
RECOMMENDATION_SCHEMA = "assistx-fleet-loadout-recommendation-v1"

#: Needs a circumstance can declare. Each need must be satisfied by a loadout.
KNOWN_NEEDS = (
    "prod_llm_endpoint",
    "prod_embedder",
    "soak_drill_runtime",
    "interactive_lane",
    "evals_runtime",
)

#: Residency kinds that can satisfy a need.
KNOWN_RESIDENT_KINDS = ("llm", "embedder", "runtime", "draft")

#: How many measured outcomes for a circumstance are needed before the decision
#: model may be considered for scoring, in addition to covering every feasible
#: candidate. Until then the declared preference decides and says so.
MODEL_EVIDENCE_THRESHOLD = 2

#: Whether a model has been proven loadable on the local runtime. ``failed`` is
#: a hard feasibility rejection: a loadout that needs a model the runtime cannot
#: load is not a plan, no matter how well it fits in VRAM.
KNOWN_LOAD_STATUSES = ("verified", "unverified", "failed")


class LoadoutPolicyError(ValueError):
    """Raised when a policy file is structurally invalid."""


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise LoadoutPolicyError(f"{where}: missing required key {key!r}")
    return mapping[key]


def _as_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LoadoutPolicyError(f"{where}: expected integer, got {value!r}")
    if value < 0:
        raise LoadoutPolicyError(f"{where}: expected non-negative integer")
    return value


class Policy:
    """A validated loadout policy.

    The policy is data: devices and models carry measured capacity, loadouts
    declare residents, needs declare what they require, and preferences
    declare which loadout a human wants when several are feasible.
    """

    def __init__(self, raw: dict[str, Any]):
        if not isinstance(raw, dict):
            raise LoadoutPolicyError("policy: expected JSON object")
        schema = str(raw.get("schema") or "")
        if schema != POLICY_SCHEMA:
            raise LoadoutPolicyError(
                f"policy: expected schema {POLICY_SCHEMA}, got {schema!r}"
            )
        self.raw = raw
        self.policy_id = str(_require(raw, "policy_id", "policy"))
        self.captured_at = str(raw.get("captured_at") or "")

        devices: dict[str, dict[str, int]] = {}
        for index, device in enumerate(raw.get("devices") or []):
            where = f"devices[{index}]"
            if not isinstance(device, dict):
                raise LoadoutPolicyError(f"{where}: expected object")
            name = str(_require(device, "name", where))
            devices[name] = {
                "vram_total_bytes": _as_int(
                    _require(device, "vram_total_bytes", f"{where}.{name}"),
                    f"{where}.{name}.vram_total_bytes",
                ),
                "vram_used_bytes": _as_int(
                    device.get("vram_used_bytes", 0),
                    f"{where}.{name}.vram_used_bytes",
                ),
            }
        if not devices:
            raise LoadoutPolicyError("policy.devices: at least one device required")
        self.devices = devices

        self.models: dict[str, dict[str, Any]] = {}
        for index, model in enumerate(raw.get("models") or []):
            where = f"models[{index}]"
            if not isinstance(model, dict):
                raise LoadoutPolicyError(f"{where}: expected object")
            key = str(_require(model, "model_key", where))
            status = str(model.get("load_status") or "unverified")
            if status not in KNOWN_LOAD_STATUSES:
                raise LoadoutPolicyError(
                    f"{where}.{key}.load_status: unknown status {status!r}"
                )
            self.models[key] = {
                "kind": str(_require(model, "kind", f"{where}.{key}")),
                "vram_bytes": _as_int(
                    _require(model, "vram_bytes", f"{where}.{key}"),
                    f"{where}.{key}.vram_bytes",
                ),
                "display_name": str(model.get("display_name") or key),
                "load_status": status,
                "load_note": str(model.get("load_note") or ""),
                # A model whose measured resident cost means it must hold the
                # device alone. Capacity arithmetic alone cannot express this:
                # 22.1 + 5.8 GiB fits inside 31.9 GiB on paper, and the runtime
                # still refuses the allocation.
                "exclusive_gpu": bool(model.get("exclusive_gpu", False)),
                # Where the load was actually verified. The drill runtime is
                # proven by real task-quality campaigns; LM Studio loadability
                # is a separate question and is not implied by it.
                "verified_in": str(model.get("verified_in") or ""),
            }
        if not self.models:
            raise LoadoutPolicyError("policy.models: at least one model required")

        self.needs: dict[str, dict[str, Any]] = {}
        for index, need in enumerate(raw.get("needs") or []):
            where = f"needs[{index}]"
            if not isinstance(need, dict):
                raise LoadoutPolicyError(f"{where}: expected object")
            name = str(_require(need, "need", where))
            if name not in KNOWN_NEEDS:
                raise LoadoutPolicyError(
                    f"{where}.{name}: unknown need {name!r}"
                )
            self.needs[name] = {
                "description": str(need.get("description") or ""),
                "resident_kind": str(
                    _require(need, "resident_kind", f"{where}.{name}")
                ),
                # An optional role pin. Without it, any resident of that kind
                # satisfies the need -- which is wrong for needs like
                # "interactive_lane": the small production model is an `llm`,
                # but it is not an interactive lane.
                "role": str(need.get("role") or ""),
            }

        self.loadouts: list[dict[str, Any]] = []
        seen_loadouts: set[str] = set()
        for index, loadout in enumerate(raw.get("loadouts") or []):
            where = f"loadouts[{index}]"
            if not isinstance(loadout, dict):
                raise LoadoutPolicyError(f"{where}: expected object")
            loadout_id = str(_require(loadout, "loadout_id", where))
            if loadout_id in seen_loadouts:
                raise LoadoutPolicyError(f"{where}: duplicate loadout {loadout_id!r}")
            seen_loadouts.add(loadout_id)
            residents = loadout.get("residents") or []
            if not isinstance(residents, list):
                raise LoadoutPolicyError(f"{where}.residents: expected list")
            parsed_residents = []
            for r_index, resident in enumerate(residents):
                r_where = f"{where}.residents[{r_index}]"
                if not isinstance(resident, dict):
                    raise LoadoutPolicyError(f"{r_where}: expected object")
                kind = str(_require(resident, "kind", r_where))
                if kind not in KNOWN_RESIDENT_KINDS:
                    raise LoadoutPolicyError(
                        f"{r_where}.kind: unknown kind {kind!r}"
                    )
                model_key = str(_require(resident, "model_key", r_where))
                if model_key not in self.models:
                    raise LoadoutPolicyError(
                        f"{r_where}.model_key: unknown model {model_key!r}"
                    )
                vram_bytes = _as_int(
                    resident.get("vram_bytes", self.models[model_key]["vram_bytes"]),
                    f"{r_where}.vram_bytes",
                )
                role = str(resident.get("role") or "")
                parsed_residents.append(
                    {
                        "kind": kind,
                        "model_key": model_key,
                        "vram_bytes": vram_bytes,
                        "port": int(resident["port"]) if resident.get("port") else None,
                        "exclusive": bool(resident.get("exclusive", False)),
                        "role": role,
                    }
                )
            self.loadouts.append(
                {
                    "loadout_id": loadout_id,
                    "description": str(loadout.get("description") or ""),
                    "residents": parsed_residents,
                    "preferred_when": [
                        str(item) for item in (loadout.get("preferred_when") or [])
                    ],
                    "provenance": str(loadout.get("provenance") or "declared"),
                }
            )
        if not self.loadouts:
            raise LoadoutPolicyError("policy.loadouts: at least one loadout required")

        self.exclusivity: dict[str, Any] = {
            str(key): value for key, value in (raw.get("exclusivity") or {}).items()
        }
        self.evidence: list[dict[str, Any]] = [
            dict(item) for item in (raw.get("evidence") or []) if isinstance(item, dict)
        ]

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def model_vram(self, model_key: str) -> int:
        return int(self.models[model_key]["vram_bytes"])


def load_circumstance(raw: dict[str, Any]) -> dict[str, bool]:
    """Normalize a circumstance into a need -> bool mapping.

    An explicit empty ``needs`` mapping is a valid circumstance ("no work
    needs this resource right now"); a missing ``needs`` key is not.
    """
    if not isinstance(raw, dict):
        raise LoadoutPolicyError("circumstance: expected JSON object")
    needs = raw.get("needs")
    if not isinstance(needs, dict):
        raise LoadoutPolicyError("circumstance.needs: expected object")
    normalized: dict[str, bool] = {}
    for name, value in needs.items():
        if name not in KNOWN_NEEDS:
            raise LoadoutPolicyError(f"circumstance.needs: unknown need {name!r}")
        normalized[name] = bool(value)
    return normalized


def evaluate_feasibility(
    policy: Policy,
    loadout: dict[str, Any],
    *,
    vram_headroom_bytes: int = 0,
    held_exclusive_ports: Iterable[int] = (),
) -> tuple[bool, list[str]]:
    """Return ``(feasible, reasons)`` for one loadout on the measured device.

    ``reasons`` explains every rejection; an empty list means feasible.
    """
    reasons: list[str] = []

    total_vram = sum(int(r["vram_bytes"]) for r in loadout["residents"])
    capacity = sum(device["vram_total_bytes"] for device in policy.devices.values())
    budget = capacity + vram_headroom_bytes
    if total_vram > budget:
        reasons.append(
            f"resident vram {total_vram} exceeds budget {budget} "
            f"(capacity {capacity} + headroom {vram_headroom_bytes})"
        )

    exclusive_residents = [
        resident
        for resident in loadout["residents"]
        if policy.models.get(resident.get("model_key", ""), {}).get("exclusive_gpu")
    ]
    if exclusive_residents and len(loadout["residents"]) > len(exclusive_residents):
        others = sorted(
            r.get("model_key")
            for r in loadout["residents"]
            if r not in exclusive_residents
        )
        reasons.append(
            "resident(s) "
            f"{[r.get('model_key') for r in exclusive_residents]} require the "
            f"device to themselves and cannot share it with {others}"
        )

    for resident in loadout["residents"]:
        model = policy.models.get(resident.get("model_key", ""), {})
        if model.get("load_status") == "failed":
            reasons.append(
                f"resident model {resident.get('model_key')} is marked failed to "
                f"load: {model.get('load_note') or 'no detail recorded'}"
            )

    held = {int(port) for port in held_exclusive_ports}
    for resident in loadout["residents"]:
        port = resident.get("port")
        if resident.get("exclusive") and port is not None and int(port) in held:
            reasons.append(
                f"resident {resident['model_key']} needs exclusive port {port} "
                "which is already held"
            )

    return (not reasons), reasons


def _satisfies(
    policy: Policy, loadout: dict[str, Any], need: str
) -> tuple[bool, list[str]]:
    spec = policy.needs.get(need)
    if spec is None:
        raise LoadoutPolicyError(f"need {need!r} is not declared in the policy")
    kind = spec["resident_kind"]
    role = spec.get("role") or ""
    matching = [
        resident for resident in loadout["residents"] if resident.get("kind") == kind
    ]
    if role:
        matching = [r for r in matching if r.get("role") == role]
    if matching:
        return True, []
    requirement = f"a resident of kind {kind}"
    if role:
        requirement += f" with role {role!r}"
    return False, [f"need {need} requires {requirement}"]


def _preference_rank(
    policy: Policy, loadout: dict[str, Any], circumstance_id: str
) -> tuple[int, str]:
    """Declared preference for a loadout under a circumstance.

    An exact match on the circumstance id wins, then a wildcard, then the
    loadout order in the policy file. Provenance is carried through so the
    operator can see whether the ranking came from measurement or judgment.
    """
    preferred = loadout.get("preferred_when") or []
    if circumstance_id in preferred:
        return (0, "declared-exact")
    if "*" in preferred:
        return (1, "declared-wildcard")
    return (2, "fallback-order")


def recommend(
    policy: Policy,
    *,
    circumstance: dict[str, bool],
    circumstance_id: str = "unspecified",
    vram_headroom_bytes: int = 0,
    held_exclusive_ports: Iterable[int] = (),
) -> dict[str, Any]:
    """Rank every loadout for a circumstance. Advisory only.

    Returns a recommendation document with all-false authority. Callers apply
    it; this function never touches the fleet.
    """
    active_needs = sorted(name for name, wanted in circumstance.items() if wanted)
    evaluated: list[dict[str, Any]] = []

    for loadout in policy.loadouts:
        feasible, reasons = evaluate_feasibility(
            policy,
            loadout,
            vram_headroom_bytes=vram_headroom_bytes,
            held_exclusive_ports=held_exclusive_ports,
        )
        for need in active_needs:
            met, need_reasons = _satisfies(policy, loadout, need)
            if not met:
                reasons = list(reasons) + need_reasons
        rank, preference_basis = _preference_rank(
            policy, loadout, circumstance_id
        )
        evaluated.append(
            {
                "loadout_id": loadout["loadout_id"],
                "description": loadout["description"],
                "provenance": loadout["provenance"],
                "feasible": not reasons,
                "reasons": reasons,
                "preference_rank": rank,
                "preference_basis": preference_basis,
                "resident_vram_bytes": sum(
                    int(r["vram_bytes"]) for r in loadout["residents"]
                ),
                "residents": [
                    {
                        "kind": r.get("kind", ""),
                        "model_key": r.get("model_key", ""),
                        "vram_bytes": int(r["vram_bytes"]),
                        "port": r.get("port"),
                        "exclusive": bool(r.get("exclusive", False)),
                    }
                    for r in loadout["residents"]
                ],
            }
        )

    feasible = [item for item in evaluated if item["feasible"]]
    feasible.sort(
        key=lambda item: (item["preference_rank"], item["resident_vram_bytes"])
    )
    best = feasible[0] if feasible else None

    # Evidence readiness is data-driven: count measured outcomes for this
    # circumstance, and require that every feasible candidate has been observed
    # before the decision model may be scored. Until then the declared
    # preference decides and the document says why.
    records = [
        record
        for record in policy.evidence
        if str(record.get("circumstance_id")) == circumstance_id
        and str(record.get("provenance")) == "measured"
    ]
    observed_loadouts = {str(record.get("loadout_id")) for record in records}
    uncovered = sorted(
        item["loadout_id"]
        for item in feasible
        if item["loadout_id"] not in observed_loadouts
    )
    model_ready = (
        len(records) >= MODEL_EVIDENCE_THRESHOLD and not uncovered and bool(feasible)
    )
    # With a single feasible option there is nothing to choose between, so no
    # amount of evidence would hand the decision to the model. Say that plainly
    # instead of leaving the gate looking like it is still waiting on data.
    decision_is_forced = len(feasible) <= 1
    if model_ready:
        model_note = (
            f"{len(records)} measured outcomes covering every feasible candidate; "
            "the decision model may be scored for this circumstance."
        )
    elif not records:
        model_note = (
            "No measured outcome exists for this circumstance; the declared "
            "preference decides."
        )
    else:
        model_note = (
            f"{len(records)} measured outcome(s) but uncovered feasible "
            f"candidates {uncovered}; the declared preference decides."
        )
    if decision_is_forced:
        model_note += (
            " Only one loadout is feasible here, so the decision is already "
            "forced by feasibility and no model would be consulted."
        )

    if best is None:
        # A null answer is only useful if it says what each option would break.
        blocking = [
            {
                "loadout_id": item["loadout_id"],
                "reasons": item["reasons"],
            }
            for item in evaluated
            if item["reasons"]
        ]
        note = (
            "No loadout can serve this circumstance on the measured device; "
            "the operator must choose which requirement to relax. Every "
            "candidate's blocking reasons are listed."
        )
        basis = "infeasible"
    else:
        blocking = []
        note = (
            "Recommendation only. Authority is all-false; the operator applies "
            "the loadout. Model scoring stays inactive until measured "
            "(circumstance, loadout) outcomes exist."
        )
        basis = "feasibility+declared-preference"

    return {
        "schema": RECOMMENDATION_SCHEMA,
        "policy_id": policy.policy_id,
        "policy_captured_at": policy.captured_at,
        "circumstance_id": circumstance_id,
        "circumstance": dict(sorted(circumstance.items())),
        "vram_headroom_bytes": vram_headroom_bytes,
        "held_exclusive_ports": sorted({int(p) for p in held_exclusive_ports}),
        "recommended_loadout_id": best["loadout_id"] if best else None,
        "decision_basis": basis,
        "blocking": blocking,
        "model_scoring_active": False,
        "model_ready": model_ready,
        "decision_is_forced": decision_is_forced,
        "model_note": model_note,
        "evaluated_evidence_records": len(records),
        "uncovered_feasible_candidates": uncovered,
        "candidates": evaluated,
        "note": note,
        "authority": dict(DEFAULT_AUTHORITY),
    }


def build_policy(
    *,
    policy_id: str,
    captured_at: str,
    devices: list[dict[str, Any]],
    models: list[dict[str, Any]],
    needs: list[dict[str, Any]],
    loadouts: list[dict[str, Any]],
    exclusivity: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble a policy document with the canonical schema."""
    return {
        "schema": POLICY_SCHEMA,
        "policy_id": policy_id,
        "captured_at": captured_at,
        "devices": devices,
        "models": models,
        "needs": needs,
        "loadouts": loadouts,
        "exclusivity": dict(exclusivity or {}),
        "evidence": list(evidence or []),
        "note": (
            "Devices and model VRAM figures are measured facts. Loadout "
            "preferences are declared by an operator until measured outcomes "
            "for (circumstance, loadout) pairs exist."
        ),
    }
