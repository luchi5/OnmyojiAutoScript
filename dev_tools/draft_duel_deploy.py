"""Prepare roster plan or listen for deployment. Never clicks matchmaking."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tasks.DraftDuel.local_draft import TimedOCR
from tasks.DraftDuel.lineup_runtime import read_roster, compute_lineup, RoundDeploymentVision
from tasks.DraftDuel.deployment import DeploymentAdbIO, DeploymentController


def prepare(source, output):
    output.mkdir(parents=True, exist_ok=True)
    ocr = TimedOCR()
    candidates = []
    portrait_files = {}
    for owner in ("01", "09"):
        detail = cv2.imread(str(source / (owner+"-tool-result.png")))
        lobby = cv2.imread(str(source / (owner+"-tool-selection.png")))
        if detail is None or lobby is None:
            raise ValueError("Missing observed roster screenshots")
        roster, portraits = read_roster(detail, lobby, owner, ocr)
        candidates += roster
        portrait_files[owner] = {}
        for index, (name, image) in enumerate(portraits.items()):
            filename = owner+"-"+str(index)+".png"
            cv2.imwrite(str(output / filename), image)
            portrait_files[owner][name] = filename
    lineups, evaluation = compute_lineup(candidates)
    plan = dict(lineups=lineups, evaluation=evaluation, portraits=portrait_files,
                observed=[dict(owner=c.owner, name=c.name, stats=asdict(c.stats)) for c in candidates])
    (output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(lineups, ensure_ascii=False), flush=True)


def worker(owner, port, folder, plan, seconds, execute):
    io = DeploymentAdbIO(ROOT / "toolkit/Lib/site-packages/adbutils/binaries/adb.exe",
                        "127.0.0.1:"+str(port))
    portraits = {n: cv2.imread(str(folder / p)) for n, p in plan["portraits"][owner].items()}
    vision = RoundDeploymentVision(TimedOCR(), portraits)
    controller = DeploymentController(io, vision, plan["lineups"][owner])
    records, previous = [], None
    deadline = time.monotonic()+seconds
    print(owner+" 上阵监听已就绪", flush=True)
    while time.monotonic() < deadline:
        try:
            status = controller.step(execute=execute)
        except Exception as exc:
            status = "error_no_repeat:"+str(exc)
        if controller.confirmed:
            vision.completed_stage = max(s for _, s in controller.confirmed)
        if status != previous:
            item = dict(time=time.time(), status=status, confirmed=sorted(controller.confirmed))
            frame = controller.last_observation
            if frame:
                item["observation"] = dict(side=frame.side, stage=frame.stage,
                                           current=frame.current, seconds=frame.seconds,
                                           wanted=controller.lineup[frame.stage-1],
                                           cards=frame.cards, target=frame.target)
            if status not in ("unknown_or_waiting", "confirmation_already_sent") and hasattr(io, "last_image"):
                filename = "%s-event-%d.png" % (owner, len(records))
                cv2.imwrite(str(folder / filename), io.last_image)
                item["screenshot"] = filename
            records.append(item)
            (folder / (owner+"-execution.json")).write_text(
                json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            print(owner+" "+status, flush=True)
            previous = status
        if len(controller.confirmed) == 3:
            print(owner+" 三次确认已发送；最终上阵仍需核对", flush=True)
            break
        time.sleep(.15)
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 300:
        parser.error("seconds must be 1..300")
    if args.prepare:
        if args.execute:
            parser.error("Preparation never controls game")
        prepare(args.prepare, args.folder)
    else:
        plan = json.loads((args.folder / "plan.json").read_text(encoding="utf-8"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, owner, port, args.folder, plan, args.seconds, args.execute)
                       for owner, port in (("01", 16384), ("09", 16640))]
            for future in futures:
                future.result()
