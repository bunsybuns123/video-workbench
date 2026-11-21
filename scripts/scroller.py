"""Bounded keyword discovery in an isolated local browser. Never starts analysis."""
import argparse
import json
from pathlib import Path
import queue
import sys
import threading
import time
import uuid
from urllib.parse import parse_qs, quote, urlparse
from common import APP_HOME, atomic_json, canonical_source, data_dirs, settings_load, utc, validate_settings

SNAPSHOT = r"""() => {
  const visible = e => { const r=e.getBoundingClientRect(); const s=getComputedStyle(e);
    return r.width>0 && r.height>0 && r.bottom>0 && r.right>0 && r.top<innerHeight && r.left<innerWidth
      && s.display!=='none' && s.visibility!=='hidden' && s.opacity!=='0' && !e.closest('[hidden],[aria-hidden=true]'); };
  const dialogs = [...document.querySelectorAll('[role=dialog],dialog[open],[data-e2e*=modal]')].filter(visible);
  const text = dialogs.map(e=>e.innerText).join('\n').toLowerCase();
  const body = (document.body?.innerText || '').slice(0,16000).toLowerCase();
  let gate = null;
  if ([...document.querySelectorAll('iframe[src*=captcha],iframe[title*=challenge],#captcha_container,[id*=captcha-verify]')].some(visible)
      || /verify to continue|verify you are human|complete the captcha|unusual traffic|security verification/.test(body)) gate='Verification required';
  else if (/consent\.(youtube|google)\.com/.test(location.hostname)) gate='Consent required';
  else if (/before you continue|accept all|reject all/.test(text)) gate='Consent required';
  else if (/\/login|\/signin/.test(location.pathname) || /accounts\.google\.com/.test(location.hostname)
      || /log in|sign in|login/.test(text)) gate='Sign-in required';
  else if (dialogs.length) gate='Action required';
  else if (/too many requests|access denied|temporarily blocked/.test(body)) gate='Access blocked';
  const links=[...document.querySelectorAll('a[href]')].filter(visible).map(a=>({
    url:a.href,title:(a.getAttribute('title')||a.getAttribute('aria-label')||a.innerText||'').trim().slice(0,500)
  })).filter(a=>/youtube\.com\/(watch\?|shorts\/)|youtu\.be\/|tiktok\.com\/@[^/]+\/video\//.test(a.url));
  return {gate,links};
}"""


def emit(event, **data):
    print(json.dumps({"event": event, **data}, ensure_ascii=False), flush=True)


def search_url(platform, prompt):
    if platform == "youtube":
        return "https://www.youtube.com/results?search_query=" + quote(prompt, safe="")
    if platform == "tiktok":
        return "https://www.tiktok.com/search/video?q=" + quote(prompt, safe="")
    raise ValueError("Platform required")


def collect(snapshot, session, prompt):
    count = 0
    lookup = {item["url"]: item for item in session["results"]}
    for item in snapshot["links"]:
        try:
            source = canonical_source(item["url"], local=False)
        except ValueError:
            continue
        if (session['platform']=='youtube') != ('youtube.com/' in source):
            continue
        now = utc()
        if source in lookup:
            record = lookup[source]
            if prompt not in record["search_prompts"]:
                record["search_prompts"].append(prompt)
                record["observations"].append({"search_prompt": prompt, "discovered_at": now})
            continue
        record = {"url": source, "title": item["title"], "platform": session["platform"],
                  "search_prompt": prompt, "search_prompts": [prompt], "discovered_at": now,
                  "discovery_session_id": session["id"], "origin": "keyword_scroller",
                  "observations": [{"search_prompt": prompt, "discovered_at": now}],
                  "automatic_analysis": False}
        session["results"].append(record)
        lookup[source] = record
        count += 1
        if len(session["results"]) >= session["settings"]["scroll_max_results"]:
            break
    return count


def matches_search(url, platform, prompt):
    parsed = urlparse(url)
    key = "search_query" if platform == "youtube" else "q"
    path = "/results" if platform == "youtube" else "/search"
    return parsed.path.startswith(path) and parse_qs(parsed.query).get(key, [None])[0] == prompt


