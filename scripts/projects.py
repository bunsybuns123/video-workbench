"""Project folders, session manifests and safe library transfers."""
from pathlib import Path
import re
import shutil
import uuid
from common import atomic_json, data_dirs, load_json, utc, exclusive, LockBusy

DONE = {"complete", "partial", "failed", "cancelled", "interrupted", "time_limit", "storage_limit",
        "access_blocked", "rate_limited", "sign_in_required", "model_unavailable", "removed"}


def assert_reading_idle(source):
    packets = [source] if (source/'packet.json').is_file() else [p.parent for p in source.glob('packets/*/packet.json')]
    for packet in packets:
        try:
            with exclusive(packet/'.evidence.lock'):
                pass
        except LockBusy:
            raise ValueError('Wait for reading build to finish') from None


def create_project(settings, name):
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("Project name required: 1 to 100 characters")
    identifier = (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:45] or "project") + "-" + uuid.uuid4().hex[:8]
    folder = data_dirs(settings) / "projects" / identifier
    folder.mkdir(parents=True)
    atomic_json(folder / "project.json", {"id": identifier, "name": name, "created_at": utc()})
    return folder


def project_list(settings):
    root = data_dirs(settings) / "projects"
    root.mkdir(exist_ok=True)
    inbox = root / "inbox"
    if not (inbox / "project.json").exists():
        inbox.mkdir(exist_ok=True)
        atomic_json(inbox / "project.json", {"id": "inbox", "name": "Inbox", "created_at": utc()})
    return [dict(load_json(p), path=str(p.parent)) for p in sorted(root.glob("*/project.json")) if load_json(p)]


def project_path(settings, identifier="inbox"):
    projects = {p["id"]: p for p in project_list(settings)}
    if identifier not in projects:
        raise ValueError("Project unavailable")
    return Path(projects[identifier]["path"])


def packet_files(settings):
    root = data_dirs(settings)
    return sorted([*root.glob("packets/*/packet.json"),
                   *root.glob("projects/*/packets/*/packet.json"),
                   *root.glob("projects/*/sessions/*/packets/*/packet.json")], reverse=True)


def migrate_legacy(settings):
    for file in data_dirs(settings).glob("packets/*/packet.json"):
        try:
            move_entry(settings, file.parent, "inbox")
        except ValueError:
            pass  # Active attempts stay in place until they finish.


def session_folder(settings, project_id, session_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session_id):
        raise ValueError("Invalid session")
    folder = project_path(settings, project_id) / "sessions" / session_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def update_project_indexes(settings, entries):
    for project in project_list(settings):
        packets = [p for p in entries if p.get("project_id") == project["id"]]
        folder = Path(project["path"])
        sessions = [load_json(p) for p in folder.glob("sessions/*/SESSION.json")]
        atomic_json(folder / "index.json", {"project": project, "updated_at": utc(), "packets": packets,
                                           "sessions": [s for s in sessions if s]})
        (folder / "PROJECT.md").write_text("# " + project["name"] + "\n\n" +
            "\n".join("- [" + (p.get("title") or p["packet_id"]).replace("]", "") + "](" +
                      str(Path(p["path"]).relative_to(folder)).replace("\\", "/") + "/PACKET.md) | " + p["status"] for p in packets), encoding="utf-8")


def move_entry(settings, source, target_project):
    root = data_dirs(settings).resolve()
    source = Path(source).resolve()
    target = project_path(settings, target_project).resolve()
    # Validate both final absolute paths before any recursive move.
    if not source.is_relative_to(root) or not target.is_relative_to(root) or source == root:
        raise ValueError("Library folder required")
    is_session = (source / "SESSION.json").is_file()
    if not is_session and not (source / "packet.json").is_file():
        raise ValueError("Packet or session required")
    destination = target / ("sessions" if is_session else "packets") / source.name
    if not destination.resolve().is_relative_to(root):
        raise ValueError("Library destination required")
    if source == destination:
        return source
    if destination.exists():
        raise ValueError("Destination already exists")
    assert_reading_idle(source)
    jobs = [load_json(p) for p in (root / "jobs").glob("*.json")]
    affected = [j for j in jobs if j and Path(j["packet_path"]).resolve().is_relative_to(source)]
    terminal = {"complete", "partial", "failed", "cancelled", "interrupted", "time_limit", "storage_limit", "access_blocked", "rate_limited", "sign_in_required", "model_unavailable"}
    if any(j["status"] not in terminal for j in affected):
        raise ValueError("Wait for processing to finish")
    session = load_json(source / "SESSION.json") if is_session else None
    if session and session.get("status") in {"running", "processing", "paused", "action_required"}:
        raise ValueError("Stop session before moving")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    for job in affected:
        job["packet_path"] = str(destination / Path(job["packet_path"]).resolve().relative_to(source))
        job["project_id"] = target_project
        if not is_session:
            job["session_id"] = None
        atomic_json(root / "jobs" / (job["id"] + ".json"), job)
    manifests = list(destination.glob("packets/*/packet.json")) if is_session else [destination / "packet.json"]
    for file in manifests:
        record = load_json(file)
        record["project_id"] = target_project
        if not is_session:
            record["session_id"] = None
        atomic_json(file, record)
    if session:
        session["project_id"] = target_project
        session["folder"] = str(destination)
        atomic_json(destination / "SESSION.json", session)
        discovery = root / "discoveries" / (session["id"] + ".json")
        atomic_json(discovery, session)
    return destination


def remove_entry(settings, source):
    root = data_dirs(settings).resolve()
    source = Path(source).resolve()
    if not source.is_relative_to(root) or source.is_relative_to(root / ".trash") or not ((source / "packet.json").is_file() or (source / "SESSION.json").is_file()):
        raise ValueError("Library packet or session required")
    assert_reading_idle(source)
    jobs = [load_json(p) for p in (root / "jobs").glob("*.json")]
    jobs = [j for j in jobs if j and Path(j["packet_path"]).resolve().is_relative_to(source)]
    if any(j["status"] not in DONE for j in jobs):
        raise ValueError("Wait for processing to finish")
    session = load_json(source / "SESSION.json", {})
    if session.get("status") in {"running", "processing", "paused", "action_required"}:
        raise ValueError("Stop session before removing")
    trash = root / ".trash" / uuid.uuid4().hex
    destination = trash / source.name
    if not destination.resolve().is_relative_to(root / ".trash"):
        raise ValueError("Invalid removal target")
    trash.mkdir(parents=True)
    atomic_json(trash / "removal.json", {"original": str(source), "removed_at": utc(), "jobs": jobs})
    shutil.move(str(source), str(destination))
    for job in jobs:
        job = dict(job, status="removed")
        atomic_json(root / "jobs" / (job["id"] + ".json"), job)


def undo_remove(settings):
    root = data_dirs(settings).resolve()
    removals = sorted((root / ".trash").glob("*/removal.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for file in removals:
        record = load_json(file)
        if record.get("restored_at"):
            continue
        original = Path(record["original"]).resolve()
        source = file.parent / original.name
        if not original.is_relative_to(root) or not source.resolve().is_relative_to(root / ".trash") or original.exists():
            raise ValueError("Original folder unavailable")
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(original))
        for job in record["jobs"]:
            atomic_json(root / "jobs" / (job["id"] + ".json"), job)
        record["restored_at"] = utc()
        atomic_json(file, record)
        return original
    raise ValueError("Nothing to restore")
