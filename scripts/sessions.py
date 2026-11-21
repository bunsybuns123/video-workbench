"""One bounded processing queue per discovery session."""
from pathlib import Path
from common import atomic_json, data_dirs, load_json, new_job, spawn, stop_process, utc
from projects import session_folder

DONE = {"complete", "partial", "failed", "cancelled", "interrupted", "time_limit", "storage_limit", "access_blocked", "rate_limited", "sign_in_required", "model_unavailable", "removed"}


class SessionQueue:
    def __init__(self, session):
        self.session = session
        self.process = None
        self.current = None
        self.log = None
        self.folder = session_folder(session["settings"], session.get("project_id", "inbox"), session["id"])
        session["folder"] = str(self.folder)
        session["automatic_analysis"] = True
        self.stopped = False
        self.pending = []

    def save(self):
        atomic_json(self.folder / "SESSION.json", self.session)
        atomic_json(data_dirs(self.session["settings"]) / "discoveries" / (self.session["id"] + ".json"), self.session)
        lines = ["# " + self.session.get("title", "Video session"), "", "Started: " + self.session["started_at"],
                 "Search prompts: " + "; ".join(self.session["search_prompts"]), "", "## Videos", ""]
        for record in self.session["results"]:
            job = load_json(data_dirs(self.session["settings"]) / "jobs" / (record.get("job_id", "missing") + ".json"), {})
            lines.append("- " + record["url"] + " | " + job.get("status", "pending") + " | " + record["search_prompt"])
        (self.folder / "SESSION.md").write_text("\n".join(lines), encoding="utf-8")

    def enqueue(self):
        if self.stopped:
            return
        for record in self.session["results"]:
            if record.get("job_id"):
                continue
            provenance = {k: record.get(k) for k in ("origin", "search_prompt", "search_prompts", "discovered_at", "discovery_session_id", "observations")}
            job = new_job(record["url"], self.session["settings"], provenance, self.session.get("project_id", "inbox"), self.session["id"])
            record["job_id"] = job["id"]
            self.pending.append(job["id"])
        self.save()

    def tick(self, allow_start=True):
        root = data_dirs(self.session["settings"])
        changed = False
        if self.process and self.process.poll() is not None:
            path = root / "jobs" / (self.current["id"] + ".json")
            job = load_json(path)
            if job and job["status"] not in DONE:
                job.update(status="interrupted", finished_at=utc())
                atomic_json(path, job)
            self.log.close()
            self.process = self.current = self.log = None
            changed = True
        if not self.process and not self.stopped and allow_start:
            while self.pending:
                identifier = self.pending.pop(0)
                path = root / "jobs" / (identifier + ".json")
                job = load_json(path, {})
                if job.get("status") == "queued":
                    self.current = job
                    self.log = (root / "jobs" / (job["id"] + ".private.log")).open("wb")
                    self.process = spawn("analyzer.py", ["run", "--job", path], stdout=self.log, stderr=self.log)
                    changed = True
                    break
        if changed:
            self.save()
        return self.busy()

    def outcome(self):
        from collections import Counter
        root = data_dirs(self.session["settings"])
        counts = Counter(load_json(root / "jobs" / (r.get("job_id", "missing") + ".json"), {}).get("status", "pending") for r in self.session["results"])
        self.session["summary"] = dict(counts)
        return "empty" if not counts else "complete" if set(counts) == {"complete"} else "partial"

    def busy(self):
        return bool(self.process) or bool(self.pending)

    def stop(self):
        self.stopped = True
        self.pending.clear()
        if self.process:
            folder = Path(self.current["packet_path"])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "STOP").touch()
            try:
                self.process.wait(timeout=3)
            except Exception:
                stop_process(self.process)
            self.log.close()
            self.process = None
        root = data_dirs(self.session["settings"])
        for r in self.session["results"]:
            path = root / "jobs" / (r.get("job_id", "missing") + ".json")
            job = load_json(path)
            if job and job["status"] not in DONE:
                job.update(status="cancelled", finished_at=utc())
                atomic_json(path, job)
        self.save()
