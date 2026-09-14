from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one replacement target, found {count}")
    file.write_text(text.replace(old, new, 1))


mobile = Path("src/assistx/mobile_agent_routes.py")
text = mobile.read_text()
helper_marker = "\ndef register_mobile_agent_routes(router: APIRouter, auth_dependency: Callable[..., str]) -> None:\n"
if "def _sanitize_runtime_projection_for_mobile(" not in text:
    helper = r'''

def _sanitize_runtime_projection_for_mobile(projection: dict[str, Any]) -> dict[str, Any]:
    """Return the minimum fleet metadata useful to an authenticated phone.

    The authoritative runtime projection contains internal routing coordinates,
    runtime/model identifiers, artifact fingerprints, and network access paths.
    None of those belong on the mobile boundary. The phone receives only opaque
    runtime identities, aggregate counts, runtime kind, and coarse capability
    flags needed to render Models & Agents truthfully.
    """

    runtimes: list[dict[str, Any]] = []
    model_total = 0
    agent_total = 0
    code_total = 0

    for provider in projection.get("providers") or []:
        if not isinstance(provider, dict) or provider.get("enabled") is False:
            continue
        models = [item for item in (provider.get("models") or []) if isinstance(item, dict)]
        if not models:
            continue

        node_id = str(provider.get("node_id") or "")
        runtime_instance_id = str(provider.get("runtime_instance_id") or "")
        runtime_name = str(provider.get("name") or "")
        opaque_seed = "|".join((node_id, runtime_instance_id, runtime_name))
        opaque_id = hashlib.sha256(opaque_seed.encode("utf-8")).hexdigest()[:20]

        agent_capable = bool(provider.get("allow_agent_runtime")) or any(
            bool(model.get("allow_agent_runtime")) for model in models
        )
        code_capable = bool(provider.get("allow_code_execution")) or any(
            bool(model.get("allow_code_execution")) for model in models
        )
        capabilities = sorted(
            {
                str(capability)
                for model in models
                for capability in (model.get("capabilities") or [])
                if str(capability).strip()
            }
        )
        runtime_kind = str(
            provider.get("runtime_kind") or provider.get("type") or "runtime"
        ).strip() or "runtime"

        runtimes.append(
            {
                "runtime_id": f"runtime:{opaque_id}",
                "kind": runtime_kind,
                "model_count": len(models),
                "agent_capable": agent_capable,
                "code_execution_capable": code_capable,
                "capabilities": capabilities,
            }
        )
        model_total += len(models)
        agent_total += int(agent_capable)
        code_total += int(code_capable)

    runtimes.sort(key=lambda item: (item["kind"], item["runtime_id"]))
    runtime_total = len(runtimes)
    return {
        "schema_version": "1",
        "source": "assistx-runtime-projection",
        "generated_at_ms": projection.get("generated_at_ms"),
        "expires_at_ms": projection.get("expires_at_ms"),
        "fleet_runtime_count": runtime_total,
        "fleet_model_count": model_total,
        "agent_runtime_count": agent_total,
        "code_runtime_count": code_total,
        "agent_auto_available": runtime_total > 0,
        "runtimes": runtimes,
    }


def _mobile_runtime_catalog() -> dict[str, Any]:
    from .api import _neo
    from .runtime_projection_v2 import build_runtime_projection_v2

    try:
        ttl_seconds = int(
            os.getenv("ASSISTX_RUNTIME_PROJECTION_TTL_SECONDS", "900")
        )
    except ValueError:
        ttl_seconds = 900
    projection = build_runtime_projection_v2(
        _neo,
        ttl_seconds=max(30, min(ttl_seconds, 3600)),
    )
    return _sanitize_runtime_projection_for_mobile(projection)
'''
    if helper_marker not in text:
        raise SystemExit("mobile_agent_routes.py: registration marker missing")
    text = text.replace(helper_marker, helper + helper_marker, 1)
    mobile.write_text(text)

text = mobile.read_text()
route_marker = '''    @router.post("/api/v1/agent/chat/completions", tags=["kipnerter-mobile"])
'''
if '@router.get("/api/v1/runtime/catalog"' not in text:
    route = r'''    @router.get("/api/v1/runtime/catalog", tags=["kipnerter-mobile"])
    def mobile_runtime_catalog(
        request: Request,
        user: str = Depends(mobile_auth),
    ) -> dict[str, Any]:
        # Re-evaluate Tailnet identity so an allowlist change cannot be bypassed
        # after dependency resolution. Legacy Basic auth remains an operator-only
        # fallback exactly as it is for whoami/chat.
        _tailnet_identity(request)
        try:
            return _mobile_runtime_catalog()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "runtime_catalog_unavailable"},
            ) from exc

'''
    if route_marker not in text:
        raise SystemExit("mobile_agent_routes.py: chat route marker missing")
    mobile.write_text(text.replace(route_marker, route + route_marker, 1))

