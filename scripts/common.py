"""Local storage and validation shared by the desktop app and workers."""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import parse_qs, urlparse

if os.name == "nt":
    _APP_DATA_HOME = Path(os.environ.get("LOCALAPPDATA", Path.home()))
elif sys.platform == "darwin":
    _APP_DATA_HOME = Path.home() / "Library" / "Application Support"
else:
    _APP_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
APP_HOME = Path(os.environ.get("VIDEO_WORKBENCH_HOME", str(_APP_DATA_HOME / "VideoWorkbench")))
RUNTIME = Path(os.environ.get("VIDEO_WORKBENCH_RUNTIME", Path.home() / ".codex" / "video-research-runtime"))
CONFIG = APP_HOME / "settings.json"
DEFAULTS = {
    "output_dir": str(Path.home() / "Documents" / "Video Workbench"),
    "obsidian_vault": "",
    "model": "base", "language": "auto", "threads": 3 if sys.platform == "darwin" else 4,
    "frame_interval": 0.5, "frame_width": 960,
    "download_height": 1080, "evidence_max_hold": 20,
    "evidence_min_change": 2, "ocr_threads": 2, "speech_vocabulary": "",
    "ocr_engine": "windows" if os.name == 'nt' else "rapidocr", "ocr_language": "en-US",
    "max_duration_minutes": 60, "max_download_mib": 512,
    "max_packet_mib": 2048, "job_timeout_minutes": 60,
    "scroll_interval": 4, "scroll_steps": 10,
    "scroll_minutes": 5, "scroll_max_results": 100,
    "caption_languages": "en.*,fi",
}


class LockBusy(RuntimeError):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def atomic_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex[:8] + ".tmp")
    try:
        tmp.write_text(value, encoding='utf-8')
        # Windows readers and antivirus can briefly hold a handle without delete sharing.
        for attempt in range(9):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 8:
                    raise
                time.sleep(min(.01 * 2**attempt, .25))
    finally:
        tmp.unlink(missing_ok=True)


def load_json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def validate_settings(values):
    settings = {**DEFAULTS, **values}
    ranges = {
        "threads": (1, 16, int), "frame_interval": (0.1, 30, float),
        "frame_width": (320, 1920, int), "max_duration_minutes": (1, 240, int),
        "download_height": (360, 2160, int), "evidence_max_hold": (5, 120, float),
        "evidence_min_change": (0.5, 10, float), "ocr_threads": (1, 8, int),
        "max_download_mib": (16, 4096, int), "max_packet_mib": (32, 16384, int),
        "job_timeout_minutes": (1, 240, int), "scroll_interval": (2, 60, float),
        "scroll_steps": (1, 200, int), "scroll_minutes": (1, 30, int),
        "scroll_max_results": (1, 1000, int),
    }
    for key, (low, high, cast) in ranges.items():
        try:
            number = cast(settings[key])
            if not math.isfinite(number) or not low <= number <= high:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{key}: {low} to {high}") from None
        settings[key] = number
    output = str(settings["output_dir"]).strip()
    if not output or output.startswith(("\\\\", "//")) or "://" in output:
        raise ValueError("Local output folder required")
    settings["output_dir"] = str(Path(output).expanduser().resolve())
    vault = str(settings.get("obsidian_vault", "")).strip()
    if vault:
        if vault.startswith(("\\\\", "//")) or "://" in vault:
            raise ValueError("Local Obsidian vault folder required")
        settings["obsidian_vault"] = str(Path(vault).expanduser().resolve())
    if settings["model"] not in {"tiny", "base", "small", "medium"}:
        raise ValueError("Model: tiny, base, small, medium")
    if not re.fullmatch(r"auto|[a-z]{2,3}", settings["language"]):
        raise ValueError("Language: auto or language code")
    if len(settings["caption_languages"]) > 200:
        raise ValueError("Caption languages too long")
    if not isinstance(settings['speech_vocabulary'], str) or len(settings['speech_vocabulary']) > 1000:
        raise ValueError('Speech vocabulary: maximum 1000 characters')
    if settings['evidence_min_change'] > settings['evidence_max_hold']:
        raise ValueError('Screen change interval exceeds maximum hold')
    if settings['ocr_engine'] not in {'windows', 'rapidocr'}:
        raise ValueError('OCR engine: windows or rapidocr')
    if os.name != 'nt' and settings['ocr_engine'] == 'windows':
        settings['ocr_engine'] = 'rapidocr'
    if not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z]{2,4})?', settings['ocr_language']):
        raise ValueError('OCR language code required')
    return settings


def settings_load():
    return validate_settings(load_json(CONFIG, {}) or {})


