"""Separate read-only transaction observer in disposable graph network.

Not a signer. Does not have broker credentials, etcd key, write grant or
capacity-release capability. Observations are NOT authenticated generation
proof; fixture labels are from the broker test coordinator.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from trace_b06_observation_gate_research import (
    PinnedGraphTarget, GraphObservation, capture_active, observe_absence_only,
    ObservationRejected,
)

TARGET = Path("/tmp/observer-target.json")
ACTIVE = Path("/tmp/observer-active.json")
QUERY = (
    "SHOW TRANSACTIONS YIELD transactionId, database, metaData "
    "RETURN transactionId, database, metaData"
)


def read_transactions(credentials: dict) -> tuple[dict, ...]:
    basic = base64.b64encode(
        (credentials["observer_user"] + ":" +
         credentials["observer_password"]).encode()).decode()
    body = json.dumps({"statements": [{"statement": QUERY,
                                      "resultDataContents": ["row"]}]},
                      separators=(",", ":")).encode()
    req = Request("http://neo4j:7474/db/neo4j/tx/commit",
                  method="POST", data=body,
                  headers={"Content-Type": "application/json",
                           "Authorization": "Basic " + basic})
    try:
        with urlopen(req, timeout=5) as response:
            doc = json.loads(response.read(65536))
    except (HTTPError, URLError, OSError, ValueError, TimeoutError):
        raise ObservationRejected("INDEPENDENT_GRAPH_READ_UNAVAILABLE") from None
    if doc.get("errors") or len(doc.get("results", [])) != 1:
        raise ObservationRejected("INDEPENDENT_GRAPH_READ_INVALID")
    result = doc["results"][0]
    if result.get("columns") != ["transactionId", "database", "metaData"]:
        raise ObservationRejected("INDEPENDENT_GRAPH_COLUMNS_INVALID")
    rows = []
    for entry in result.get("data", []):
        values = entry.get("row")
        if not isinstance(values, list) or len(values) != 3:
            raise ObservationRejected("INDEPENDENT_GRAPH_ROWS_INVALID")
        rows.append(dict(zip(result["columns"], values)))
    return tuple(rows)


def make_target(doc: dict) -> PinnedGraphTarget:
    metadata = doc["metadata"]
    return PinnedGraphTarget(doc["graph_member"], doc["graph_boot"],
                             "neo4j", metadata["assistx_reservation_id"],
                             metadata, "fixture-broker")


def snapshot(doc: dict, credentials: dict, seq: int) -> GraphObservation:
    return GraphObservation("fixture-observer", doc["graph_member"],
                            doc["graph_boot"], seq,
                            read_transactions(credentials))


def observe_active(doc: dict, credentials: dict) -> dict:
    target = make_target(doc)
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        rows = snapshot(doc, credentials, 1)
        try:
            active = capture_active(target, rows)
        except ObservationRejected:
            time.sleep(.13)
            continue
        original = next(row for row in rows.rows
                        if row["transactionId"] == active.transaction_id)
        evidence = {
            "txid": active.transaction_id,
            "observed_row": {
                "transactionId": original["transactionId"],
                "database": original["database"],
                "metaData": dict(original["metaData"]),
            },
            "observed_sequence": 1,
        }
        ACTIVE.write_text(json.dumps(evidence, sort_keys=True))
        return {
            "status": "EXACT_ACTIVE_TRANSACTION_OBSERVED",
            "txid_format": "neo4j-transaction-N",
            "signer_authorized": False,
            "release_authorized": False,
        }
    raise ObservationRejected("ORIGINAL_ACTIVE_TRANSACTION_NOT_OBSERVED")


def observe_absence(doc: dict, credentials: dict) -> dict:
    target = make_target(doc)
    evidence = json.loads(ACTIVE.read_text())
    active = capture_active(target, GraphObservation(
        "fixture-observer", doc["graph_member"], doc["graph_boot"], 1,
        (evidence["observed_row"],)))
    deadline = time.monotonic() + 18
    first = None
    seq = 2
    while time.monotonic() < deadline:
        rows = snapshot(doc, credentials, seq)
        if any(row.get("transactionId") == active.transaction_id
               for row in rows.rows):
            first = None
        else:
            if first is None:
                first = rows
            else:
                observed = observe_absence_only(active, first, rows)
                return {
                    "status": "ABSENT_OBSERVATIONS_ONLY",
                    "txid_format": "neo4j-transaction-N",
                    "sequences": list(observed.observed_sequences),
                    "signer_authorized": False,
                    "release_authorized": False,
                }
        seq += 1
        time.sleep(.15)
    raise ObservationRejected("ABSENCE_NOT_OBSERVED")


def main():
    phase = sys.argv[1]
    conf = json.loads(Path("/tmp/observer-config.json").read_text())
    doc = json.loads(TARGET.read_text())
    if phase == "active":
        result = observe_active(doc, conf)
    elif phase == "absent":
        result = observe_absence(doc, conf)
    else:
        raise ObservationRejected("UNKNOWN_OBSERVER_PHASE")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