# Add the catalog path to the deliberately route-scoped Tailscale Serve surface.
configure = Path("scripts/configure-kipnerter-tailnet-serve.sh")
text = configure.read_text()
if '/api/v1/runtime/catalog' not in text:
    text = text.replace(
        'check_path_conflict "/api/v1/auth/whoami" "http://127.0.0.1:${API_PORT}/api/v1/auth/whoami"\n',
        'check_path_conflict "/api/v1/auth/whoami" "http://127.0.0.1:${API_PORT}/api/v1/auth/whoami"\n'
        'check_path_conflict "/api/v1/runtime/catalog" "http://127.0.0.1:${API_PORT}/api/v1/runtime/catalog"\n',
        1,
    )
    text = text.replace(
        '# Add only the three route-scoped mobile mounts.',
        '# Add only the four route-scoped mobile mounts.',
        1,
    )
    text = text.replace(
        'sudo tailscale serve --https="${SERVE_PORT}" --set-path=/api/v1/auth/whoami --bg \\\n  "http://127.0.0.1:${API_PORT}/api/v1/auth/whoami"\n',
        'sudo tailscale serve --https="${SERVE_PORT}" --set-path=/api/v1/auth/whoami --bg \\\n  "http://127.0.0.1:${API_PORT}/api/v1/auth/whoami"\n'
        'sudo tailscale serve --https="${SERVE_PORT}" --set-path=/api/v1/runtime/catalog --bg \\\n  "http://127.0.0.1:${API_PORT}/api/v1/runtime/catalog"\n',
        1,
    )
    text = text.replace(
        '  /api/v1/auth/whoami\n  /api/v1/agent/chat/completions\n',
        '  /api/v1/auth/whoami\n  /api/v1/runtime/catalog\n  /api/v1/agent/chat/completions\n',
        1,
    )
    configure.write_text(text)

verify = Path("scripts/verify-kipnerter-tailnet-gateway.sh")
text = verify.read_text()
if 'KIPNERTER_GATEWAY_CATALOG_PROBE' not in text:
    text = text.replace(
        'IDENTITY_PROBE="${KIPNERTER_GATEWAY_IDENTITY_PROBE:-1}"\nAGENT_SMOKE=',
        'IDENTITY_PROBE="${KIPNERTER_GATEWAY_IDENTITY_PROBE:-1}"\nCATALOG_PROBE="${KIPNERTER_GATEWAY_CATALOG_PROBE:-1}"\nAGENT_SMOKE=',
        1,
    )
    text = text.replace(
        '  /api/v1/auth/whoami \\\n  /api/v1/agent/chat/completions; do',
        '  /api/v1/auth/whoami \\\n  /api/v1/runtime/catalog \\\n  /api/v1/agent/chat/completions; do',
        1,
    )
    text = text.replace(
        "trap 'rm -f \"${whoami_file:-}\" \"${agent_headers:-}\" \"${agent_body:-}\"' EXIT",
        "trap 'rm -f \"${whoami_file:-}\" \"${catalog_file:-}\" \"${agent_headers:-}\" \"${agent_body:-}\"' EXIT",
        1,
    )
    catalog_probe = r'''
if [[ "$CATALOG_PROBE" == "1" ]]; then
  catalog_file="$(mktemp)"
  catalog_status="$(curl --silent --show-error --max-time 10 \
    --output "$catalog_file" --write-out '%{http_code}' \
    "${gateway_url}/api/v1/runtime/catalog")"
  [[ "$catalog_status" == "200" ]] || {
    cat "$catalog_file" >&2 || true
    fail "runtime catalog returned HTTP ${catalog_status}"
  }

  python3 - "$catalog_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
if value.get("schema_version") != "1":
    raise SystemExit(f"unexpected runtime catalog schema: {value}")
if value.get("source") != "assistx-runtime-projection":
    raise SystemExit(f"unexpected runtime catalog source: {value}")
for key in ("fleet_runtime_count", "fleet_model_count", "agent_runtime_count", "code_runtime_count"):
    item = value.get(key)
    if not isinstance(item, int) or item < 0:
        raise SystemExit(f"invalid {key}: {item!r}")
if not isinstance(value.get("runtimes"), list):
    raise SystemExit("runtime catalog runtimes must be a list")
serialized = json.dumps(value, sort_keys=True).lower()
for forbidden in ("base_url", "access_urls", "node_id", "runtime_instance_id", "artifact_fingerprint", "provider_model", "http://", "https://"):
    if forbidden in serialized:
        raise SystemExit(f"mobile runtime catalog leaked forbidden detail: {forbidden}")
print(f"fleet_runtime_count={value['fleet_runtime_count']}")
print(f"fleet_model_count={value['fleet_model_count']}")
PY
fi
'''
    marker = '\nif [[ "$AGENT_SMOKE" == "1" ]]; then\n'
    if marker not in text:
        raise SystemExit("verify script: agent smoke marker missing")
    text = text.replace(marker, catalog_probe + marker, 1)
    text = text.replace(
        'identity_probe=${IDENTITY_PROBE}\nagent_smoke=${AGENT_SMOKE}',
        'identity_probe=${IDENTITY_PROBE}\ncatalog_probe=${CATALOG_PROBE}\nagent_smoke=${AGENT_SMOKE}',
        1,
    )
    verify.write_text(text)

