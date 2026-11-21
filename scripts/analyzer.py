"""Deterministic video packaging. No LLM, agent, telemetry, or remote inference."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import wave
import traceback
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
from common import (APP_HOME, atomic_json, binary, canonical_source, clock, data_dirs, digest,
                    exclusive, folder_size, load_json, LockBusy, new_job, RUNTIME, settings_load, spawn,
                    stop_process, utc, validate_settings, worker_env)


def save_job(job, status=None, **changes):
    if status:
        job["status"] = status
    job.update(changes)
    atomic_json(Path(job["settings"]["output_dir"]) / "jobs" / (job["id"] + ".json"), job)


_progress_updates = {}


def progress(job, stage, percent):
    previous = _progress_updates.get(job["id"])
    now = time.monotonic()
    if previous and previous[0] == stage and abs(previous[1] - percent) < 1 and now - previous[2] < 1:
        return
    _progress_updates[job["id"]] = (stage, percent, now)
    save_job(job, stage, progress=round(percent, 1))


def run_media(args, timeout=120):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("Media processing failed: " + result.stderr.decode("utf-8", errors="replace")[-1500:])
    return result.stdout


def error_label(error):
    text = str(error).lower()
    if "429" in text or "too many requests" in text:
        return "rate_limited"
    if any(x in text for x in ("sign in", "log in", "login", "authentication", "private video", "members-only")):
        return "sign_in_required"
    if any(x in text for x in ("captcha", "not a bot", "verify", "403", "blocked")):
        return "access_blocked"
    if "offline mode" in text or "local cache" in text or "cached" in text or "model unavailable" in text:
        return "model_unavailable"
    return "failed"


def safe_error_detail(error):
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", str(error))
    return re.sub(r"\s+", " ", text).strip()[:600]


class Quiet:
    def debug(self, message): pass
    def info(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass


class OptionalCaptions(YoutubeDL):
    """A caption failure must not abort media acquisition."""
    def __init__(self, options):
        super().__init__(options)
        self.caption_warnings = []

    def _write_subtitles(self, info_dict, filename):
        # Narrow override of the installed, pinned yt-dlp subtitle hook.
        # Media errors retain yt-dlp's normal fatal behavior.
        try:
            return super()._write_subtitles(info_dict, filename)
        except DownloadError as error:
            self.caption_warnings.append("Optional captions unavailable: " + error_label(error))
            return []


def acquire(job, packet):
    source = job["source"]
    settings = job["settings"]
    target = packet / "source"
    target.mkdir()
    previous = reuse_job(job)
    if previous and previous['settings'].get('download_height') == settings['download_height']:
        old = Path(previous["packet_path"])
        videos = [p for p in (old / "source").glob("video.*") if p.suffix in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}]
        if len(videos) == 1 and videos[0].stat().st_size <= settings["max_download_mib"] * 1048576:
            shutil.copytree(old / "source", target, dirs_exist_ok=True, copy_function=link_or_copy)
            metadata = load_json(old / "source_metadata.json", {})
            metadata["reused_from"] = previous["id"]
            return target / videos[0].name, metadata
    if not source.startswith("https://"):
        original = Path(source)
        if original.stat().st_size > settings["max_download_mib"] * 1048576:
            raise ValueError("Source size limit")
        file = target / ("video" + original.suffix.lower())
        shutil.copyfile(original, file)
        # Explicit local sidecars only. No browser session or downloader config.
        for suffix in (".vtt", ".srt"):
            sidecar = original.with_suffix(suffix)
            if sidecar.is_file() and sidecar.stat().st_size <= 10 * 1048576:
                shutil.copyfile(sidecar, target / ("captions" + suffix))
        return file, {"title": original.stem, "platform": "local", "uploader": None}
    limit = settings["max_download_mib"] * 1048576
    download_started = time.monotonic()

    def cap(state):
        if job.get('cancel_file') and Path(job['cancel_file']).exists():
            raise InterruptedError('Download cancelled')
        if time.monotonic()-download_started > settings['job_timeout_minutes']*60:
            raise TimeoutError('Download time limit')
        if state.get("downloaded_bytes", 0) > limit:
            raise RuntimeError("Download size limit")
        if state.get("status") == "downloading":
            total = state.get("total_bytes") or state.get("total_bytes_estimate")
            if total:
                fraction = min(1.0, state.get("downloaded_bytes", 0) / total)
                progress(job, "downloading", 5 + 14 * fraction)
        elif state.get("status") == "finished":
            progress(job, "downloading", 19)

    platform = "youtube" if "youtube.com" in source else "tiktok"
    options = download_options(source, target, settings, cap)
    with OptionalCaptions(options) as downloader:
        info = downloader.extract_info(source, download=False)
        duration = info.get("duration") if info else None
        if not info or info.get("_type") in {"playlist", "multi_video"} or info.get("is_live"):
            raise ValueError("Single recorded video required")
        if not isinstance(duration, (float, int)) or not 0 < duration <= settings["max_duration_minutes"] * 60:
            raise ValueError("Source duration limit or unavailable duration")
        # Retain descriptive metadata only. Signed CDN URLs and headers stay out of packets.
        metadata = {key: info.get(key) for key in ("id", "title", "uploader", "description", "upload_date", "duration", "language")}
        metadata["platform"] = platform
        metadata['download_height_requested'] = settings['download_height']
        resolved = info.get("webpage_url")
        if resolved:
            metadata["resolved_url"] = canonical_source(resolved, local=False)
        metadata["captions"] = [{"language": lang, "provenance": kind}
                                for kind, key in (("publisher", "subtitles"), ("platform_automatic", "automatic_captions"))
                                for lang in (info.get(key) or {})]
        if metadata["platform"] == "youtube":
            # YouTube exposes translated captions as if they were native tracks.
            # Keep publisher tracks and original-language ASR only. Local Whisper
            # supplies the transcript without requesting translated captions.
            original = {k: v for k, v in (info.get("automatic_captions") or {}).items() if k.endswith("-orig")}
            info["requested_subtitles"] = downloader.process_subtitles(info["id"], info.get("subtitles"), original)
        atomic_json(packet / "source_metadata.json", metadata)
        downloader.process_info(info)
        metadata["warnings"] = downloader.caption_warnings
        metadata["caption_files"] = [p.name for p in sorted(target.glob("*.vtt"))]
        atomic_json(packet / "source_metadata.json", metadata)
    videos = [f for f in target.glob("video.*") if f.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}]
    if len(videos) != 1 or videos[0].stat().st_size > limit:
        raise RuntimeError("Download incomplete or size limit")
    return videos[0], metadata


def download_options(source, target, settings, progress_hook):
    platform = "youtube" if "youtube.com" in source else "tiktok"
    options = {"quiet": True, "logger": Quiet(), "no_warnings": True,
               "noplaylist": True, "cachedir": False, "socket_timeout": 30,
               "retries": 3, "fragment_retries": 3, "extractor_retries": 3,
               "max_filesize": settings["max_download_mib"] * 1048576,
               "ffmpeg_location": str(Path(binary("ffmpeg")).parent),
               "format": f"bestvideo[height<={settings['download_height']}]+bestaudio/best[height<={settings['download_height']}]",
               "merge_output_format": "mp4", "outtmpl": str(Path(target) / "video.%(ext)s"),
               "windowsfilenames": True, "writesubtitles": True, "writeautomaticsub": True,
               "subtitleslangs": settings["caption_languages"].split(","), "subtitlesformat": "vtt",
               "progress_hooks": [progress_hook]}
    profile = APP_HOME / "login-profiles" / platform / "Default"
    if profile.is_dir():
        options["cookiesfrombrowser"] = ("chromium", str(profile), None, None)
    return options


def link_or_copy(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def reuse_job(job):
    identifier = job.get("reuse_from")
    if not identifier or not isinstance(identifier, str) or not all(c.isalnum() or c == "-" for c in identifier):
        return None
    root = data_dirs(job["settings"]).resolve()
    old = load_json(root / "jobs" / (identifier + ".json"))
    if old and old["source"] == job["source"] and old["status"] in {"partial", "complete", "failed", "model_unavailable"} and Path(old["packet_path"]).resolve().is_relative_to(root):
        return old
    return None


def probe(file):
    return json.loads(run_media([binary("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(file)], 20))


def audio_levels(wav, output):
    import numpy as np
    levels = []
    with wave.open(str(wav)) as stream:
        rate = stream.getframerate()
        total = stream.getnframes()
        offset = 0
        while raw := stream.readframes(rate):
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
            rms = float(np.sqrt(np.mean(samples * samples)))
            levels.append({"start": offset / rate, "end": min(total, offset + len(samples)) / rate,
                           "rms_dbfs": round(20 * math.log10(max(rms, 1e-8)), 2),
                           "peak": round(float(np.max(np.abs(samples))), 5)})
            offset += len(samples)
    atomic_json(output, {"sample_rate": rate, "channels": 1, "duration_seconds": total / rate,
                         "measurement": "one-second RMS and peak; not speech or meaning detection", "levels": levels})


def transcribe(job, wav, packet, duration):
    from faster_whisper import WhisperModel
    settings = job["settings"]
    audio = packet / "audio"
    records = []
    transcript = {"provenance": "local_faster_whisper", "model": settings["model"],
                  "verified": False, "full_audio_processed": False, "duration_seconds": duration,
                  "segments": records, "warnings": []}
    atomic_json(audio / "transcript.json", transcript)
    model = WhisperModel(settings["model"], device="cpu", compute_type="int8",
                         cpu_threads=settings["threads"], num_workers=1,
                         download_root=str(RUNTIME / "models" / "hub"), local_files_only=True)
    segments, info = model.transcribe(str(wav), language=None if settings["language"] == "auto" else settings["language"],
                                      beam_size=3, word_timestamps=True, vad_filter=True,
                                      initial_prompt=settings['speech_vocabulary'] or None,
                                      condition_on_previous_text=False)
    transcript["language"] = info.language
    transcript["language_probability"] = info.language_probability
    with (audio / "segments.jsonl").open("w", encoding="utf-8") as incremental:
        for segment in segments:
            record = {"start": segment.start, "end": segment.end, "text": segment.text.strip(),
                      "avg_logprob": segment.avg_logprob, "no_speech_probability": segment.no_speech_prob,
                      "words": [{"start": w.start, "end": w.end, "text": w.word, "probability": w.probability}
                                for w in (segment.words or [])]}
            records.append(record)
            incremental.write(json.dumps(record, ensure_ascii=False) + "\n")
            incremental.flush()
            progress(job, "transcribing", 35 + 35 * min(1, segment.end / duration))
    transcript["full_audio_processed"] = True
    if not records:
        transcript["warnings"].append("No speech recognized; this does not prove silence")
    if any(s["end"] > duration + 0.25 or s["start"] < 0 for s in records):
        transcript["warnings"].append("ASR timestamps exceed source bounds")
    atomic_json(audio / "transcript.json", transcript)
    (audio / "transcript.txt").write_text("\n".join(f"[{clock(s['start'])} - {clock(s['end'])}] {s['text']}" for s in records), encoding="utf-8")
    (audio / "transcript.srt").write_text("\n\n".join(f"{i}\n{clock(s['start'], True)} --> {clock(s['end'], True)}\n{s['text']}" for i, s in enumerate(records, 1)), encoding="utf-8")
    return transcript


def visuals(job, file, packet, duration):
    from PIL import Image, ImageDraw, ImageFont
    settings = job["settings"]
    root = packet / "visuals"
    old = reuse_job(job)
    if old and all(old["settings"][k] == settings[k] for k in ("frame_interval", "frame_width")):
        cached = Path(old["packet_path"]) / "visuals"
        coverage = load_json(cached / "coverage.json", {})
        if coverage.get("full_timeline_sampled"):
            shutil.copytree(cached, root, copy_function=link_or_copy)
            return coverage
    frames = root / "frames"
    sheets = root / "sheets"
    frames.mkdir(parents=True)
    sheets.mkdir()
    interval = settings["frame_interval"]
    # showinfo emits actual source PTS, retained separately from expected sample times.
    filter_arg = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval})',scale='min(iw,{settings['frame_width']})':-2,showinfo"
    with (root / "frame_decode.log").open("wb") as log:
        result = subprocess.run([binary("ffmpeg"), "-nostdin", "-y", "-i", str(file),
                                 "-vf", filter_arg, "-fps_mode", "vfr", "-q:v", "4",
                                 str(frames / "frame-%06d.jpg")], stdout=subprocess.DEVNULL, stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("Frame extraction failed")
    import re
    stamps = [float(x) for x in re.findall(r"\bpts_time:([\d.eE+-]+)", (root / "frame_decode.log").read_text(errors="replace"))]
    images = sorted(frames.glob("frame-*.jpg"))
    if not images or len(stamps) != len(images):
        raise RuntimeError("Frame timestamp coverage unavailable")
    mapping = [{"file": "frames/" + image.name, "timestamp_seconds": stamp} for image, stamp in zip(images, stamps)]
    atomic_json(root / "frames.json", {"sampling_interval_seconds": interval, "frames": mapping})
    font = ImageFont.load_default(size=16)
    pages = []
    for start in range(0, len(images), 12):
        sheet = Image.new("RGB", (1080, 4 * 236), "#f5f3ee")
        draw = ImageDraw.Draw(sheet)
        for n, image in enumerate(images[start:start + 12]):
            with Image.open(image) as original:
                thumb = original.copy()
                thumb.thumbnail((344, 196))
            x, y = (n % 3) * 360 + 8, (n // 3) * 236 + 8
            sheet.paste(thumb, (x, y))
            draw.text((x, y + 200), clock(stamps[start + n]), font=font, fill="#292824")
        name = f"sheet-{start // 12 + 1:04d}.jpg"
        sheet.save(sheets / name, quality=85)
        pages.append("sheets/" + name)
        progress(job, "contact_sheets", 85 + 10 * (start + 12) / max(len(images), 12))
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    max_gap = max(gaps, default=0)
    full = stamps[0] <= interval + .1 and duration - stamps[-1] <= interval + .25 and max_gap <= interval + .25
    coverage = {"full_timeline_sampled": full, "every_decoded_frame": False,
                "reviewed_by_human_or_codex": False, "first_seconds": stamps[0], "last_seconds": stamps[-1],
                "max_gap_seconds": max_gap, "frames": len(images), "contact_sheets": pages}
    atomic_json(root / "coverage.json", coverage)
    (root / "CONTACT_SHEETS.md").write_text("# Visual evidence\n\nSampled frames, not semantic analysis. Review every sheet for whole-video review.\n\n" +
                                           "\n\n".join(f"![{Path(p).stem}]({p})" for p in pages), encoding="utf-8")
    return coverage


def packet_manifest(job, packet, metadata, duration, audio, visual, status, warnings):
    provenance = job["provenance"]
    manifest = {"schema_version": 2, "packet_id": job["id"], "status": status,
                "collection_status": status, "review_status": "unreviewed",
                "evidence": load_json(packet / "evidence" / "status.json", {"status": "not_built"}),
                "project_id": job.get("project_id"), "session_id": job.get("session_id"),
                "source": {"url_or_file": job["source"], **metadata},
                "research": provenance, "queued_at": job["created_at"],
                "processing_started_at": job.get("started_at"), "processing_finished_at": utc() if status != "incomplete" else None,
                "duration_seconds": duration, "processing_settings": job["settings"],
                "coverage": {"audio": audio, "visuals": visual}, "warnings": warnings,
                "handoff": {"automatic": False, "sent_to_codex": False, "user_selects_packets": True},
                "cloud_inference_calls": 0, "semantic_analysis": False, "files": []}
    text = ["# " + str(metadata.get("title") or job["id"]), "", "", "",
            f"Status: {status}", f"Source: {job['source']}", f"Origin: {provenance.get('origin')}",
            f"Search prompt: {provenance.get('search_prompt') or '(manual link)'}",
            f"Discovered: {provenance.get('discovered_at') or '(not recorded)'}",
            f"Queued: {job['created_at']}", f"Processed: {manifest['processing_finished_at']}",
            f"Duration: {duration} seconds", "", "## Files", "",
            "- packet.json: provenance, coverage, settings, file inventory",
            "- source/: original media and available captions",
            "- audio/transcript.txt: complete timestamped local ASR",
            "- audio/transcript.json: word confidence, provenance, completeness",
            "- audio/segments.jsonl: incremental ASR, retained on interruption",
            "- audio/audio.wav: full extracted audio",
            "- audio/levels.json: one-second audio measurements",
            "- visuals/CONTACT_SHEETS.md: complete ordered contact sheet list",
            "- visuals/frames.json: source timestamps and full-size image paths", "", "## Limitations", "",
            "Speech recognition can omit or invent words. Sampling can miss brief visual events. "
            "Nothing in this packet proves a claim in the source. Missing evidence remains incomplete.", "",
            *["- " + w for w in warnings]]
    (packet / "PACKET.md").write_text("\n".join(text), encoding="utf-8")
    if (packet / "evidence" / "READ.md").is_file():
        with (packet / "PACKET.md").open("a", encoding="utf-8") as stream:
            stream.write("\n\n## Reading\n\nStart with evidence/READ.md and evidence/reader.html. Collection status is separate from OCR readiness and research review.\n")
    manifest["files"] = [{"path": str(p.relative_to(packet)).replace("\\", "/"), "bytes": p.stat().st_size}
                         for p in sorted(packet.rglob("*")) if p.is_file() and p.name != "packet.json" and not p.name.endswith(".tmp")]
    atomic_json(packet / "packet.json", manifest)


def rebuild_index(settings):
    root = data_dirs(settings)
    entries = []
    from projects import packet_files, update_project_indexes
    for file in packet_files(settings):
        p = load_json(file)
        if not p:
            continue
        entries.append({"packet_id": p["packet_id"], "status": p["status"], "project_id": p.get("project_id"), "session_id": p.get("session_id"),
                        "evidence_status": p.get("evidence", {}).get("status", "not_built"),
                        "review_status": p.get('review_status','unreviewed'),
                        "title": p["source"].get("title"), "source": p["source"]["url_or_file"],
                        "search_prompt": p["research"].get("search_prompt"),
                        "discovered_at": p["research"].get("discovered_at"),
                        "processed_at": p.get("processing_finished_at"), "path": str(file.parent)})
    atomic_json(root / "index.json", {"schema_version": 1, "updated_at": utc(), "packets": entries})
    update_project_indexes(settings, entries)
    return entries


def worker(job):
    settings = job["settings"] = validate_settings(job["settings"])
    os.environ.update(worker_env(settings))
    packet = Path(job["packet_path"])
    packet.mkdir(parents=True, exist_ok=True)
    metadata, duration, audio, visual, warnings = {}, None, None, None, []
    status = "incomplete"
    try:
        progress(job, "downloading" if job["source"].startswith("https://") else "copying", 5)
        file, metadata = acquire(job, packet)
        warnings.extend(metadata.get("warnings", []))
        atomic_json(packet / "source_metadata.json", metadata)
        details = probe(file)
        atomic_json(packet / "media_probe.json", details)
        duration = float(details["format"]["duration"])
        if not math.isfinite(duration) or not 0 < duration <= settings["max_duration_minutes"] * 60:
            raise ValueError("Source duration limit")
        metadata["sha256"] = digest(file)
        audio_dir = packet / "audio"
        audio_dir.mkdir()
        if any(s.get("codec_type") == "audio" for s in details["streams"]):
            progress(job, "extracting_audio", 20)
            wav = audio_dir / "audio.wav"
            run_media([binary("ffmpeg"), "-nostdin", "-y", "-i", str(file), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], settings["job_timeout_minutes"] * 60)
            audio_levels(wav, audio_dir / "levels.json")
            progress(job, "transcribing", 35)
            try:
                with wave.open(str(wav)) as stream:
                    audio_duration = stream.getnframes() / stream.getframerate()
                transcript = transcribe(job, wav, packet, audio_duration)
                audio = {"full_audio_processed": True, "speech_detected": bool(transcript["segments"]),
                         "duration_seconds": audio_duration, "verified": False, "provenance": "local_faster_whisper"}
                warnings.extend(transcript["warnings"])
            except Exception as error:
                (audio_dir / "diagnostics.txt").write_text(traceback.format_exc(), encoding="utf-8")
                audio = {"full_audio_processed": False, "error": error_label(error), "error_type": type(error).__name__, "detail": str(error)[:500], "verified": False}
                warnings.append("Transcription incomplete: " + error_label(error))
        else:
            audio = {"full_audio_processed": False, "error": "no_audio_stream", "verified": False}
            warnings.append("No audio stream")
        progress(job, "extracting_frames", 75)
        visual = visuals(job, file, packet, duration)
        from evidence import build_evidence
        progress(job, "reading_screens", 96)
        try:
            evidence = build_evidence(packet, settings, source=file, duration=duration)
            warnings.extend(evidence.get("warnings", []))
        except Exception as error:
            warnings.append("Reading evidence incomplete: " + str(error)[:180])
            atomic_json(packet / "evidence" / "status.json", {"status": "failed", "error": str(error)[:500]})
        status = "complete" if audio.get("full_audio_processed") and audio.get("speech_detected") and visual["full_timeline_sampled"] else "partial"
        packet_manifest(job, packet, metadata, duration, audio, visual, status, warnings)
        save_job(job, status, progress=100, finished_at=utc(), detail="; ".join(warnings))
    except Exception as error:
        status = error_label(error)
        detail = safe_error_detail(error)
        (packet / "diagnostics.txt").write_text(detail, encoding="utf-8")
        warnings.append(status)
        packet_manifest(job, packet, metadata, duration, audio, visual, status, warnings)
        save_job(job, status, error=status, detail=detail, finished_at=utc())
        return 1
    return 0


def supervise(path):
    job = load_json(path)
    if not job or job["status"] != "queued":
        raise ValueError("Queued job required")
    job["settings"] = validate_settings(job["settings"])
    job["source"] = canonical_source(job["source"])
    packet = Path(job["packet_path"])
    packet.mkdir(parents=True, exist_ok=True)
    save_job(job, "starting", started_at=utc())
    packet_manifest(job, packet, {}, None, None, None, "incomplete", ["Processing in progress"])
    started = time.monotonic()
    reason = None
    next_size_check = 0
    with (packet / "worker.private.log").open("wb") as log:
        child = spawn("analyzer.py", ["_worker", "--job", path], stdout=log, stderr=log)
        try:
            while child.poll() is None:
                if (packet / "STOP").exists():
                    reason = "cancelled"
                elif time.monotonic() - started > job["settings"]["job_timeout_minutes"] * 60:
                    reason = "time_limit"
                elif time.monotonic() >= next_size_check:
                    next_size_check = time.monotonic() + 2
                    if folder_size(packet) > job["settings"]["max_packet_mib"] * 1048576:
                        reason = "storage_limit"
                if reason:
                    stop_process(child)
                    break
                time.sleep(.3)
        except KeyboardInterrupt:
            reason = "cancelled"
            stop_process(child)
        finally:
            if child.poll() is None:
                stop_process(child)
    current = load_json(path, job)
    if reason or current["status"] in {"starting", "downloading", "copying", "extracting_audio", "transcribing", "extracting_frames", "contact_sheets", "reading_screens"}:
        reason = reason or "interrupted"
        old = load_json(packet / "packet.json", {})
        packet_manifest(current, packet, load_json(packet / "source_metadata.json", {}), old.get("duration_seconds"),
                        {"full_audio_processed": False}, {"full_timeline_sampled": False}, reason, ["Incomplete: " + reason])
        save_job(current, reason, finished_at=utc(), error=reason)
    rebuild_index(job["settings"])
    return 0 if load_json(path)["status"] in {"complete", "partial"} else 1


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "_worker"):
        sub.add_parser(command).add_argument("--job", required=True)
    add = sub.add_parser("add")
    add.add_argument("--source", required=True)
    args = parser.parse_args()
    if args.command == "add":
        job = new_job(args.source, settings_load())
        print(json.dumps({"job_id": job["id"], "job_file": str(Path(job["settings"]["output_dir"]) / "jobs" / (job["id"] + ".json"))}))
        return 0
    if args.command == "_worker":
        return worker(load_json(args.job))
    started = time.monotonic()
    while True:
        try:
            with exclusive(RUNTIME / ".workbench-analysis.lock"):
                return supervise(args.job)
        except LockBusy:
            job = load_json(args.job)
            if (Path(job["packet_path"]) / "STOP").exists() or time.monotonic() - started > job["settings"]["job_timeout_minutes"] * 60:
                save_job(job, "cancelled", finished_at=utc())
                return 1
            time.sleep(.3)


if __name__ == "__main__":
    raise SystemExit(main())
