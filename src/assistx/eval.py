
from __future__ import annotations
from pathlib import Path
import json, re
from typing import Dict, Any, List
from rouge_score import rouge_scorer

def _load_gold(gold_dir: Path) -> List[Dict[str, Any]]:
    items = []
    for j in sorted(gold_dir.glob("*.json")):
        with j.open("r", encoding="utf-8") as f:
            d = json.load(f)
        items.append(d)
    return items

def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())

def eval_summaries(pred: str, gold: str) -> Dict[str, float]:
    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
    scores = scorer.score(gold, pred)
    r = scores["rougeL"]
    return {"rougeL_f": r.fmeasure, "rougeL_p": r.precision, "rougeL_r": r.recall}

def eval_tasks(pred_tasks: List[Dict[str, Any]], gold_tasks: List[Dict[str, Any]]) -> Dict[str, float]:
    pset = {_normalize(t.get("title","")) for t in pred_tasks if t.get("title")}
    gset = {_normalize(t.get("title","")) for t in gold_tasks if t.get("title")}
    tp = len(pset & gset); fp = max(0, len(pset) - tp); fn = max(0, len(gset) - tp)
    prec = tp / (tp + fp) if (tp+fp) else 0.0; rec = tp / (tp + fn) if (tp+fn) else 0.0
    f1 = 2*prec*rec/(prec+rec) if (prec+rec) else 0.0
    return {"precision": prec, "recall": rec, "f1": f1, "tp": tp, "fp": fp, "fn": fn}

def run_eval(gold_dir: str, pred_dir: str) -> Dict[str, Any]:
    gold = _load_gold(Path(gold_dir))
    results = []; agg = {"rougeL_f":0.0,"precision":0.0,"recall":0.0,"f1":0.0}; n = 0
    for item in gold:
        pid = item["id"]; pred_path = Path(pred_dir) / f"{pid}.json"
        if not pred_path.exists(): continue
        pred = json.loads(pred_path.read_text(encoding="utf-8"))
        sm = eval_summaries(pred.get("summary",""), item.get("summary",""))
        tk = eval_tasks(pred.get("tasks",[]), item.get("tasks",[]))
        res = {"id": pid, **sm, **tk}; results.append(res)
        for k in agg: agg[k] += res.get(k, 0.0)
        n += 1
    if n: 
        for k in agg: agg[k] /= n
    return {"count": n, "aggregate": agg, "results": results}
