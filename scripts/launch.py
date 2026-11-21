"""Launch with the existing local video runtime, without a console window."""
import os
from pathlib import Path
import subprocess
import sys

runtime = Path(os.environ.get("VIDEO_WORKBENCH_RUNTIME", Path.home() / ".codex" / "video-research-runtime"))
python = runtime / "venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
if not python.is_file():
    raise SystemExit("Video runtime unavailable. See references/LOCAL_APP.md")
subprocess.Popen([str(python), str(Path(__file__).with_name("app.py"))],
                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