def canonical_source(value, local=True):
    value = value.strip()
    if value.startswith(("http:", "https:")):
        p = urlparse(value)
        if p.scheme != "https" or p.username or p.password or p.port:
            raise ValueError("Public HTTPS video link required")
        host = (p.hostname or "").lower()
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
            parts = p.path.strip("/").split("/")
            video = parts[0] if host == "youtu.be" else (
                parts[1] if len(parts) == 2 and parts[0] in {"shorts", "embed", "live"}
                else parse_qs(p.query).get("v", [""])[0] if p.path == "/watch" else "")
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
                return "https://www.youtube.com/watch?v=" + video
        if host in {"www.tiktok.com", "tiktok.com", "m.tiktok.com"}:
            if re.fullmatch(r"/@[A-Za-z0-9_.]+/video/[0-9]{10,25}/?", p.path):
                return "https://www.tiktok.com" + p.path.rstrip("/")
            if re.fullmatch(r"/t/[A-Za-z0-9_-]{4,80}/?", p.path):
                return "https://www.tiktok.com" + p.path.rstrip("/") + "/"
        if host in {"vm.tiktok.com", "vt.tiktok.com"} and re.fullmatch(r"/[A-Za-z0-9_-]{4,80}/?", p.path):
            return "https://" + host + p.path.rstrip("/") + "/"
        raise ValueError("Single YouTube or TikTok video required")
    if not local or value.startswith(("\\\\", "//")) or "://" in value:
        raise ValueError("Video link required")
    file = Path(value).expanduser().resolve()
    if not file.is_file() or file.suffix.lower() not in {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}:
        raise ValueError("Local video file required")
    return str(file)


def data_dirs(settings):
    root = Path(settings["output_dir"])
    for name in ("packets", "discoveries", "jobs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def new_job(source, settings, provenance=None, project_id="inbox", session_id=None):
    settings = validate_settings(settings)
    root = data_dirs(settings)
    source = canonical_source(source)
    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    from projects import project_path, session_folder
    parent = session_folder(settings, project_id, session_id) if session_id else project_path(settings, project_id)
    job = {"schema_version": 1, "id": identifier, "source": source, "project_id": project_id, "session_id": session_id,
           "created_at": utc(), "status": "queued", "settings": settings,
           "provenance": provenance or {"origin": "manual_link", "search_prompt": None,
                                         "discovered_at": None, "discovery_session_id": None},
           "packet_path": str(parent / "packets" / identifier), "error": None}
    atomic_json(root / "jobs" / (identifier + ".json"), job)
    return job


def binary(name):
    candidate = RUNTIME / "ffmpeg" / "bin" / (name + (".exe" if os.name == "nt" else ""))
    found = str(candidate) if candidate.is_file() else shutil.which(name)
    if not found and name == "ffmpeg":
        try:
            import imageio_ffmpeg
            found = imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError):
            pass
    if not found:
        raise RuntimeError(f"{name} unavailable")
    return found


def open_path(path):
    """Open a local folder or file with the operating system's default app."""
    path = str(Path(path))
    if os.name == "nt":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def worker_env(settings=None):
    env = {k: v for k, v in os.environ.items() if not re.search(r"TOKEN|SECRET|PASSWORD|API_KEY|COOKIE", k, re.I)}
    env.update({"HF_HOME": str(RUNTIME / "models"), "HF_HUB_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
                "DO_NOT_TRACK": "1", "PYTHONIOENCODING": "utf-8", "CUDA_VISIBLE_DEVICES": "-1"})
    scripts = "Scripts" if os.name == "nt" else "bin"
    env["PATH"] = os.pathsep.join([str(RUNTIME / "ffmpeg" / "bin"), str(Path(sys.executable).parent),
                                   str(RUNTIME / "venv" / scripts), env.get("PATH", "")])
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        env[key] = str((settings or DEFAULTS)["threads"])
    return env


def stop_process(process):
    if process and process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def spawn(script, args, **kwargs):
    # pythonw has no stdout. Explicit pipes/files supplied by callers work on Windows.
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--video-workbench-worker", script, *map(str, args)]
    else:
        command = [sys.executable, str(Path(__file__).parent / script), *map(str, args)]
    return subprocess.Popen(command,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                            start_new_session=os.name != "nt", env=worker_env(), **kwargs)


def folder_size(root):
    total = 0
    for file in Path(root).rglob("*"):
        try:
            if file.is_file() and not file.is_symlink():
                total += file.stat().st_size
        except FileNotFoundError:
            pass
    return total


@contextmanager
def exclusive(path):
    """OS-held nonblocking lock, released after process termination."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    locked = False
    try:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise LockBusy("Already active") from error
        locked = True
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def clock(seconds, srt=False):
    ms = max(0, round(seconds * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}{',' if srt else '.'}{ms % 1000:03}"


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()
