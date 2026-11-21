"""Install this self-contained skill locally, preserving the previous version."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import zipfile
from common import APP_HOME


def install(source=None):
    source = Path(source or Path(__file__).resolve().parents[1]).resolve()
    codex_skills = ".agents" / "skills" if sys.platform == "darwin" else ".codex" / "skills"
    target = Path.home() / codex_skills / "video-rabbit-holes"
    if source == target.resolve():
        print("Already installed")
        return target
    if target.exists():
        backups = APP_HOME / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        with zipfile.ZipFile(backups / ("video-rabbit-holes-" + stamp + ".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
            for path in target.rglob("*"):
                if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts:
                    archive.write(path, path.relative_to(target))
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(str(target))
    return target


if __name__ == "__main__":
    install()
