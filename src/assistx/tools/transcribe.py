
import argparse, os, json, uuid, time, pathlib
from typing import List, Dict, Any
from faster_whisper import WhisperModel

AUDIO_EXTS = {'.wav', '.mp3', '.m4a', '.flac', '.ogg', '.opus'}

def find_audio(root: str) -> List[pathlib.Path]:
    p = pathlib.Path(root)
    return [q for q in p.rglob("*") if q.suffix.lower() in AUDIO_EXTS]

def transcribe_file(model: WhisperModel, path: pathlib.Path) -> Dict[str, Any]:
    segments, info = model.transcribe(str(path), beam_size=1)
    segs = []
    for i, seg in enumerate(segments):
        segs.append({
            "id": f"{path.stem}_{i}",
            "idx": i,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "tokens_count": None
        })
    txt = "\n".join(s["text"] for s in segs)
    return {
        "id": uuid.uuid4().hex,
        "key": path.stem,
        "text": txt,
        "source_json": None,
        "source_rttm": None,
        "segments": segs
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-root", required=True)
    ap.add_argument("--out-root", required=True, help="Where to write transcription JSON and TXT")
    ap.add_argument("--model", default="tiny", choices=["tiny","base"])
    args = ap.parse_args()

    os.makedirs(args.out_root, exist_ok=True)
    model = WhisperModel(args.model, device="auto", compute_type="int8")
    files = find_audio(args.audio_root)
    if not files:
        print("No audio found."); return
    print(f"Found {len(files)} audio files")

    for f in files:
        t0 = time.time()
        obj = transcribe_file(model, f)
        obj["source_json"] = str((pathlib.Path(args.out_root) / f"{f.stem}_transcription.json").resolve())
        # write json + txt
        (pathlib.Path(args.out_root) / f"{f.stem}_transcription.json").write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        (pathlib.Path(args.out_root) / f"{f.stem}_transcription.txt").write_text(obj["text"], encoding="utf-8")
        print(f"{f.name}: {len(obj['segments'])} segments in {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()
