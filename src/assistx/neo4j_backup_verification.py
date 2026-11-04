from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BackupArtifact:
    path: str
    database: str
    database_id: str
    timestamp_ms: int
    full: bool
    lowest_tx: int
    highest_tx: int
    recovered: bool | None = None


class BackupVerificationError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _timestamp_ms(value: Any) -> int:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return 0
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _strict_backup_bool(value: Any, *, nullable: bool = False) -> bool | None:
    """Parse a Neo4j backup metadata boolean without Python truthiness.

    In particular, bool("false") is True, so never coerce string flags
    through bool() when they authorize a full backup or recovered chain.
    """
    if value is None and nullable:
        return None
    if type(value) is bool:
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise BackupVerificationError("untrusted backup metadata boolean")


class Neo4jBackupVerifier:
    """Inspect and validate Neo4j Enterprise backup artifacts.

    Current Neo4j releases use ``neo4j-admin backup inspect``. Older 5.x
    deployments expose inspection through ``neo4j-admin database backup
    --inspect-path``. The verifier tries both without mutating the backup set.
    """

    def __init__(
        self,
        runner: Callable[..., Any] = subprocess.run,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.runner = runner
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def inspect(self, path: str, database: str) -> list[BackupArtifact]:
        commands = [
            [
                "neo4j-admin",
                "backup",
                "inspect",
                "--format=JSON",
                "--latest-chain",
                f"--database={database}",
                path,
            ],
            [
                "neo4j-admin",
                "database",
                "backup",
                f"--inspect-path={path}",
            ],
        ]
        errors: list[str] = []
        for command in commands:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                errors.append(str(getattr(result, "stderr", ""))[-500:])
                continue
            artifacts = self._parse_output(str(result.stdout), database)
            if artifacts:
                return artifacts
            errors.append("inspection returned no parseable artifacts")
        raise BackupVerificationError("; ".join(errors)[:1000])

    def verify(
        self,
        path: str,
        database: str,
        *,
        max_age_seconds: int = 3600,
        require_recovered: bool = False,
    ) -> dict[str, Any]:
        artifacts = self.inspect(path, database)
        artifacts = sorted(artifacts, key=lambda item: item.timestamp_ms)
        if not artifacts[0].full:
            raise BackupVerificationError("backup chain does not begin with a full backup")
        database_ids = {item.database_id for item in artifacts if item.database_id}
        if len(database_ids) > 1:
            raise BackupVerificationError("backup chain contains multiple database IDs")
        previous_high = artifacts[0].highest_tx
        for artifact in artifacts[1:]:
            # Neo4j 2026.02 permits the first differential to overlap the full
            # backup. Later differential links must remain contiguous.
            if artifact.lowest_tx > previous_high + 1:
                raise BackupVerificationError("backup transaction chain has a gap")
            if artifact.highest_tx < previous_high:
                raise BackupVerificationError("backup transaction IDs moved backwards")
            previous_high = max(previous_high, artifact.highest_tx)
        latest = artifacts[-1]
        age_ms = self.clock_ms() - latest.timestamp_ms
        if latest.timestamp_ms <= 0 or age_ms < 0:
            raise BackupVerificationError("latest backup timestamp is invalid")
        if age_ms > max(60, int(max_age_seconds)) * 1000:
            raise BackupVerificationError("latest backup is older than the allowed RPO")
        if require_recovered and latest.recovered is not True:
            raise BackupVerificationError("latest backup is not recovered/aggregated")
        return {
            "ok": True,
            "database": database,
            "database_id": latest.database_id,
            "artifact_count": len(artifacts),
            "latest_path": latest.path,
            "latest_timestamp_ms": latest.timestamp_ms,
            "latest_age_seconds": round(age_ms / 1000, 3),
            "highest_tx": latest.highest_tx,
            "full_chain": artifacts[0].full,
            "recovered": latest.recovered,
            "consistency_command": self.consistency_command(path, database),
            "restore_command": self.restore_command(path, database),
        }

    @staticmethod
    def consistency_command(path: str, database: str) -> list[str]:
        return [
            "neo4j-admin",
            "database",
            "check",
            f"--from-path={path}",
            database,
        ]

    @staticmethod
    def restore_command(path: str, database: str) -> list[str]:
        return [
            "neo4j-admin",
            "database",
            "restore",
            f"--from-path={path}",
            "--overwrite-destination=true",
            database,
        ]

    @staticmethod
    def aggregate_commands(path: str, database: str) -> list[list[str]]:
        return [
            [
                "neo4j-admin",
                "backup",
                "aggregate",
                f"--from-path={path}",
                database,
            ],
            [
                "neo4j-admin",
                "database",
                "aggregate-backup",
                f"--from-path={path}",
                database,
            ],
        ]

    def run_consistency_check(self, path: str, database: str) -> dict[str, Any]:
        command = self.consistency_command(path, database)
        result = self.runner(
            command,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if result.returncode != 0:
            raise BackupVerificationError(
                "Neo4j consistency check failed: "
                + str(getattr(result, "stderr", ""))[-1000:]
            )
        return {
            "ok": True,
            "command": command,
            "output_tail": str(result.stdout)[-1000:],
        }

    @classmethod
    def _parse_output(cls, output: str, database: str) -> list[BackupArtifact]:
        text = output.strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return cls._parse_table(text, database)
        rows: Sequence[Any]
        if isinstance(decoded, list):
            rows = decoded
        elif isinstance(decoded, dict):
            rows = (
                decoded.get("backups")
                or decoded.get("artifacts")
                or decoded.get("items")
                or [decoded]
            )
        else:
            return []
        artifacts = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("database") or row.get("databaseName") or "")
            if name and name != database:
                continue
            artifacts.append(
                BackupArtifact(
                    path=str(row.get("path") or row.get("file") or ""),
                    database=name or database,
                    database_id=str(
                        row.get("databaseId")
                        or row.get("database_id")
                        or ""
                    ),
                    timestamp_ms=_timestamp_ms(
                        row.get("time")
                        or row.get("timestamp")
                        or row.get("backupTime")
                    ),
                    full=_strict_backup_bool(row.get("full")),
                    lowest_tx=int(
                        row.get("lowestTransactionId")
                        or row.get("lowest_tx")
                        or 0
                    ),
                    highest_tx=int(
                        row.get("highestTransactionId")
                        or row.get("highest_tx")
                        or 0
                    ),
                    recovered=_strict_backup_bool(
                        row.get("recovered"), nullable=True
                    ),
                )
            )
        return artifacts

    @staticmethod
    def _parse_table(output: str, database: str) -> list[BackupArtifact]:
        """Parse the fixed native 5.x 8-column table, never filename tokens.

        FILE | DATABASE | DATABASE ID | TIME (UTC) | FULL | COMPRESSED |
        LOWEST TX | HIGHEST TX. A compressed differential is NOT a full.
        """
        artifacts: list[BackupArtifact] = []
        for raw in output.splitlines():
            if "|" not in raw:
                continue
            columns = [
                value.strip() for value in raw.strip().strip("|").split("|")
            ]
            if len(columns) < 2 or columns[1] != database:
                continue
            # A malformed row for this requested database is a failed
            # attestation, not an ignorable row that silently shrinks a chain.
            if len(columns) != 8:
                raise BackupVerificationError("backup table column count mismatch")
            timestamp = _timestamp_ms(columns[3])
            if timestamp <= 0:
                raise BackupVerificationError("invalid backup table timestamp")
            if not re.fullmatch(
                r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
                columns[2],
            ):
                raise BackupVerificationError("invalid backup database identity")
            full = _strict_backup_bool(columns[4])
            _strict_backup_bool(columns[5])  # validate, not a FULL substitute
            try:
                lowest = int(columns[6])
                highest = int(columns[7])
            except ValueError as exc:
                raise BackupVerificationError("invalid backup transaction IDs") from exc
            if lowest < 0 or highest < lowest:
                raise BackupVerificationError("inverted backup transaction range")
            artifacts.append(
                BackupArtifact(
                    path=columns[0],
                    database=columns[1],
                    database_id=columns[2],
                    timestamp_ms=timestamp,
                    full=full,
                    lowest_tx=lowest,
                    highest_tx=highest,
                )
            )
        return artifacts
