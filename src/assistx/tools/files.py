
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any
import hashlib

def write_text(path: str, text: str) -> Dict[str, Any]:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {"path": str(p), "sha256": h}
