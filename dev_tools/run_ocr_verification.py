"""Run offline OCR verification with a guard limited to this child process tree."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import psutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--isolated-python", type=Path)
    args = parser.parse_args()
    available = psutil.virtual_memory().available
    if available < 1600*1024**2:
        raise SystemExit("Not started: at least 1600 MiB available RAM is required")
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output/"integration-result.json").exists():
        raise SystemExit("Choose a new output directory; previous results will not be overwritten")
    command = [str(args.python), "-B", str(Path(__file__).with_name("verify_rapidocr.py")),
               "--corpus", str(args.corpus), "--output", str(args.output)]
    if args.isolated_python:
        command += ["--isolated-python", str(args.isolated_python), "--skip-tests"]
    record = {"command": command, "available_at_start": available, "peak_child_rss": 0,
              "minimum_host_available": available, "terminated_by_guard": False}
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with (args.output/"verification.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env,
                                 creationflags=subprocess.CREATE_NO_WINDOW | subprocess.IDLE_PRIORITY_CLASS)
        record["pid"] = child.pid
        process = psutil.Process(child.pid)
        while child.poll() is None:
            try:
                rss = sum(p.memory_info().rss for p in [process]+process.children(recursive=True) if p.is_running())
                available = psutil.virtual_memory().available
                record["peak_child_rss"] = max(record["peak_child_rss"], rss)
                record["minimum_host_available"] = min(record["minimum_host_available"], available)
                if rss > 800*1024**2 or available < 800*1024**2:
                    record["terminated_by_guard"] = True
                    for p in process.children(recursive=True):
                        p.terminate()
                    child.terminate()
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(.25)
        record["exit_code"] = child.wait()
    (args.output/"resource-check.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record), flush=True)
    raise SystemExit(record["exit_code"])


if __name__ == "__main__":
    main()
