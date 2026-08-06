"""Runtime-safe repository path filtering shared by scanner compatibility code."""

from __future__ import annotations

from pathlib import Path


def install_repository_path_policy() -> None:
    from . import repo_task_generator as generator

    def allowed_file(path: Path) -> bool:
        if any(part in generator.EXCLUDE_DIRS for part in path.parts):
            return False
        supported = (
            path.name in generator.INCLUDE_NAMES
            or path.suffix.lower() in generator.INCLUDE_SUFFIXES
        )
        if not supported:
            return False
        # Relative Git paths are validated structurally here; the resolved
        # absolute candidate is validated for existence and size separately.
        if not path.is_absolute():
            return True
        try:
            return path.is_file() and path.stat().st_size <= generator.MAX_FILE_BYTES
        except OSError:
            return False

    generator._allowed_file = allowed_file
