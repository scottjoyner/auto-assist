"""Schema version for the unified fleet event contract.

Bump this when a breaking change is made to EventEnvelope or any schema in
``contracts/schemas``. Consumed by every repo; lack of an exact match is a
contract-test failure.
"""

SCHEMA_VERSION: str = "2026-06-08.v1"