# Focused regression tests for sanitization and route behavior.
test_path = Path("tests/test_mobile_runtime_catalog.py")
test_path.write_text(r'''from __future__ import annotations

import json

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from assistx import mobile_agent_routes as mobile


def _projection() -> dict:
    return {
        "generated_at_ms": 100,
        "expires_at_ms": 200,
        "providers": [
            {
                "name": "assistx-secret-node-runtime",
                "node_id": "x1-370",
                "runtime_instance_id": "lmstudio:1234",
                "runtime_kind": "lmstudio",
                "enabled": True,
                "base_url": "http://100.64.0.1:1234/v1",
                "access_urls": ["http://x1-370:1234/v1"],
                "allow_agent_runtime": True,
                "allow_code_execution": False,
                "models": [
                    {
                        "alias": "qwen",
                        "provider_model": "secret/model-name",
                        "model_instance_id": "model-secret-id",
                        "artifact_fingerprint": "abc123",
                        "capabilities": ["chat", "streaming", "local_only"],
                    },
                    {
                        "alias": "coder",
                        "allow_code_execution": True,
                        "capabilities": ["chat", "tools"],
                    },
                ],
            },
            {
                "name": "ignored-empty-runtime",
                "node_id": "empty-node",
                "runtime_instance_id": "empty",
                "runtime_kind": "vllm",
                "models": [],
            },
        ],
    }


def test_mobile_catalog_redacts_internal_runtime_coordinates():
    catalog = mobile._sanitize_runtime_projection_for_mobile(_projection())

    assert catalog["schema_version"] == "1"
    assert catalog["source"] == "assistx-runtime-projection"
    assert catalog["fleet_runtime_count"] == 1
    assert catalog["fleet_model_count"] == 2
    assert catalog["agent_runtime_count"] == 1
    assert catalog["code_runtime_count"] == 1
    assert catalog["agent_auto_available"] is True
    assert catalog["runtimes"][0]["kind"] == "lmstudio"
    assert catalog["runtimes"][0]["agent_capable"] is True
    assert catalog["runtimes"][0]["code_execution_capable"] is True
    assert catalog["runtimes"][0]["capabilities"] == ["chat", "local_only", "streaming", "tools"]
    assert catalog["runtimes"][0]["runtime_id"].startswith("runtime:")

    serialized = json.dumps(catalog, sort_keys=True)
    for forbidden in (
        "x1-370",
        "lmstudio:1234",
        "100.64.0.1",
        "secret/model-name",
        "model-secret-id",
        "artifact_fingerprint",
        "base_url",
        "access_urls",
        "node_id",
        "runtime_instance_id",
    ):
        assert forbidden not in serialized


def _app() -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    def auth(request: Request, credentials=None) -> str:
        login = request.headers.get("Tailscale-User-Login")
        if not login:
            raise AssertionError("test expected Tailnet identity")
        return login

    mobile.register_mobile_agent_routes(router, auth)
    app.include_router(router)
    return app


def test_mobile_runtime_catalog_route_uses_tailnet_boundary(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setattr(
        mobile,
        "_mobile_runtime_catalog",
        lambda: mobile._sanitize_runtime_projection_for_mobile(_projection()),
    )

    response = TestClient(_app()).get(
        "/api/v1/runtime/catalog",
        headers={"Tailscale-User-Login": "scott@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["fleet_runtime_count"] == 1


def test_mobile_runtime_catalog_route_sanitizes_backend_failure(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")

    def fail():
        raise RuntimeError("bolt://neo4j:7687 super-secret-detail")

    monkeypatch.setattr(mobile, "_mobile_runtime_catalog", fail)
    response = TestClient(_app()).get(
        "/api/v1/runtime/catalog",
        headers={"Tailscale-User-Login": "scott@example.com"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": {"error": "runtime_catalog_unavailable"}}
    assert "neo4j" not in response.text.lower()
''')

# Document the new route without exposing the internal projection contract.
docs = Path("docs/KIPNERTER_MOBILE_AGENT_GATEWAY.md")
if docs.exists():
    doc_text = docs.read_text()
    if "## Sanitized runtime catalog" not in doc_text:
        doc_text += r'''

## Sanitized runtime catalog

Authenticated Kipnerter clients may read `GET /api/v1/runtime/catalog` through the
same route-scoped Tailscale Serve boundary as `whoami` and agent chat. The route
is derived from AssistX's approved runtime projection but deliberately returns
only opaque runtime identifiers, aggregate runtime/model counts, runtime kind,
and coarse capability flags. It must never return node names, raw runtime/model
identifiers, access URLs, ports, artifact fingerprints, or executor/router
credentials.

Catalog availability is observational. A catalog failure must not cause the iOS
client to invent direct fleet topology or weaken Agent Auto authentication; the
phone may continue using its separately bounded direct-LM-Studio fallback.
'''
        docs.write_text(doc_text)
