"""Optional bounded public-search smoke test. Never analyzes discovered videos."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import DEFAULTS, load_json, stop_process, worker_env


def check(platform):
    with tempfile.TemporaryDirectory(prefix="video-workbench-browser-test-") as temporary:
        root = Path(temporary)
        env = worker_env()
        env["VIDEO_WORKBENCH_HOME"] = str(root / "app")
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "scroller.py"), "--platform", platform],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", env=env,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                   start_new_session=os.name != "nt")
        events = queue.Queue()
        def reader():
            for line in process.stdout:
                try:
                    events.put(json.loads(line))
                except ValueError:
                    pass
        threading.Thread(target=reader, daemon=True).start()
        states = []
        results = 0
        query = "Blender official tutorial"
        deadline = time.monotonic() + 45
        started = False
        try:
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    event = events.get(timeout=.3)
                except queue.Empty:
                    continue
                if event["event"] == "status":
                    states.append(event["status"])
                if event.get("status") == "Browser ready" and not started:
                    settings = {**DEFAULTS, "output_dir": str(root / "library"), "scroll_steps": 2,
                                "scroll_interval": 2, "scroll_minutes": 1, "scroll_max_results": 10}
                    process.stdin.write(json.dumps({"action": "start", "prompts": [query], "settings": settings}) + "\n")
                    process.stdin.flush()
                    started = True
                if event["event"] == "results":
                    results = event["count"]
                if event.get("attention") or event.get("status") in {"Complete", "Browser error", "Result limit"}:
                    break
            if process.poll() is None:
                process.stdin.write('{"action":"stop"}\n')
                process.stdin.flush()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    stop_process(process)
            sessions = [load_json(p) for p in (root / "library" / "discoveries").glob("*.json")]
            print(json.dumps({"platform": platform, "states": states, "results": results,
                              "provenance_valid": all(r["search_prompt"] == query and r["discovered_at"] for s in sessions for r in s["results"]),
                              "packets_created": len(list((root / "library" / "packets").glob("*")))}), flush=True)
        finally:
            if process.poll() is None:
                stop_process(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


if __name__ == "__main__":
    for platform in (sys.argv[1:] or ["youtube", "tiktok"]):
        check(platform)
