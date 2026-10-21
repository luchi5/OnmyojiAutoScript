"""Read-only by default; --execute only selects in an already-open draft."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tasks.DraftDuel.local_draft import AdbIO, DraftVision, LocalDraft, TimedOCR
from tasks.DraftDuel.strategy import rank_draft_choices
from tasks.DraftDuel.ratings import rating_policy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not 1 <= args.seconds <= 180:
        parser.error("seconds must be 1..180")
    records = []
    if args.replay:
        if args.execute:
            parser.error("Replay can never execute")
        import cv2
        ocr = TimedOCR()
        try:
            vision = DraftVision(ocr)
            for filename in ("01-start.png", "09-start.png", "01-draft2.png",
                             "09-draft2.png", "01-manual-draft.png", "09-manual-draft.png"):
                image = cv2.imread(str(args.replay / filename))
                start = time.monotonic()
                frame = vision.read(image, filename[:2], start)
                records.append({"file": filename, "seconds": time.monotonic()-start,
                                "round": frame.round_no if frame else None,
                                "names": [c.name for c in frame.candidates] if frame else [],
                                "actionable": frame is not None})
        finally:
            ocr.close()
    else:
        adb = ROOT / "toolkit/Lib/site-packages/adbutils/binaries/adb.exe"
        runner = LocalDraft({a: AdbIO(adb, s) for a, s in
                             (("01", "127.0.0.1:16384"), ("09", "127.0.0.1:16640"))})
        try:
            print("选将工具已启动：仅处理1号、9号的选将页面，不点击开始或上阵。", flush=True)
            end = time.monotonic()+args.seconds
            while time.monotonic() < end:
                start = time.monotonic()
                try:
                    record = runner.step(execute=args.execute)
                except Exception as exc:
                    record = {"state": "error_no_retry_click", "error": str(exc)}
                record["seconds"] = time.monotonic()-start
                records.append(record)
                args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
                if record.get("actions") or record.get("error"):
                    print(json.dumps(record, ensure_ascii=False), flush=True)
                time.sleep(.2)
        finally:
            runner.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
