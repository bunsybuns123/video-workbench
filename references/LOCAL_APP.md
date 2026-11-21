# Video Workbench

## macOS Sequoia on Apple Silicon

The public Mac download is a drag-to-Applications `.dmg` built on an Apple Silicon runner. On first open, the app starts a Terminal setup. It uses an existing Python 3.12 installation with Tk when available. Otherwise it uses Homebrew to install `python@3.12` and `python-tk@3.12`, then creates `~/.codex/video-research-runtime/venv`, installs `requirements.txt` and installs the Playwright Chromium browser. If Homebrew is not installed, set it up from [brew.sh](https://brew.sh), then open the app again. The setup asks whether to install the optional Codex skill. Settings can install or update it later.

The Mac app defaults to three CPU threads and RapidOCR. In Settings, choose a speech model and press **Cache model** while online. The model is stored in the local runtime and is then available offline. Choose Tiny or Base for an 8 GB Mac. Settings stores app data in `~/Library/Application Support/VideoWorkbench`; the runtime and model cache remain in `~/.codex/video-research-runtime`.

To use Obsidian, choose the vault in Settings and press **Use vault for library**, then **Save**. New project folders and Markdown packets are created under `<vault>/Video Workbench`. This does not move existing library data. To use Codex, install the skill in Settings to `~/.agents/skills/video-rabbit-holes`, start a new chat, select a project in Library and copy its path. The user decides when to ask Codex to read that project.

This app is not signed or notarized with an Apple Developer identity. macOS asks for a one-time Control-click and **Open** confirmation. The setup installs packages and a browser from their package sources. It does not import personal browser profiles or send packets to Codex.

Run scripts/launch.py with Python, or use the Video Workbench desktop app. The Codex skill installs to `~/.agents/skills/video-rabbit-holes` on macOS and `~/.codex/skills/video-rabbit-holes` on Windows. `scripts/install.py` preserves a backup when updating an existing skill. Runtime dependencies and speech models live in `~/.codex/video-research-runtime`; `VIDEO_WORKBENCH_RUNTIME` can select another runtime. Install `requirements.txt` with that runtime's Python. FFmpeg can be provided in its `ffmpeg/bin` folder, on `PATH`, or by the `imageio-ffmpeg` package. Install the matching browser once using `python -m playwright install chromium`. Cache speech models deliberately before selecting them; missing models are reported without a fallback.

## Projects

Choose a project in Analyzer, Library or Scroller. New project creates a named folder. Library shows a project's individual packets and expandable session groups. Move transfers selected packets or whole sessions to another project and preserves provenance. Active items cannot move. Project folder opens its directory; Copy project path copies the directory for a later review request. Old loose packets are assigned to Inbox during migration.

Remove or the Delete key hides selected items immediately. Undo restores the most recent removal. Removed items are retained under .trash and excluded from indexes. This does not free their disk space. No permanent-delete command is installed.

```text
Video Workbench/
  projects/<name-id>/
    project.json
    index.json
    PROJECT.md
    packets/<packet-id>/
      PACKET.md
      packet.json
      source/
      audio/
      visuals/
    sessions/<session-id>/
      SESSION.md
      SESSION.json
      packets/<packet-id>/
  jobs/
  discoveries/
  .trash/
```

A packet contains source metadata, original media, available captions, a timestamped transcript with word confidence, extracted audio, one-second audio levels, full-timeline sampled frames and ordered contact sheets. Manifests record exact search prompts, source links, origin, discovery time, processing time, settings, warnings and coverage. All wall times use ISO 8601 UTC. Media timestamps are seconds within the video. Complete means successful extraction with recognized speech and full visual sampling, not verified claims or perfect recognition.

## Scroller

Keywords are one per line. Start creates a session in the selected project and processes each unique video separately. The next scroll waits for the current batch to finish, avoiding an unbounded backlog. Pause suspends discovery and starting further video jobs; an in-flight video may finish. Resume continues. Stop cancels unfinished jobs and retains evidence and failure records. Sessions report complete, partial, empty or a stopping reason, rather than treating blocked videos as successful research. Scope is bounded by keyword scrolls, elapsed discovery time and result count in Settings. Verification and sign-in gates require human action. Idle browser windows close after 30 minutes.

Browser access and public downloading are separate. Signing into the app-owned browser does not export account access to the downloader. Optional caption errors cannot abort a media download. Publisher captions and original automatic captions are used; translated YouTube captions are skipped. HTTP 429 media errors are distinguished from sign-in requirements. No access retry loop is installed.

## Processing and settings

Retry creates a fresh attempt, preserves the original and reuses its downloaded source. Complete visual samples are reused when frame settings match. Actual speech exceptions are retained in audio/diagnostics.txt and exposed in job details. Transcript progress writes and UI refreshes are throttled. Frame extraction avoids upscaling. Storage checks scan at two-second intervals; cancellation checks remain at 300 ms. These are application bounds, not OS quotas.

Settings live in the platform's application-data folder and browser profiles in its `browsers/` subfolder. On macOS, this is `~/Library/Application Support/VideoWorkbench`; on Windows, `%LOCALAPPDATA%/VideoWorkbench`. `VIDEO_WORKBENCH_HOME` relocates these. Changing output folder selects a library without moving the previous one. Settings apply to new jobs. Model options are Tiny, Base, Small and Medium; CPU/int8 processing uses the selected thread count. The Mac setup downloads no speech model in advance.

CLI: scripts/analyzer.py add --source <link-or-file> --prompt <optional-query> creates an Inbox job. scripts/analyzer.py run --job <job-file> processes it. A STOP file in its packet directory cancels. Opening the app does not automatically run old queued jobs.

## Reading evidence

New packets build an `evidence/` folder automatically. In Library, select a video and use Open reading. Build reading upgrades selected existing videos, including a bounded attempt to fetch higher-resolution media. Original media and transcripts are retained. Stop reading build cancels pending builds and stops the current build at its next checkpoint. New collection and reading builds share the processing lock. Move and Remove reject active reading builds.

Downloads now select separate video and audio streams when available, targeting the configured download height (default 1080). The old combined-stream selector commonly returned 360p. The actual source dimensions are recorded; a 1080p preference is not a guarantee. The bounded audit samples remain available, while reading images are decoded directly from the source at native resolution. Overlapping crops are at most 1000 pixels wide/high. They preserve source pixels and permit readable inspection without shrinking an entire screen into a thumbnail. Upscaling during OCR is preprocessing and does not restore source detail.

Windows OCR is the default engine on Windows. It uses the configured installed recognition language (default en-US). Windows reports no per-line confidence; confidence is null and all such output remains unverified. RapidOCR is a selectable alternative using its bundled models, with OCR thread controls and confidence triage. Screen text language is independent of speech language. This installation has English and Finnish Windows OCR languages; other languages need an installed Windows OCR language pack or the alternative engine. No language pack is installed automatically.

The reader starts with screen evidence and provides optional video/audio playback, transcript navigation, search over speech and OCR, source captions alongside overlapping speech when available, full-size images and native crops. Clicking speech seeks the player; playback selects the corresponding screen and highlights speech. Captions and ASR are independent unverified tracks, not automatically corrected ground truth. Speech vocabulary can help future transcriptions with domain terms; it is a hint, not a correctness guarantee. Low-confidence ASR and audio-energy gaps without text are listed for checking.

Screen representatives are selected by whole-screen and local-region changes, with a minimum change interval and a maximum periodic anchor interval. Every representative retains its actual decoded PTS and the sampled map remains intact. This compresses repeated views but can miss brief changes, text inside moving imagery, or changes smaller than the threshold. It is sampled evidence, not every-frame coverage. Important numbers and column relationships require image checks.

`evidence/alignment.json` contains the complete transcript with overlapping screen IDs, original ASR intervals, media intervals, nearby sample paths and precise timing offsets. `audio-alignment.json` compares the reference waveform with the reader's source at three landmarks. A consistent matched offset is applied to the media intervals and playback; raw ASR timestamps are retained. A mismatch or inconclusive check produces needs_attention. This is a timing check, not speech verification or proof of whole-track identity. `transcript-quality.json`, `captions.json`, `screens.json`, `timeline.md`, `pages/` and `READ.md` provide a navigable Codex handoff.

Library shows collection, reading readiness and research review separately. Ready means the reading artifacts were produced without recorded coverage/alignment/resolution warnings. It does not verify OCR, ASR or source claims. Low-resolution media, acquisition failures, missing transcripts or alignment problems remain needs_attention; failed builds retain diagnostics and reusable per-frame OCR caches. No successful build marks research reviewed.

```powershell
python scripts/evidence.py build --packet '<packet-folder>' --refresh-media
python scripts/evidence.py project --project '<project-folder>' --refresh-media
python scripts/evidence.py review --packet '<packet-folder>' --ids screen-00001 screen-00002 --reviewer Codex --transcript --visual
```

The review command is an explicit record of already inspected speech and native crops. Never use it to mark uninspected material reviewed. `review.json` records screen IDs, reviewer and time, and is bound to the evidence fingerprint. Changed source, recognition settings, selected timeline, captions or transcript invalidate the previous review. Whole-video claims require every screen interval inspected and any missing or illegible evidence reported. Overview mosaics cannot substitute for readable image inspection.

## Dependency references

Primary API and license references: [yt-dlp](https://github.com/yt-dlp/yt-dlp), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Playwright](https://playwright.dev/python/docs/api/class-browsertype). PyAV 18.1.0 is retained for compatibility with faster-whisper 1.2.1. The optional-caption hook is checked against the pinned yt-dlp version. Run tests/test_workbench.py --integration using the runtime Python. Fixtures are synthetic and use temporary libraries. See VALIDATION.md for tested behavior and limits.
