"""Video Workbench desktop UI. This program has no Codex integration."""
import importlib.metadata
import json
import os
from pathlib import Path
import queue
import runpy
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk
from projects import create_project, project_list, project_path, move_entry
from analyzer import rebuild_index, packet_manifest, save_job
from common import (APP_HOME, CONFIG, DEFAULTS, RUNTIME, atomic_json, data_dirs,
                    exclusive, load_json, LockBusy, new_job, settings_load, spawn, stop_process, utc,
                    validate_settings, open_path)
from install import install as install_codex_skill

BG = "#F6F4EF"
CARD = "#FFFEFB"
SIDEBAR = "#ECE9E1"
TEXT = "#292824"
MUTED = "#706D64"
ACCENT = "#9A5238"
BORDER = "#DAD6CC"
TERMINAL = {"complete", "partial", "failed", "cancelled", "interrupted", "time_limit", "storage_limit", "access_blocked", "rate_limited", "sign_in_required", "model_unavailable", "removed"}


class Workbench:
    def __init__(self, root):
        self.root = root
        root.title("Video Workbench")
        root.geometry("1180x820")
        root.minsize(980, 720)
        root.configure(bg=BG)
        try:
            self.ui_font = tkfont.nametofont("TkDefaultFont").actual("family") if sys.platform == "darwin" else "Segoe UI"
        except tk.TclError:
            self.ui_font = "TkDefaultFont"
        try:
            self.settings = settings_load()
        except ValueError:
            self.settings = dict(DEFAULTS)
        self.data = data_dirs(self.settings)
        self.analysis_process = None
        self.analysis_job = None
        self.analysis_log = None
        self.evidence_process = None
        self.evidence_log = None
        self.evidence_packet = None
        self.evidence_queue = []
        self.evidence_stopping = False
        self.run_queue = False
        self.browser = None
        self.browser_platform = None
        self.browser_events = queue.Queue()
        self.discovery_records = {}
        self.session_processors = []
        self.jobs = {}
        self.pages = {}
        self.active_view = "Analyzer"
        self.nav_buttons = {}
        self.status = tk.StringVar(value="Ready")
        self.browser_status = tk.StringVar(value="Closed")
        self.notice = tk.StringVar(value="")
        self.project = tk.StringVar(value="Inbox")
        self.project_boxes = []
        self.refresh_projects()
        self.last_job_signature = None
        self.last_discovery_refresh = 0
        self.configure_style()
        self.build_shell()
        self.build_analyzer()
        self.build_library()
        self.build_scroller()
        self.build_settings()
        self.recover_jobs()
        self.refresh_library()
        self.refresh_discoveries()
        self.show("Analyzer")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(300, self.tick)

    def configure_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, troughcolor=SIDEBAR, font=(self.ui_font, 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Georgia", 27), foreground=TEXT)
        style.configure("TButton", padding=(14, 9), background=CARD, borderwidth=1, relief="flat")
        style.map("TButton", background=[("active", SIDEBAR)], foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF", borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#82422C"), ("disabled", BORDER)])
        style.configure("TEntry", fieldbackground=CARD, padding=8, borderwidth=1)
        style.configure("TCombobox", fieldbackground=CARD, padding=7)
        style.configure("Treeview", fieldbackground=CARD, background=CARD, rowheight=36, borderwidth=0,
                        font=(self.ui_font, 10))
        style.configure("Treeview.Heading", background=SIDEBAR, font=(self.ui_font, 10, "bold"), padding=(8, 10), relief="flat")
        style.map("Treeview", background=[("selected", "#DFD8CC")], foreground=[("selected", TEXT)])
        style.configure("Horizontal.TProgressbar", background=ACCENT, borderwidth=0, thickness=5)

    def build_shell(self):
        side = tk.Frame(self.root, bg=SIDEBAR, width=230)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="Video\nWorkbench", font=("Georgia", 20), bg=SIDEBAR, fg=TEXT,
                 justify="left", anchor="w").pack(fill="x", padx=25, pady=(35, 38))
        for name in ("Analyzer", "Library", "Scroller", "Settings"):
            button = tk.Button(side, text=name, font=(self.ui_font, 11), anchor="w", relief="flat", bd=0,
                               bg=SIDEBAR, fg=MUTED, activebackground=BG, padx=18, pady=13,
                               command=lambda n=name: self.show(n), cursor="hand2")
            button.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[name] = button
        tk.Label(side, textvariable=self.notice, bg=SIDEBAR, fg=MUTED, font=(self.ui_font, 9),
                 wraplength=180, justify="left").pack(side="bottom", anchor="w", padx=25, pady=28)
        self.main = ttk.Frame(self.root, padding=(34, 28, 34, 18))
        self.main.pack(side="left", fill="both", expand=True)
        for name in ("Analyzer", "Library", "Scroller", "Settings"):
            self.pages[name] = ttk.Frame(self.main)

    def show(self, name):
        self.active_view = name
        for title, frame in self.pages.items():
            frame.pack_forget()
            self.nav_buttons[title].configure(bg=BG if title == name else SIDEBAR, fg=TEXT if title == name else MUTED)
        self.pages[name].pack(fill="both", expand=True)
        if name == "Library":
            self.refresh_library()
        if name == "Scroller":
            self.refresh_discoveries()

    def heading(self, page, title):
        ttk.Label(page, text=title, style="Title.TLabel").pack(anchor="w", pady=(0, 25))

    def project_picker(self, page):
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(0, 12))
        ttk.Label(row, text="Project").pack(side="left", padx=(0, 12))
        box = ttk.Combobox(row, textvariable=self.project, state="readonly", values=list(self.project_map))
        box.pack(side="left", fill="x", expand=True)
        box.bind("<<ComboboxSelected>>", lambda _: (self.refresh_library(), self.refresh_jobs()))
        self.project_boxes.append(box)
        ttk.Button(row, text="New project", command=self.new_project).pack(side="right", padx=(8, 0))
        if page is self.pages["Library"]:
            ttk.Button(row, text="Copy project path", command=self.copy_project_path).pack(side="right", padx=(8, 0))

    def copy_project_path(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(str(project_path(self.settings, self.selected_project())))

    def refresh_projects(self):
        from collections import Counter
        projects = project_list(self.settings)
        counts = Counter(p['name'] for p in projects)
        self.project_map = {p['name'] + (' [' + p['id'][-8:] + ']' if counts[p['name']] > 1 else ''): p for p in projects}
        if self.project.get() not in self.project_map:
            self.project.set(next(iter(self.project_map)))
        for box in self.project_boxes:
            box.configure(values=list(self.project_map))

    def selected_project(self):
        return self.project_map[self.project.get()]["id"]

    def new_project(self):
        name = simpledialog.askstring("New project", "Name", parent=self.root)
        if name:
            try:
                folder = create_project(self.settings, name)
                self.refresh_projects()
                self.project.set(next(k for k, p in self.project_map.items() if p["id"] == folder.name))
                self.refresh_library()
            except ValueError as error:
                messagebox.showerror("Project", str(error))

    def label(self, parent, value):
        ttk.Label(parent, text=value, style="Muted.TLabel").pack(anchor="w", pady=(0, 7))

    def text_box(self, parent, height):
        box = tk.Text(parent, height=height, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="solid",
                      bd=1, highlightthickness=0, wrap="word", padx=12, pady=12, font=(self.ui_font, 11), undo=True)
        box.pack(fill="x")
        return box

    def table(self, parent, columns, height=8):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height, selectmode="extended")
        for name, title, width in columns:
            tree.heading(name, text=title)
            tree.column(name, width=width, minwidth=60, stretch=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def build_analyzer(self):
        page = self.pages["Analyzer"]
        self.heading(page, "Analyzer")
        self.project_picker(page)
        self.label(page, "Video links")
        self.links = self.text_box(page, 3)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(15, 0))
        ttk.Label(row, text="Search prompt", style="Muted.TLabel").pack(side="left")
        ttk.Label(row, text="Optional", style="Muted.TLabel").pack(side="right")
        self.prompt = ttk.Entry(page)
        self.prompt.pack(fill="x", pady=(7, 15))
        actions = ttk.Frame(page)
        actions.pack(fill="x", pady=(0, 24))
        ttk.Button(actions, text="Analyze", style="Accent.TButton", command=self.add_links).pack(side="left")
        ttk.Button(actions, text="Add files", command=self.add_files).pack(side="left", padx=9)
        ttk.Button(actions, text="Run queue", command=self.start_queue).pack(side="right")
        self.label(page, "Queue")
        self.job_tree = self.table(page, [("source", "Source", 350), ("status", "Status", 150), ("date", "Queued", 170)], 3)
        self.progress = ttk.Progressbar(page, mode="determinate")
        self.progress.pack(fill="x", pady=(16, 10))
        footer = ttk.Frame(page)
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        ttk.Button(footer, text="Cancel", command=self.cancel_selected).pack(side="right")
        ttk.Button(footer, text="Retry", command=self.retry_selected).pack(side="right", padx=9)

    def build_library(self):
        page = self.pages["Library"]
        self.heading(page, "Library")
        self.project_picker(page)
        top = ttk.Frame(page)
        top.pack(fill="x", pady=(0, 18))
        self.filter = ttk.Entry(top)
        self.filter.pack(side="left", fill="x", expand=True)
        self.filter.bind("<KeyRelease>", lambda _: self.refresh_library(False))
        ttk.Button(top, text="Refresh", command=self.refresh_library).pack(side="right", padx=(10, 0))
        self.packet_tree = self.table(page, [("title", "Title", 260), ("prompt", "Search prompt", 230),
                                             ("date", "Processed", 150), ("status", "Collection / Reading / Review", 245)], 6)
        self.packet_tree.bind("<<TreeviewSelect>>", self.select_packet)
        self.packet_tree.configure(show="tree headings")
        self.packet_tree.column("#0", width=26, minwidth=26, stretch=False)
        self.packet_detail = tk.StringVar(value="")
        ttk.Label(page, textvariable=self.packet_detail, style="Muted.TLabel", wraplength=820, justify="left").pack(anchor="w", pady=18)
        actions = ttk.Frame(page)
        actions.pack(fill="x")
        ttk.Button(actions, text="Open folder", command=self.open_selected).pack(side="left")
        ttk.Button(actions, text="Copy path", command=self.copy_selected).pack(side="left", padx=9)
        ttk.Button(actions, text="Move", command=self.move_selected).pack(side="left")
        ttk.Button(actions, text="Remove", command=self.remove_selected).pack(side="left", padx=9)
        ttk.Button(actions, text="Undo", command=self.undo_remove).pack(side="left")
        self.packet_tree.bind("<Delete>", lambda _: self.remove_selected())
        ttk.Button(actions, text="Project folder", command=lambda: self.open_folder(project_path(self.settings, self.selected_project()))).pack(side="right")
        reading = ttk.Frame(page)
        reading.pack(fill="x", pady=(12, 0))
        ttk.Button(reading, text="Open reading", command=self.open_reading).pack(side="left")
        ttk.Button(reading, text="Build reading", command=self.build_reading).pack(side="left", padx=9)
        ttk.Button(reading, text="Stop reading build", command=self.stop_reading).pack(side="left")

    def build_scroller(self):
        page = self.pages["Scroller"]
        self.heading(page, "Scroller")
        self.project_picker(page)
        top = ttk.Frame(page)
        top.pack(fill="x", pady=(0, 16))
        self.platform = tk.StringVar(value="TikTok")
        ttk.Combobox(top, textvariable=self.platform, values=["TikTok", "YouTube"], state="readonly", width=16).pack(side="left")
        ttk.Label(top, textvariable=self.browser_status, style="Muted.TLabel").pack(side="right")
        self.label(page, "Keywords")
        self.keywords = self.text_box(page, 2)
        actions = ttk.Frame(page)
        actions.pack(fill="x", pady=(15, 23))
        ttk.Button(actions, text="Open browser", command=self.open_browser).pack(side="left")
        ttk.Button(actions, text="Start", style="Accent.TButton", command=self.start_scroll).pack(side="left", padx=9)
        ttk.Button(actions, text="Pause", command=lambda: self.browser_command("pause")).pack(side="left")
        ttk.Button(actions, text="Resume", command=lambda: self.browser_command("resume")).pack(side="left", padx=9)
        ttk.Button(actions, text="Stop", command=lambda: self.browser_command("stop")).pack(side="right")
        self.label(page, "Discovered links")
        self.discovery_tree = self.table(page, [("title", "Title", 270), ("prompt", "Search prompt", 200),
                                                ("platform", "Platform", 90), ("date", "Discovered", 160)], 4)
        footer = ttk.Frame(page)
        footer.pack(fill="x", pady=(18, 0))
        ttk.Button(footer, text="Process selected", command=self.queue_discoveries).pack(side="left")
        ttk.Button(footer, text="Refresh", command=self.refresh_discoveries).pack(side="left", padx=9)
        ttk.Button(footer, text="Open discoveries", command=lambda: self.open_folder(self.data / "discoveries")).pack(side="right")

    def build_settings(self):
        page = self.pages["Settings"]
        self.heading(page, "Settings")
        canvas = tk.Canvas(page, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        form = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        def scroll_settings(event):
            if self.active_view != "Settings":
                return
            delta = int(event.delta / 120) if os.name == "nt" else int(event.delta)
            if delta:
                canvas.yview_scroll(-delta, "units")
        self.root.bind("<MouseWheel>", scroll_settings)
        self.setting_vars = {}
        fields = [("output_dir", "Output folder"), ("model", "Speech model"), ("language", "Language"),
                  ("threads", "CPU threads"), ("frame_interval", "Frame interval (seconds)"),
                  ("frame_width", "Frame width (pixels)"), ("caption_languages", "Caption languages"),
                  ("download_height", "Download height (pixels)"), ("evidence_max_hold", "Screen anchor (seconds)"),
                  ("evidence_min_change", "Screen change (seconds)"), ("ocr_threads", "OCR threads"),
                  ("ocr_engine", "OCR engine"), ("ocr_language", "Screen text language"),
                  ("speech_vocabulary", "Speech vocabulary"),
                  ("max_duration_minutes", "Video limit (minutes)"), ("max_download_mib", "Download limit (MiB)"),
                  ("max_packet_mib", "Packet limit (MiB)"), ("job_timeout_minutes", "Processing limit (minutes)"),
                  ("scroll_interval", "Scroll interval (seconds)"), ("scroll_steps", "Scrolls per keyword"),
                  ("scroll_minutes", "Scroller limit (minutes)"), ("scroll_max_results", "Result limit")]
        for i, (key, label) in enumerate(fields):
            row = ttk.Frame(form)
            row.pack(fill="x", pady=6)
            ttk.Label(row, text=label, width=27).pack(side="left")
            var = tk.StringVar(value=str(self.settings[key]))
            self.setting_vars[key] = var
            if key == "model":
                ttk.Combobox(row, textvariable=var, values=["tiny", "base", "small", "medium"], state="readonly", width=23).pack(side="left")
            elif key == 'ocr_engine':
                ocr_options = ['windows', 'rapidocr'] if os.name == 'nt' else ['rapidocr']
                ttk.Combobox(row, textvariable=var, values=ocr_options, state='readonly', width=23).pack(side='left')
            else:
                ttk.Entry(row, textvariable=var, width=44 if key == "output_dir" else 25).pack(side="left", fill="x", expand=key == "output_dir")
            if key == "output_dir":
                ttk.Button(row, text="Browse", command=self.browse_output).pack(side="right", padx=8)

        links = ttk.LabelFrame(form, text="Obsidian and Codex", padding=12)
        links.pack(fill="x", pady=(18, 8))
        vault_row = ttk.Frame(links)
        vault_row.pack(fill="x")
        ttk.Label(vault_row, text="Obsidian vault", width=27).pack(side="left")
        self.vault_var = tk.StringVar(value=self.settings.get("obsidian_vault", ""))
        ttk.Entry(vault_row, textvariable=self.vault_var).pack(side="left", fill="x", expand=True)
        ttk.Button(vault_row, text="Browse", command=self.browse_obsidian_vault).pack(side="left", padx=(8, 0))
        vault_actions = ttk.Frame(links)
        vault_actions.pack(fill="x", pady=(10, 0))
        ttk.Button(vault_actions, text="Use vault for library", command=self.use_vault_for_library).pack(side="left")
        ttk.Button(vault_actions, text="Open vault", command=self.open_obsidian_vault).pack(side="left", padx=8)
        ttk.Button(vault_actions, text="Install Codex skill", command=self.install_codex_skill).pack(side="left")
        ttk.Label(links, text="Choosing a vault for the library stores project packets as Markdown inside it.",
                  style="Muted.TLabel", wraplength=780).pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(form)
        row.pack(fill="x", pady=(22, 20))
        ttk.Button(row, text="Save", style="Accent.TButton", command=self.save_settings).pack(side="left")
        ttk.Button(row, text="Diagnostics", command=self.diagnostics).pack(side="left", padx=9)
        self.settings_status = tk.StringVar(value="")
        ttk.Label(form, textvariable=self.settings_status, style="Muted.TLabel", wraplength=780).pack(anchor="w", pady=(0, 18))

    def browse_output(self):
        selected = filedialog.askdirectory(title="Output folder")
        if selected:
            self.setting_vars["output_dir"].set(selected)

    def browse_obsidian_vault(self):
        selected = filedialog.askdirectory(title="Obsidian vault folder")
        if selected:
            self.vault_var.set(selected)

    def use_vault_for_library(self):
        vault = self.vault_var.get().strip()
        if not vault or not Path(vault).expanduser().is_dir():
            self.settings_status.set("Choose an existing Obsidian vault folder first")
            return
        self.setting_vars["output_dir"].set(str(Path(vault).expanduser() / "Video Workbench"))
        self.settings_status.set("Output folder set. Save settings to use it.")

    def open_obsidian_vault(self):
        vault = Path(self.vault_var.get().strip()).expanduser()
        if not vault.is_dir():
            self.settings_status.set("Choose an existing Obsidian vault folder")
            return
        open_path(vault)

    def install_codex_skill(self):
        try:
            bundled_source = os.environ.get("VIDEO_WORKBENCH_BUNDLE_SOURCE")
            target = install_codex_skill(bundled_source or Path(__file__).resolve().parents[1])
            self.settings_status.set("Codex skill installed: " + str(target))
        except OSError as error:
            self.settings_status.set("Codex skill install failed: " + str(error))

    def save_settings(self):
        if self.analysis_process or self.evidence_process or (self.browser and self.browser.poll() is None):
            messagebox.showinfo("Settings", "Stop active jobs and browser first")
            return
        try:
            values = {k: v.get() for k, v in self.setting_vars.items()}
            values["obsidian_vault"] = self.vault_var.get().strip()
            settings = validate_settings(values)
            self.data = data_dirs(settings)
            atomic_json(CONFIG, settings)
            self.settings = settings
            self.refresh_projects()
            self.reload_jobs()
            self.refresh_library()
            self.refresh_discoveries()
            self.settings_status.set("Saved")
        except (ValueError, OSError) as error:
            self.settings_status.set(str(error))

    def diagnostics(self):
        versions = []
        for package in ("yt-dlp", "faster-whisper", "playwright", "av", "Pillow", "rapidocr-onnxruntime"):
            try:
                versions.append(package + " " + importlib.metadata.version(package))
            except importlib.metadata.PackageNotFoundError:
                versions.append(package + ": unavailable")
        cached = list((RUNTIME / "models" / "hub").glob("models--Systran--faster-whisper-*/snapshots/*/model.bin"))
        models = sorted({p.parents[2].name.split("faster-whisper-")[-1] for p in cached})
        self.settings_status.set("\n".join(versions + ["Cached models: " + (", ".join(models) or "none"),
                                                      "Output: " + str(self.data), "Profile: " + str(APP_HOME / "browsers"),
                                                      ]))

    def add_files(self):
        files = filedialog.askopenfilenames(title="Video files", filetypes=[("Video", "*.mp4 *.mkv *.webm *.mov *.m4v *.avi")])
        for file in files:
            self.links.insert("end", ("\n" if self.links.get("1.0", "end").strip() else "") + file)

    def add_links(self):
        sources = list(dict.fromkeys(line.strip() for line in self.links.get("1.0", "end").splitlines() if line.strip()))
        if not sources:
            self.status.set("Video links required")
            return
        if len(sources) > 100:
            self.status.set("Queue limit: 100")
            return
        # Validate the entire input before creating any jobs.
        from common import canonical_source
        try:
            sources = [canonical_source(s) for s in sources]
            for source in sources:
                job = new_job(source, self.settings, {"origin": "manual_link", "search_prompt": self.prompt.get().strip() or None,
                                                      "discovered_at": None, "discovery_session_id": None}, self.selected_project())
                self.jobs[job["id"]] = job
            self.links.delete("1.0", "end")
            self.start_queue()
        except (ValueError, OSError) as error:
            self.status.set(str(error))

    def reload_jobs(self):
        self.jobs = {p.stem: load_json(p) for p in (self.data / 'jobs').glob('*.json') if load_json(p, {}).get('status') != 'removed'}
        self.refresh_jobs()

    def recover_jobs(self):
        for file in sorted((self.data / "jobs").glob("*.json")):
            job = load_json(file)
            if not job or job.get("status") == "removed":
                continue
            if job["status"] not in TERMINAL | {"queued"}:
                # Preserve existing output and never restart an interrupted job implicitly.
                save_job(job, "interrupted", finished_at=utc())
                packet = Path(job["packet_path"])
                if packet.exists():
                    packet_manifest(job, packet, load_json(packet / "source_metadata.json", {}), None,
                                    {"full_audio_processed": False}, {"full_timeline_sampled": False}, "interrupted", ["App closed during processing"])
            self.jobs[job["id"]] = job
        self.refresh_jobs()

    def refresh_jobs(self):
        selected = self.job_tree.selection()
        self.job_tree.delete(*self.job_tree.get_children())
        for identifier, job in self.jobs.items():
            if (job.get("project_id") or "inbox") != self.selected_project():
                continue
            source = job["source"] if job["source"].startswith("https://") else Path(job["source"]).name
            self.job_tree.insert("", "end", iid=identifier, values=(source, job["status"].replace("_", " ").capitalize(), job["created_at"].replace("T", " ").replace("+00:00", " UTC")))
        for item in selected:
            if self.job_tree.exists(item):
                self.job_tree.selection_add(item)

    def start_queue(self):
        self.run_queue = True
        self.status.set("Queued")
        self.refresh_jobs()

    def cancel_selected(self):
        selected = list(self.job_tree.selection())
        if not selected and self.analysis_job:
            selected = [self.analysis_job["id"]]
        for identifier in selected:
            job = self.jobs[identifier]
            if self.analysis_job and identifier == self.analysis_job["id"]:
                (Path(job["packet_path"]) / "STOP").touch()
                self.status.set("Cancelling")
            elif job["status"] == "queued":
                save_job(job, "cancelled", finished_at=utc())
        self.refresh_jobs()

    def retry_selected(self):
        count = 0
        for identifier in self.job_tree.selection():
            old = self.jobs[identifier]
            if old["status"] not in TERMINAL - {"complete"}:
                continue
            job = new_job(old["source"], self.settings, old["provenance"], self.selected_project())
            job["retry_of"] = identifier
            job["reuse_from"] = identifier
            save_job(job)
            self.jobs[job["id"]] = job
            count += 1
        if count:
            self.start_queue()
        else:
            self.status.set("Select a failed or partial job")

    def refresh_library(self, refresh=True):
        if not hasattr(self, "packet_tree"):
            return
        entries = getattr(self, "library_entries_cache", [])
        if refresh:
            entries = rebuild_index(self.settings)
            for project in project_list(self.settings):
                for file in Path(project["path"]).glob("sessions/*/SESSION.json"):
                    session = load_json(file, {})
                    entries.append({"packet_id": "session:" + session["id"], "title": session.get("title", "Session"),
                                    "project_id": project["id"], "source": "Session", "path": str(file.parent),
                                    "search_prompt": "; ".join(session["search_prompts"]), "processed_at": session.get("started_at"),
                                    "status": session["status"]})
            self.library_entries_cache = entries
        text = self.filter.get().casefold()
        selected = self.packet_tree.selection()
        self.packet_entries = {p["packet_id"]: p for p in entries}
        self.packet_tree.delete(*self.packet_tree.get_children())
        for p in sorted(entries, key=lambda p: (not p["packet_id"].startswith("session:"), p.get("processed_at") or "")):
            if (p.get("project_id") or "inbox") != self.selected_project():
                continue
            if text and text not in json.dumps(p, ensure_ascii=False).casefold():
                continue
            parent = "session:" + p["session_id"] if p.get("session_id") and self.packet_tree.exists("session:" + p["session_id"]) else ""
            self.packet_tree.insert(parent, "end", iid=p["packet_id"], values=(p["title"] or p["packet_id"], p["search_prompt"] or "Manual link",
                                                                                (p["processed_at"] or "").replace("T", " ").replace("+00:00", " UTC"),
                                                                                p["status"] if p['packet_id'].startswith('session:') else ' / '.join(("Collected" if p['status']=='complete' else p['status'],p.get('evidence_status','not built').replace('_',' '),p.get('review_status','unreviewed')))))
        for item in selected:
            if self.packet_tree.exists(item):
                self.packet_tree.selection_add(item)

    def select_packet(self, _=None):
        selected = self.packet_tree.selection()
        if selected:
            p = self.packet_entries[selected[0]]
            self.packet_detail.set(p["source"] + "\n" + p["path"])
        else:
            self.packet_detail.set("")

    def open_folder(self, folder):
        if Path(folder).is_dir():
            open_path(folder)

    def open_selected(self):
        selected = self.packet_tree.selection()
        if selected:
            self.open_folder(self.packet_entries[selected[0]]["path"])

    def open_reading(self):
        selected = self.packet_tree.selection()
        if selected:
            packet = Path(self.packet_entries[selected[0]]['path'])
            file = packet/'evidence'/'reader-ready.html'
            status = load_json(packet/'evidence'/'status.json', {})
            if file.is_file():
                open_path(file)
                if status.get('status') not in {'ready','needs_attention'}:
                    self.status.set('Opened previous reading; latest build '+status.get('status','incomplete'))
            elif (packet/'evidence'/'reader.html').is_file() and status.get('status') in {'ready','needs_attention'}:
                open_path(packet/'evidence'/'reader.html')
            else:
                self.status.set('Build reading first' if not file.is_file() else 'Reading build incomplete')

    def build_reading(self):
        if self.analysis_process or any(p.busy() for p in self.session_processors):
            self.status.set('Wait for collection to finish')
            return
        selected = self.packet_tree.selection()
        paths = [Path(self.packet_entries[s]['path']) for s in selected if not s.startswith('session:')]
        if not paths:
            self.status.set('Select videos')
            return
        for path in paths:
            if path not in self.evidence_queue and (path != self.evidence_packet or self.evidence_stopping):
                self.evidence_queue.append(path)
        self.status.set('Reading queued')

    def stop_reading(self):
        self.evidence_queue.clear()
        if self.evidence_packet and (self.evidence_packet/'evidence').is_dir():
            self.evidence_stopping = True
            (self.evidence_packet/'evidence'/'STOP').touch()
            self.status.set('Stopping reading build')

    def copy_selected(self):
        selected = self.packet_tree.selection()
        if selected:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(self.packet_entries[s]["path"] for s in selected))

    def move_selected(self):
        choices = list(self.project_map)
        dialog = tk.Toplevel(self.root)
        dialog.title("Move to project")
        dialog.geometry("360x145")
        target = tk.StringVar(value=self.project.get())
        ttk.Combobox(dialog, textvariable=target, values=choices, state="readonly").pack(fill="x", padx=20, pady=20)
        def move():
            try:
                selected = [Path(self.packet_entries[s]["path"]) for s in self.packet_tree.selection()]
                for source in selected:
                    if not any(source != p and source.is_relative_to(p) for p in selected):
                        move_entry(self.settings, source, self.project_map[target.get()]["id"])
                self.reload_jobs()
                self.refresh_library()
                dialog.destroy()
            except (ValueError, OSError) as error:
                messagebox.showerror("Move", str(error), parent=dialog)
        ttk.Button(dialog, text="Move", command=move, style="Accent.TButton").pack()

    def remove_selected(self):
        from projects import remove_entry
        selected = [Path(self.packet_entries[s]["path"]) for s in self.packet_tree.selection()]
        try:
            for source in selected:
                if not any(source != p and source.is_relative_to(p) for p in selected):
                    remove_entry(self.settings, source)
            self.refresh_library()
            self.reload_jobs()
        except (ValueError, OSError) as error:
            messagebox.showerror("Remove", str(error))

    def undo_remove(self):
        from projects import undo_remove
        try:
            undo_remove(self.settings)
            self.refresh_library()
            self.reload_jobs()
        except (ValueError, OSError) as error:
            messagebox.showerror("Undo", str(error))

    def open_browser(self):
        platform = self.platform.get().lower()
        if self.browser and self.browser.poll() is None:
            if platform != self.browser_platform:
                self.browser_status.set("Stop browser before switching platform")
            return
        self.browser_platform = platform
        self.browser = spawn("scroller.py", ["--platform", platform], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
        process = self.browser
        self.browser_status.set("Opening")

        def receive():
            for line in process.stdout:
                try:
                    self.browser_events.put(json.loads(line))
                except ValueError:
                    pass
        threading.Thread(target=receive, daemon=True).start()

    def browser_command(self, action, **data):
        if not self.browser or self.browser.poll() is not None:
            self.browser_status.set("Open browser first")
            return
        try:
            self.browser.stdin.write(json.dumps({"action": action, **data}) + "\n")
            self.browser.stdin.flush()
        except (OSError, ValueError):
            self.browser_status.set("Browser closed")

    def start_scroll(self):
        prompts = [p.strip() for p in self.keywords.get("1.0", "end").splitlines() if p.strip()]
        if not prompts or len(prompts) > 20 or any(len(p) > 200 for p in prompts):
            self.browser_status.set("Keywords required: 1 to 20")
            return
        if not self.browser or self.browser.poll() is not None:
            self.open_browser()
        elif self.browser_platform != self.platform.get().lower():
            self.browser_status.set("Stop browser before switching platform")
            return
        self.browser_command("start", prompts=prompts, settings=self.settings, project_id=self.selected_project(), analyze=True)

    def refresh_discoveries(self):
        if not hasattr(self, "discovery_tree"):
            return
        selected = self.discovery_tree.selection()
        self.discovery_tree.delete(*self.discovery_tree.get_children())
        self.discovery_records.clear()
        for file in sorted((self.data / "discoveries").glob("*.json"), reverse=True):
            session = load_json(file, {})
            for i, record in enumerate(session.get("results", [])):
                identifier = file.stem + ":" + str(i)
                self.discovery_records[identifier] = record
                self.discovery_tree.insert("", "end", iid=identifier, values=(record["title"] or record["url"], ", ".join(record.get("search_prompts", [record["search_prompt"]])), record["platform"], record["discovered_at"].replace("T", " ").replace("+00:00", " UTC")))
        for item in selected:
            if self.discovery_tree.exists(item):
                self.discovery_tree.selection_add(item)

    def queue_discoveries(self):
        from sessions import SessionQueue
        import uuid
        selected = self.discovery_tree.selection()
        records = []
        seen = set()
        for identifier in selected:
            record = dict(self.discovery_records[identifier])
            if record["url"] not in seen and not record.get("job_id"):
                records.append(record)
                seen.add(record["url"])
        if records:
            session = {"id": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8],
                       "title": "Selected videos " + utc(), "project_id": self.selected_project(), "settings": self.settings,
                       "platform": "mixed", "search_prompts": list(dict.fromkeys(r["search_prompt"] for r in records)),
                       "started_at": utc(), "status": "processing", "results": records}
            processor = SessionQueue(session)
            processor.enqueue()
            self.session_processors.append(processor)
            self.reload_jobs()
            self.show("Library")

    def tick(self):
        try:
            self.tick_workers()
        except (OSError, ValueError) as error:
            self.status.set(type(error).__name__)
        self.root.after(400, self.tick)

    def tick_workers(self):
        if self.evidence_process:
            status = load_json(self.evidence_packet/'evidence'/'status.json', {})
            self.status.set('Reading: ' + (str(status.get('completed_screens',0))+' / '+str(status.get('total_screens','?')) if status.get('status')=='building' else status.get('status','starting').replace('_',' ')))
            if self.evidence_process.poll() is not None:
                if status.get('status')=='building':
                    atomic_json(self.evidence_packet/'evidence'/'status.json', {'status':'interrupted'})
                self.evidence_log.close()
                self.evidence_process = self.evidence_log = self.evidence_packet = None
                self.refresh_library()
        if self.evidence_queue and not self.evidence_process and not self.analysis_process:
            self.evidence_packet = self.evidence_queue.pop(0)
            self.evidence_stopping = False
            if not (self.evidence_packet/'packet.json').is_file():
                self.evidence_packet = None
                self.status.set('Queued video unavailable')
                return
            folder = self.evidence_packet/'evidence'
            folder.mkdir(exist_ok=True)
            (folder/'STOP').unlink(missing_ok=True)
            self.evidence_log = (folder/'build.private.log').open('wb')
            self.evidence_process = spawn('evidence.py', ['build','--packet',self.evidence_packet,'--refresh-media'],stdout=self.evidence_log,stderr=self.evidence_log)
        for processor in list(self.session_processors):
            if not processor.tick():
                processor.session["status"] = processor.outcome()
                processor.session["finished_at"] = utc()
                processor.save()
                self.session_processors.remove(processor)
                self.reload_jobs()
                self.refresh_library()
        if self.analysis_process:
            path = self.data / "jobs" / (self.analysis_job["id"] + ".json")
            job = load_json(path, self.analysis_job)
            self.jobs[job["id"]] = job
            self.progress["value"] = job.get("progress", 0)
            self.status.set(job["status"].replace("_", " ").capitalize() + (": " + job["detail"] if job.get("detail") else ""))
            signature = (job["id"], job["status"], job.get("detail"))
            if signature != self.last_job_signature:
                self.refresh_jobs()
                self.last_job_signature = signature
            if self.analysis_process.poll() is not None:
                if job["status"] not in TERMINAL:
                    save_job(job, "interrupted", finished_at=utc())
                self.analysis_log.close()
                self.analysis_process = self.analysis_job = self.analysis_log = None
                self.refresh_library()
        if self.run_queue and not self.analysis_process and not self.evidence_process and not self.evidence_queue:
            job = next((j for j in self.jobs.values() if j["status"] == "queued" and not j.get("session_id")), None)
            if job:
                self.analysis_job = job
                self.analysis_log = (self.data / "jobs" / (job["id"] + ".private.log")).open("wb")
                self.analysis_process = spawn("analyzer.py", ["run", "--job", self.data / "jobs" / (job["id"] + ".json")], stdout=self.analysis_log, stderr=self.analysis_log)
            else:
                self.run_queue = False
        while not self.browser_events.empty():
            event = self.browser_events.get_nowait()
            if event["event"] == "status":
                self.browser_status.set(event["status"])
                if event.get("attention"):
                    self.root.bell()
                    self.status.set(event["status"])
                    self.notice.set((self.browser_platform or "Browser").capitalize() + "\n" + event["status"])
                    self.root.title("Video Workbench | " + event["status"])
                elif event["status"] in {"Browser closed", "Browser ready", "Ready to resume", "Running"}:
                    self.notice.set("")
                    self.root.title("Video Workbench")
            if event["event"] in {"results", "session"}:
                if time.monotonic() - self.last_discovery_refresh > 1:
                    self.reload_jobs()
                    self.refresh_discoveries()
                    self.last_discovery_refresh = time.monotonic()

    def close(self):
        self.run_queue = False
        self.stop_reading()
        for processor in self.session_processors:
            processor.stop()
            processor.session["status"] = "stopped"
            processor.save()
        if self.analysis_job:
            packet = Path(self.analysis_job["packet_path"])
            packet.mkdir(parents=True, exist_ok=True)
            (packet / "STOP").touch()
        if self.browser and self.browser.poll() is None:
            self.browser_command("stop")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(p and p.poll() is None for p in (self.analysis_process, self.browser, self.evidence_process)):
            time.sleep(.1)
        for process in (self.analysis_process, self.browser, self.evidence_process):
            if process and process.poll() is None:
                stop_process(process)
        if self.analysis_log:
            self.analysis_log.close()
        if self.evidence_log:
            self.evidence_log.close()
        if self.evidence_packet and (self.evidence_packet/'packet.json').is_file():
            path=self.evidence_packet/'evidence'/'status.json'
            if load_json(path,{}).get('status')=='building':
                atomic_json(path,{'status':'interrupted','at':utc()})
                from evidence import update_manifest
                update_manifest(self.evidence_packet)
        self.root.destroy()


def main():
    if os.name == "nt":
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    root = tk.Tk()
    try:
        with exclusive(APP_HOME / ".app.lock"):
            Workbench(root)
            root.mainloop()
    except LockBusy:
        root.withdraw()
        messagebox.showinfo("Video Workbench", "Already open")
        root.destroy()


def run_packaged_worker():
    if not getattr(sys, "frozen", False) or len(sys.argv) < 3 or sys.argv[1] != "--video-workbench-worker":
        return False
    scripts = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "scripts"
    script = scripts / sys.argv[2]
    if not script.is_file() or script.parent != scripts:
        raise SystemExit("Bundled worker unavailable")
    sys.path.insert(0, str(scripts))
    sys.argv = [str(script), *sys.argv[3:]]
    if sys.stdout is None:
        sys.stdout = os.fdopen(1, "w", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = os.fdopen(2, "w", encoding="utf-8", buffering=1)
    runpy.run_path(str(script), run_name="__main__")
    return True


if __name__ == "__main__":
    if not run_packaged_worker():
        main()
