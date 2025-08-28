
from __future__ import annotations
from typing import Tuple, Dict, Any

DEFAULT_POLICY = {
    "python": {"allowed": True},  # flip to False if you want stricter default
    "web_search": {"allowed": True},
    "write_text": {"allowed": True},
}

def tool_allowed(name: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
    rule = DEFAULT_POLICY.get(name, {"allowed": False})
    if not rule.get("allowed", False):
        return False, f"tool {name} disabled by policy"
    return True, ""
