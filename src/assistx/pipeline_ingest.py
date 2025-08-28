
from __future__ import annotations
import json, uuid
from pathlib import Path
from typing import Dict, Any
from .neo4j_client import Neo4jClient

SUPPORTED = {".txt", ".jsonl"}

def load_transcript(path: Path) -> Dict[str, Any]:
    if path.suffix == ".txt":
        text = path.read_text(encoding="utf-8")
        return {
            "title": path.stem,
            "source": str(path),
            "utterances": [{"id": str(uuid.uuid4()), "speaker": None, "text": text}],
        }
    elif path.suffix == ".jsonl":
        utters = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                j = json.loads(line)
                utters.append({
                    "id": j.get("id", str(uuid.uuid4())),
                    "speaker": j.get("speaker"),
                    "text": j.get("text", ""),
                    "started_at": j.get("start"),
                    "ended_at": j.get("end"),
                })
        return {"title": path.stem, "source": str(path), "utterances": utters}
    else:
        raise ValueError(f"Unsupported file: {path}")

def ingest_dir(src: str, neo: Neo4jClient):
    p = Path(src)
    files = [f for f in p.glob("**/*") if f.suffix in SUPPORTED]
    for f in sorted(files):
        tr = load_transcript(f)
        cid = neo.upsert_conversation(tr["title"], tr["source"])
        neo.add_utterances(cid, tr["utterances"])
        print(f"Ingested {f} as Conversation {cid} with {len(tr['utterances'])} utterances")
