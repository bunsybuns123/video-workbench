"""Create the self-bootstrapping Apple Silicon macOS application bundle."""
from pathlib import Path
import argparse
import plistlib
import shutil


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def build(output):
    app = Path(output).expanduser().resolve() / "Video Workbench.app"
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    resources = contents / "Resources"
    source = resources / "video-rabbit-holes"
    executable = contents / "MacOS" / "VideoWorkbench"
    resources.mkdir(parents=True)
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "tests", "dist", "build"),
    )
    shutil.copy2(Path(__file__).with_name("setup_macos.command"), resources / "setup_macos.command")
    (resources / "setup_macos.command").chmod(0o755)
    info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": "Video Workbench",
        "CFBundleExecutable": "VideoWorkbench",
        "CFBundleIdentifier": "com.videoworkbench.app",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Video Workbench",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "15.0",
        "NSHighResolutionCapable": True,
    }
    with (contents / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)
    launcher = r'''#!/bin/zsh
set -euo pipefail
APP_BUNDLE="$(cd "$(dirname "$0")/../.." && pwd)"
RESOURCES="$APP_BUNDLE/Contents/Resources"
SOURCE="$RESOURCES/video-rabbit-holes"
SKILL="$HOME/.agents/skills/video-rabbit-holes"
RUNTIME="$HOME/.codex/video-research-runtime"
PYTHON="$RUNTIME/venv/bin/python"
if [[ ! -x "$PYTHON" || ! -f "$RUNTIME/setup-complete" ]]; then
  /usr/bin/osascript - "$RESOURCES/setup_macos.command" "$APP_BUNDLE" <<'APPLESCRIPT'
on run argv
    set setupPath to item 1 of argv
    set appPath to item 2 of argv
    tell application "Terminal"
        activate
        do script "/bin/zsh " & quoted form of setupPath & " " & quoted form of appPath
    end tell
end run
APPLESCRIPT
  exit 0
fi
APP_CODE="$SKILL"
if [[ ! -f "$APP_CODE/scripts/app.py" ]]; then
  APP_CODE="$SOURCE"
fi
export VIDEO_WORKBENCH_BUNDLE_SOURCE="$SOURCE"
exec "$PYTHON" "$APP_CODE/scripts/app.py"
'''
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text(launcher, encoding="utf-8")
    executable.chmod(0o755)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build(args.output))


if __name__ == "__main__":
    main()