def run(platform):
    from playwright.sync_api import sync_playwright
    from sessions import SessionQueue
    commands = queue.Queue()

    def receive():
        for line in sys.stdin:
            try:
                commands.put(json.loads(line))
            except ValueError:
                pass
        commands.put({"action": "stop"})

    threading.Thread(target=receive, daemon=True).start()
    profile = APP_HOME / "browsers" / platform
    profile.mkdir(parents=True, exist_ok=True)
    session = None
    active = False
    query_index, step = 0, 0
    next_action = time.monotonic()
    last_interaction = time.monotonic()
    path = None
    last_gate = None
    deadline = None
    processor = None

    def save(status):
        if session:
            if session.get("status") != status:
                session.setdefault("events", []).append({"status": status, "at": utc()})
            session["status"] = status
            session["updated_at"] = utc()
            if status in {"complete", "partial", "empty", "stopped", "time_limit", "result_limit", "idle_timeout", "interrupted"}:
                session["finished_at"] = utc()
            atomic_json(path, session)
            if processor:
                processor.save()

    with sync_playwright() as browser:
        # App-owned profile only. No personal profile import or cookie export.
        context = browser.chromium.launch_persistent_context(str(profile), headless=False,
                                                             viewport={"width": 1100, "height": 800},
                                                             accept_downloads=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(5000)
        try:
            page.goto("https://www.youtube.com" if platform == "youtube" else "https://www.tiktok.com", wait_until="domcontentloaded", timeout=30000)
            emit("status", status="Browser ready", platform=platform)
            while not page.is_closed():
                now = time.monotonic()
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    command = None
                if command:
                    last_interaction = now
                    action = command.get("action")
                    if action == "stop":
                        if processor:
                            processor.stop()
                        if session and session["status"] not in {"complete", "partial", "empty", "time_limit", "result_limit"}:
                            save("stopped")
                        break
                    if action == "pause":
                        active = False
                        save("paused")
                        emit("status", status="Paused")
                    if action == "resume" and session:
                        if query_index >= len(session["search_prompts"]) or session["status"] in {"complete", "partial", "empty", "time_limit", "result_limit"}:
                            emit("status", status="Start a new session")
                            continue
                        target = session["search_prompts"][min(query_index, len(session["search_prompts"]) - 1)]
                        if not matches_search(page.url, platform, target):
                            page.goto(search_url(platform, target), wait_until="domcontentloaded", timeout=30000)
                        active = True
                        next_action = now
                        emit("status", status="Running")
                    if action == "start":
                        if processor and processor.busy():
                            emit("status", status="Finish or stop current session")
                            continue
                        settings = validate_settings(command.get("settings", settings_load()))
                        prompts = list(dict.fromkeys(p.strip() for p in command.get("prompts", []) if p.strip()))
                        if not prompts or len(prompts) > 20 or any(len(p) > 200 for p in prompts):
                            emit("status", status="Keywords required: 1 to 20")
                            continue
                        identifier = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
                        if session and session["status"] not in {"complete", "partial", "empty", "time_limit", "result_limit"}:
                            save("stopped")
                        session = {"schema_version": 1, "id": identifier, "platform": platform,
                                   "project_id": command.get("project_id", "inbox"), "title": platform.capitalize() + " " + utc(),
                                   "search_prompts": prompts, "started_at": utc(), "settings": settings,
                                   "status": "running", "results": [], "events": [{"status": "running", "at": utc()}], "automatic_analysis": False,
                                   "automatic_codex_handoff": False}
                        path = data_dirs(settings) / "discoveries" / (identifier + ".json")
                        processor = SessionQueue(session) if command.get("analyze", True) else None
                        save("running")
                        query_index, step, active = 0, 0, True
                        deadline = now + settings["scroll_minutes"] * 60
                        page.goto(search_url(platform, prompts[0]), wait_until="domcontentloaded", timeout=30000)
                        next_action = time.monotonic() + settings["scroll_interval"]
                        emit("session", path=str(path))
                # Idle browser remains available for manual sign-in, for at most 30 minutes.
                if now - last_interaction > 1800:
                    save("idle_timeout")
                    break
                busy = processor.tick(allow_start=session["status"] not in {"paused", "action_required"}) if processor else False
                if session and not active and session["status"] in {"processing", "search_complete"} and not busy:
                    outcome = processor.outcome() if processor else "complete"
                    save(outcome)
                    emit("status", status=outcome.capitalize())
                if session and active and now >= deadline:
                    active = False
                    save("time_limit")
                    emit("status", status="Time limit")
                snapshot = page.evaluate(SNAPSHOT)
                host = (urlparse(page.url).hostname or "").lower()
                allowed = {"youtube.com", "www.youtube.com", "m.youtube.com"} if platform == "youtube" else {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
                gate = snapshot["gate"] or ("Navigation paused" if host not in allowed else None)
                if active and session and not gate and not matches_search(page.url, platform, session["search_prompts"][query_index]):
                    gate = "Search changed"
                if gate:
                    if active:
                        active = False
                        session["last_gate"] = {"status": gate, "at": utc()}
                        save("action_required")
                    if gate != last_gate:
                        emit("status", status=gate, attention=True)
                    last_gate = gate
                elif last_gate:
                    last_gate = None
                    emit("status", status="Ready to resume" if session else "Browser ready")
                if active and now >= next_action:
                    if busy:
                        page.wait_for_timeout(250)
                        continue
                    prompt = session["search_prompts"][query_index]
                    last_interaction = now
                    collect(snapshot, session, prompt)
                    if processor:
                        processor.enqueue()
                    save("running")
                    emit("results", path=str(path), count=len(session["results"]), prompt=prompt)
                    if len(session["results"]) >= session["settings"]["scroll_max_results"]:
                        active = False
                        save("processing" if processor and processor.busy() else "result_limit")
                        emit("status", status="Processing" if processor and processor.busy() else "Result limit")
                        continue
                    page.mouse.wheel(0, 750)
                    step += 1
                    if step >= session["settings"]["scroll_steps"]:
                        query_index += 1
                        step = 0
                        if query_index >= len(session["search_prompts"]):
                            active = False
                            outcome = "processing" if processor and processor.busy() else processor.outcome() if processor else "complete"
                            save(outcome)
                            emit("status", status=outcome.capitalize())
                        else:
                            page.goto(search_url(platform, session["search_prompts"][query_index]), wait_until="domcontentloaded", timeout=30000)
                    next_action = time.monotonic() + session["settings"]["scroll_interval"]
                page.wait_for_timeout(250)
        finally:
            if processor:
                processor.stop()
            if session and session["status"] == "running":
                save("interrupted")
            context.close()
    emit("status", status="Browser closed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("youtube", "tiktok"), required=True)
    args = parser.parse_args()
    try:
        run(args.platform)
    except Exception as error:
        # Do not emit full browser state, headers, cookies, or credentials.
        emit("status", status="Browser error", error_type=type(error).__name__)
        raise SystemExit(1)
