"""Video Workbench desktop UI. This program has no Codex integration."""
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk
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
        self.model_download_busy = False
        self.closing = False
        self.evidence_process = None
        self.evidence_log = None
        self.evidence_packet = None
        self.evidence_queue = []
        self.evidence_stopping = False
        self.run_queue = False
        self.queue_run_ids = []
        self.login_process = None
        self.login_log = None
        self.login_log_path = None
        self.login_platform = None
        self.jobs = {}
        self.pages = {}
        self.active_view = "Analyzer"
        self.nav_buttons = {}
        self.status = tk.StringVar(value="Ready")
        self.notice = tk.StringVar(value="")
        self.project = tk.StringVar(value="Inbox")
        self.project_boxes = []
        self.refresh_projects()
        self.last_job_signature = None
        self.configure_style()
        self.build_shell()
        self.build_analyzer()
        self.build_library()
        self.build_settings()
        self.recover_jobs()
        self.refresh_library()
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
        for name in ("Analyzer", "Library", "Settings"):
            button = tk.Button(side, text=name, font=(self.ui_font, 11), anchor="w", relief="flat", bd=0,
                               bg=SIDEBAR, fg=MUTED, activebackground=BG, padx=18, pady=13,
                               command=lambda n=name: self.show(n), cursor="hand2")
            button.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[name] = button
        tk.Label(side, textvariable=self.notice, bg=SIDEBAR, fg=MUTED, font=(self.ui_font, 9),
                 wraplength=180, justify="left").pack(side="bottom", anchor="w", padx=25, pady=28)
        self.main = ttk.Frame(self.root, padding=(34, 28, 34, 18))
        self.main.pack(side="left", fill="both", expand=True)
        for name in ("Analyzer", "Library", "Settings"):
            self.pages[name] = ttk.Frame(self.main)

    def show(self, name):
        self.active_view = name
        for title, frame in self.pages.items():
            frame.pack_forget()
            self.nav_buttons[title].configure(bg=BG if title == name else SIDEBAR, fg=TEXT if title == name else MUTED)
        self.pages[name].pack(fill="both", expand=True)
        if name == "Library":
            self.refresh_library()

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
        current = getattr(self, "project_dialog", None)
        if current is not None and current.winfo_exists():
            current.lift()
            current.focus_force()
            return
        dialog = tk.Toplevel(self.root)
        self.project_dialog = dialog
        dialog.title("New project")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=22)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Project name").pack(anchor="w", pady=(0, 8))
        name = tk.StringVar()
        entry = ttk.Entry(body, textvariable=name, width=36)
        entry.pack(fill="x")
        error = tk.StringVar(value="")
        ttk.Label(body, textvariable=error, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(18, 0))

        def create():
            try:
                folder = create_project(self.settings, name.get())
                self.refresh_projects()
                self.project.set(next(k for k, p in self.project_map.items() if p["id"] == folder.name))
            except (ValueError, OSError) as exception:
                error.set(str(exception))
                return
            dialog.destroy()
            try:
                self.refresh_library()
                self.refresh_jobs()
                self.status.set("Project created")
            except (ValueError, OSError) as exception:
                self.status.set("Project created. Library refresh failed: " + str(exception))

        ttk.Button(actions, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(actions, text="Create", command=create, style="Accent.TButton").pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _: create())
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - dialog.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.grab_set()
        entry.focus_set()

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
        self.label(page, "Video link")
        input_row = ttk.Frame(page)
        input_row.pack(fill="x")
        self.link = tk.StringVar()
        self.link_entry = ttk.Entry(input_row, textvariable=self.link)
        self.link_entry.pack(side="left", fill="x", expand=True)
        self.link_entry.bind("<Return>", lambda _: self.add_link())
        ttk.Button(input_row, text="Add to queue", command=self.add_link).pack(side="left", padx=(9, 0))
        actions = ttk.Frame(page)
        actions.pack(fill="x", pady=(12, 22))
        ttk.Button(actions, text="Add files", command=self.add_files).pack(side="left")
        self.analyze_button = ttk.Button(actions, text="Analyze", style="Accent.TButton", command=self.start_queue)
        self.analyze_button.pack(side="right")
        self.label(page, "Queue")
        self.job_tree = self.table(page, [("source", "Video", 420), ("status", "Status", 145), ("date", "Added", 190)], 7)
        self.job_tree.bind("<<TreeviewSelect>>", self.select_job)
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
        self.packet_tree = self.table(page, [("title", "Title", 300), ("date", "Processed", 170),
                                             ("status", "Collection / Reading / Review", 290)], 6)
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
                  ("max_packet_mib", "Packet limit (MiB)"), ("job_timeout_minutes", "Processing limit (minutes)")]
        for i, (key, label) in enumerate(fields):
            row = ttk.Frame(form)
            row.pack(fill="x", pady=6)
            ttk.Label(row, text=label, width=27).pack(side="left")
            var = tk.StringVar(value=str(self.settings[key]))
            self.setting_vars[key] = var
            if key == "model":
                ttk.Combobox(row, textvariable=var, values=["tiny", "base", "small", "medium"], state="readonly", width=23).pack(side="left")
                self.model_download_button = ttk.Button(row, text="Cache model", command=self.cache_selected_model)
                self.model_download_button.pack(side="left", padx=8)
            elif key == 'ocr_engine':
                ocr_options = ['windows', 'rapidocr'] if os.name == 'nt' else ['rapidocr']
                ttk.Combobox(row, textvariable=var, values=ocr_options, state='readonly', width=23).pack(side='left')
            else:
                ttk.Entry(row, textvariable=var, width=44 if key == "output_dir" else 25).pack(side="left", fill="x", expand=key == "output_dir")
            if key == "output_dir":
                ttk.Button(row, text="Browse", command=self.browse_output).pack(side="right", padx=8)

        self.login_frame = ttk.LabelFrame(form, text="Video site access", padding=12)
        ttk.Label(self.login_frame, text="Sign in with the app browser, then close it and retry the video.",
                  style="Muted.TLabel", wraplength=760).pack(anchor="w")
        login_row = ttk.Frame(self.login_frame)
        login_row.pack(fill="x", pady=(8, 0))
        self.login_button = ttk.Button(login_row, text="Open sign-in browser", command=self.open_site_login)
        self.login_button.pack(side="left")
        self.login_status = tk.StringVar(value="")
        ttk.Label(login_row, textvariable=self.login_status, style="Muted.TLabel", wraplength=520).pack(side="left", padx=10)

        links = ttk.LabelFrame(form, text="Obsidian and Codex", padding=12)
        self.settings_links = links
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
        if self.analysis_process or self.evidence_process or (self.login_process and self.login_process.poll() is None):
            messagebox.showinfo("Settings", "Stop active jobs and sign-in browser first")
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
                                                      "Output: " + str(self.data), "Sign-in profile: " + str(APP_HOME / "login-profiles"),
                                                      ]))

    def cache_selected_model(self):
        if self.model_download_busy:
            self.settings_status.set("A model download is already running")
            return
        if self.analysis_process or self.run_queue:
            self.settings_status.set("Stop active analysis before caching a model")
            return
        model = self.setting_vars["model"].get()
        self.model_download_busy = True
        self.model_download_button.configure(state="disabled")
        self.settings_status.set("Caching " + model.title() + " model. This needs an internet connection.")

        def download():
            try:
                os.environ.pop("HF_HUB_OFFLINE", None)
                os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
                os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
                os.environ["DO_NOT_TRACK"] = "1"
                from faster_whisper.utils import download_model
                cache = str(RUNTIME / "models" / "hub")
                try:
                    download_model(model, cache_dir=cache, local_files_only=True)
                except Exception:
                    download_model(model, cache_dir=cache, local_files_only=False)
                result = model.title() + " model is ready for offline use"
            except Exception as error:
                result = "Model download failed: " + type(error).__name__

            def finish():
                self.model_download_busy = False
                self.model_download_button.configure(state="normal")
                self.settings_status.set(result)

            if not self.closing:
                try:
                    self.root.after(0, finish)
                except tk.TclError:
                    pass

        threading.Thread(target=download, name="video-workbench-model-cache", daemon=True).start()

    def add_files(self):
        files = filedialog.askopenfilenames(title="Video files", filetypes=[("Video", "*.mp4 *.mkv *.webm *.mov *.m4v *.avi")])
        if not files:
            return
        queued = sum(1 for job in self.jobs.values()
                     if (job.get("project_id") or "inbox") == self.selected_project() and job.get("status") == "queued")
        if queued + len(files) > 100:
            self.status.set("Queue limit: 100")
            return
        added = 0
        failure = None
        for source in files:
            try:
                job = new_job(source, self.settings, project_id=self.selected_project())
                self.jobs[job["id"]] = job
                added += 1
            except (ValueError, OSError) as error:
                failure = str(error)
                break
        if added:
            self.refresh_jobs()
        if failure:
            prefix = f"Added {added} file" + ("" if added == 1 else "s") + ". " if added else ""
            self.status.set(prefix + "Could not add the remaining files: " + failure)
        elif added:
            self.status.set(f"Added {added} file" + ("" if added == 1 else "s") + " to queue")

    def add_link(self):
        source = self.link.get().strip()
        if not source:
            self.status.set("Enter a video link")
            return
        queued = sum(1 for job in self.jobs.values()
                     if (job.get("project_id") or "inbox") == self.selected_project() and job.get("status") == "queued")
        if queued >= 100:
            self.status.set("Queue limit: 100")
            return
        try:
            job = new_job(source, self.settings, project_id=self.selected_project())
        except (ValueError, OSError) as error:
            self.status.set(str(error))
            return
        self.jobs[job["id"]] = job
        self.link.set("")
        self.refresh_jobs()
        self.status.set("Added to queue")

    def reload_jobs(self):
        jobs = {}
        for path in (self.data / "jobs").glob("*.json"):
            job = load_json(path)
            if job and job.get("status") != "removed":
                jobs[path.stem] = job
        self.jobs = jobs
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
        self.refresh_login_access()

    def refresh_jobs(self):
        selected = self.job_tree.selection()
        self.job_tree.delete(*self.job_tree.get_children())
        project_jobs = [(identifier, job) for identifier, job in self.jobs.items()
                        if (job.get("project_id") or "inbox") == self.selected_project()]
        order = lambda item: (str(item[1].get("queue_order", item[1].get("created_at", ""))), item[0])
        pending = sorted((item for item in project_jobs if item[1].get("status") not in TERMINAL), key=order)
        history = sorted((item for item in project_jobs if item[1].get("status") in TERMINAL), key=order, reverse=True)[:20]
        for identifier, job in pending + history:
            source = job["source"] if job["source"].startswith("https://") else Path(job["source"]).name
            self.job_tree.insert("", "end", iid=identifier, values=(source, job["status"].replace("_", " ").capitalize(), job["created_at"].replace("T", " ").replace("+00:00", " UTC")))
        for item in selected:
            if self.job_tree.exists(item):
                self.job_tree.selection_add(item)
        self.refresh_login_access()

    def refresh_login_access(self):
        failed = sorted((job for job in self.jobs.values()
                         if job.get("error") == "sign_in_required" or job.get("status") == "sign_in_required"),
                        key=lambda job: job.get("created_at", ""), reverse=True)
        platform = None
        if failed:
            source = failed[0].get("source", "")
            platform = "youtube" if "youtube" in source else "tiktok"
        self.login_platform = platform
        if not hasattr(self, "login_frame"):
            return
        if platform:
            if not self.login_frame.winfo_manager():
                self.login_frame.pack(fill="x", pady=(12, 8), before=self.settings_links)
            self.login_button.configure(text="Open " + platform.title() + " sign-in")
        else:
            self.login_frame.pack_forget()

    def open_site_login(self):
        if not self.login_platform:
            return
        if self.login_process and self.login_process.poll() is None:
            self.login_status.set("Sign-in browser is open")
            return
        try:
            APP_HOME.mkdir(parents=True, exist_ok=True)
            self.login_log_path = APP_HOME / (self.login_platform + "-sign-in.private.log")
            self.login_log = self.login_log_path.open("wb")
            self.login_process = spawn("site_login.py", ["--platform", self.login_platform],
                                      stdout=self.login_log, stderr=self.login_log)
            self.login_status.set("Preparing sign-in browser")
        except (OSError, ValueError) as error:
            if self.login_log:
                self.login_log.close()
                self.login_log = None
            self.login_status.set("Could not open browser: " + str(error))

    def select_job(self, _=None):
        selected = self.job_tree.selection()
        if not selected:
            return
        job = self.jobs.get(selected[0], {})
        detail = job.get("detail") or job.get("error") or job.get("status", "").replace("_", " ").capitalize()
        self.status.set(detail)

    def start_queue(self):
        if self.model_download_busy:
            self.status.set("Wait for model caching to finish")
            return
        if self.login_process and self.login_process.poll() is None:
            self.status.set("Close the sign-in browser before analyzing")
            return
        if self.run_queue:
            self.status.set("Analysis is already running")
            return
        project_id = self.selected_project()
        self.queue_run_ids = [identifier for identifier, job in sorted(
            self.jobs.items(), key=lambda item: (str(item[1].get("queue_order", item[1].get("created_at", ""))), item[0]))
            if job.get("status") == "queued" and (job.get("project_id") or "inbox") == project_id]
        if not self.queue_run_ids:
            self.status.set("Queue is empty")
            return
        self.run_queue = True
        self.analyze_button.configure(state="disabled")
        self.status.set(f"Analyzing {len(self.queue_run_ids)} video" + ("" if len(self.queue_run_ids) == 1 else "s"))
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
        remaining = 100 - sum(1 for job in self.jobs.values()
                              if (job.get("project_id") or "inbox") == self.selected_project()
                              and job.get("status") == "queued")
        for identifier in self.job_tree.selection():
            if remaining <= 0:
                break
            old = self.jobs[identifier]
            if old["status"] not in TERMINAL - {"complete"}:
                continue
            job = new_job(old["source"], self.settings, old["provenance"], self.selected_project())
            job["retry_of"] = identifier
            job["reuse_from"] = identifier
            save_job(job)
            self.jobs[job["id"]] = job
            count += 1
            remaining -= 1
        if count:
            self.status.set(f"Requeued {count} video" + ("" if count == 1 else "s"))
            self.refresh_jobs()
        elif remaining <= 0:
            self.status.set("Queue limit: 100")
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
            self.packet_tree.insert(parent, "end", iid=p["packet_id"], values=(p["title"] or p["packet_id"],
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
        if self.analysis_process or self.run_queue:
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

    def tick(self):
        try:
            self.tick_workers()
        except (OSError, ValueError) as error:
            self.status.set(type(error).__name__)
        self.root.after(400, self.tick)

    def tick_workers(self):
        if self.login_process and self.login_process.poll() is not None:
            self.login_process = None
            if self.login_log:
                self.login_log.close()
                self.login_log = None
            output = self.login_log_path.read_text(encoding="utf-8", errors="replace").strip() if self.login_log_path and self.login_log_path.is_file() else ""
            if output:
                self.login_status.set("Sign-in browser error: " + output.splitlines()[-1][:400])
            else:
                self.login_status.set("Browser closed. Retry the video.")
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
                if job.get("status") == "sign_in_required":
                    self.run_queue = False
                    self.queue_run_ids.clear()
                    self.analyze_button.configure(state="normal")
                    self.status.set("Sign-in required. Open Settings.")
                self.refresh_library()
        if self.run_queue and not self.analysis_process and not self.evidence_process and not self.evidence_queue:
            while self.queue_run_ids:
                identifier = self.queue_run_ids.pop(0)
                job = self.jobs.get(identifier)
                if not job:
                    continue
                job = load_json(self.data / "jobs" / (identifier + ".json"), job)
                self.jobs[identifier] = job
                if job.get("status") != "queued":
                    continue
                log = None
                try:
                    log = (self.data / "jobs" / (identifier + ".private.log")).open("wb")
                    process = spawn("analyzer.py", ["run", "--job", self.data / "jobs" / (identifier + ".json")], stdout=log, stderr=log)
                except (OSError, ValueError, RuntimeError) as error:
                    if log:
                        log.close()
                    detail = "Could not start analysis: " + str(error)[:400]
                    save_job(job, "failed", error="failed", detail=detail, finished_at=utc())
                    self.jobs[identifier] = job
                    continue
                self.analysis_job = job
                self.analysis_log = log
                self.analysis_process = process
                break
            if not self.analysis_process and not self.queue_run_ids:
                self.run_queue = False
                self.analyze_button.configure(state="normal")
                self.status.set("Analysis finished")
    def close(self):
        self.closing = True
        self.run_queue = False
        self.queue_run_ids.clear()
        self.stop_reading()
        if self.analysis_job:
            packet = Path(self.analysis_job["packet_path"])
            packet.mkdir(parents=True, exist_ok=True)
            (packet / "STOP").touch()
        processes = (self.analysis_process, self.login_process, self.evidence_process)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(p and p.poll() is None for p in processes):
            time.sleep(.1)
        for process in processes:
            if process and process.poll() is None:
                stop_process(process)
        if self.login_log:
            self.login_log.close()
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
