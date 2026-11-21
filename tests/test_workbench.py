"""Synthetic fixtures only. No user packets or personal browser sessions."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import (DEFAULTS, atomic_json, binary, canonical_source, exclusive,
                    load_json, LockBusy, new_job, spawn, utc, validate_settings, worker_env)
from analyzer import OptionalCaptions, Quiet, error_label, rebuild_index, run_media, supervise

INTEGRATION = "--integration" in sys.argv
if INTEGRATION:
    sys.argv.remove("--integration")


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="video-workbench-test-")
        self.root = Path(self.temp.name).resolve()
        self.settings = {**DEFAULTS, "output_dir": str(self.root / "library")}

    def tearDown(self):
        self.temp.cleanup()

    def test_sources_and_rejections(self):
        self.assertEqual(canonical_source("https://youtu.be/XLtBXlcBRzE?si=tracking"), "https://www.youtube.com/watch?v=XLtBXlcBRzE")
        self.assertEqual(canonical_source("https://www.youtube.com/shorts/XLtBXlcBRzE"), "https://www.youtube.com/watch?v=XLtBXlcBRzE")
        self.assertEqual(canonical_source("https://www.tiktok.com/@fixture/video/1234567890123456789?x=1"), "https://www.tiktok.com/@fixture/video/1234567890123456789")
        self.assertEqual(canonical_source("https://vm.tiktok.com/ABC123/?share=x"), "https://vm.tiktok.com/ABC123/")
        for value in ("https://youtube.com.evil.example/watch?v=XLtBXlcBRzE", "http://youtu.be/XLtBXlcBRzE", "https://u:p@youtube.com/watch?v=XLtBXlcBRzE", "https://www.youtube.com/playlist?list=x", "https://localhost/video.mp4", "https://vm.tiktok.com/x", "https://tiktok.com.evil.example/t/ABC123", "\\\\server\\video.mp4"):
            with self.subTest(source=value), self.assertRaises(ValueError):
                canonical_source(value)

    def test_settings_bounds(self):
        for key, value in (("threads", 0), ("frame_interval", "nan"), ("job_timeout_minutes", 1000), ("output_dir", ""), ("language", "English")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_settings({**self.settings, key: value})
        self.assertEqual(validate_settings(self.settings)["frame_interval"], .5)

    def test_projects_session_transfer_remove_and_undo(self):
        from projects import create_project, move_entry, packet_files, remove_entry, undo_remove
        from analyzer import packet_manifest, save_job
        first = create_project(self.settings, "First project").name
        second = create_project(self.settings, "Second project").name
        job = new_job("https://www.youtube.com/watch?v=XLtBXlcBRzE", self.settings, project_id=first, session_id="fixture-session")
        folder = Path(job["packet_path"])
        folder.mkdir(parents=True)
        packet_manifest(job, folder, {"title": "Synthetic"}, 1, {}, {}, "partial", [])
        save_job(job, "partial")
        session_folder = folder.parent.parent
        atomic_json(session_folder / "SESSION.json", {"id": "fixture-session", "project_id": first, "title": "Session", "search_prompts": ["query"], "status": "partial"})
        destination = move_entry(self.settings, session_folder, second)
        file = list(destination.glob("packets/*/packet.json"))[0]
        self.assertEqual(load_json(file)["project_id"], second)
        stored_job = load_json(Path(self.settings["output_dir"]) / "jobs" / (job["id"] + ".json"))
        self.assertTrue(Path(stored_job["packet_path"]).is_relative_to(destination))
        self.assertEqual(len(packet_files(self.settings)), 1)
        remove_entry(self.settings, destination)
        self.assertEqual(packet_files(self.settings), [])
        self.assertEqual(undo_remove(self.settings), destination)
        self.assertTrue(file.exists())
        self.assertEqual(load_json(Path(self.settings["output_dir"]) / "jobs" / (job["id"] + ".json"))["status"], "partial")
        entries = rebuild_index(self.settings)
        self.assertEqual(entries[0]["project_id"], second)
        index = load_json(Path(self.settings["output_dir"]) / "projects" / first / "index.json")
        self.assertEqual(index["packets"], [])

    def test_active_packet_cannot_move_or_remove(self):
        from projects import create_project, move_entry, remove_entry
        from analyzer import packet_manifest
        job = new_job("https://www.youtube.com/watch?v=XLtBXlcBRzE", self.settings)
        folder = Path(job["packet_path"])
        folder.mkdir(parents=True)
        packet_manifest(job, folder, {}, None, None, None, "incomplete", [])
        target = create_project(self.settings, "Target").name
        with self.assertRaises(ValueError):
            move_entry(self.settings, folder, target)
        with self.assertRaises(ValueError):
            remove_entry(self.settings, folder)

    def test_project_ui_initialization(self):
        import tkinter as tk
        import app
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(app, "settings_load", lambda: self.settings):
                ui = app.Workbench(root)
                for page in ("Analyzer", "Library", "Settings"):
                    ui.show(page)
                    root.update_idletasks()
                self.assertEqual(ui.selected_project(), "inbox")
                self.assertEqual(ui.notice.get(), "")
                ui.evidence_queue = [self.root/'removed-packet']
                ui.tick_workers()
                self.assertIsNone(ui.evidence_packet)
                self.assertFalse((self.root/'removed-packet').exists())
        finally:
            root.destroy()

    def test_caption_429_is_optional_without_hiding_media_errors(self):
        from yt_dlp.utils import DownloadError
        with OptionalCaptions({"logger": Quiet(), "quiet": True, "writesubtitles": True,
                               "outtmpl": str(self.root / "video.%(ext)s")}) as downloader:
            info = {"id": "fixture", "title": "fixture", "ext": "mp4",
                    "requested_subtitles": {"fi": {"ext": "vtt", "url": "https://example.invalid/subs"}}}
            with patch.object(downloader, "dl", side_effect=DownloadError("HTTP Error 429: Too Many Requests", exc_info=(None, None, None))) as download:
                self.assertEqual(downloader._write_subtitles(info, str(self.root / "video.mp4")), [])
                self.assertEqual(download.call_count, 1)
            self.assertEqual(downloader.caption_warnings, ["Optional captions unavailable: rate_limited"])
            # The narrow subtitle override does not enable blanket error suppression.
            self.assertIsNot(downloader.params.get("ignoreerrors"), True)
            with self.assertRaises(DownloadError):
                downloader.report_error("Media HTTP Error 403: Forbidden")
        self.assertEqual(error_label("HTTP Error 429: Too Many Requests"), "rate_limited")
        self.assertEqual(error_label("Sign in required"), "sign_in_required")

    def test_original_caption_selection_excludes_translations(self):
        with OptionalCaptions({"logger": Quiet(), "quiet": True, "writesubtitles": True,
                               "writeautomaticsub": True, "subtitleslangs": ["en.*", "fi"]}) as downloader:
            tracks = {k: [{"ext": "vtt", "url": "https://example.invalid/" + k}]
                      for k in ("en-orig", "en", "fi")}
            original = {k: v for k, v in tracks.items() if k.endswith("-orig")}
            selected = downloader.process_subtitles("fixture", {}, original)
            self.assertEqual(set(selected), {"en-orig"})

    def test_exclusive_worker_and_environment(self):
        lock = self.root / "worker.lock"
        with exclusive(lock):
            with self.assertRaises(LockBusy):
                with exclusive(lock):
                    pass
        with exclusive(lock):
            pass
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fixture", "TEST_PASSWORD": "fixture"}):
            env = worker_env()
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("TEST_PASSWORD", env)
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")

    @unittest.skipUnless(INTEGRATION, "--integration")
    def test_whole_local_pipeline(self):
        wav = self.root / "speech.wav"
        # Controlled Windows text-to-speech, saved to a temporary fixture file.
        script = self.root / "fixture.ps1"
        script.write_text("$voice = New-Object -ComObject SAPI.SpVoice\n$stream = New-Object -ComObject SAPI.SpFileStream\n$stream.Open($args[0], 3)\n$voice.AudioOutputStream = $stream\n[void]$voice.Speak('The first scene is red. The second scene is blue. This is a local video test.')\n$stream.Close()\n", encoding="utf-8")
        subprocess.run(["powershell", "-NoProfile", "-File", str(script), str(wav)], check=True, capture_output=True, timeout=30)
        video = self.root / "fixture.mp4"
        run_media([binary("ffmpeg"), "-y", "-f", "lavfi", "-i", "color=red:s=320x240:r=10:d=6",
                   "-f", "lavfi", "-i", "color=blue:s=320x240:r=10:d=6", "-i", str(wav),
                   "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a:0",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)])
        provenance = {"origin": "synthetic_test", "search_prompt": "red blue whole video fixture", "discovered_at": utc(), "discovery_session_id": "local-fixture"}
        job = new_job(str(video), self.settings, provenance)
        path = Path(self.settings["output_dir"]) / "jobs" / (job["id"] + ".json")
        self.assertEqual(supervise(path), 0)
        packet = Path(job["packet_path"])
        manifest = load_json(packet / "packet.json")
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest['evidence']['status'],'needs_attention')
        self.assertEqual(manifest['evidence']['source_dimensions'],[320,240])
        self.assertEqual(manifest['review_status'],'unreviewed')
        self.assertFalse(manifest["handoff"]["automatic"])
        self.assertFalse(manifest["handoff"]["sent_to_codex"])
        self.assertEqual(manifest["cloud_inference_calls"], 0)
        self.assertEqual(manifest["research"], provenance)
        transcript = (packet / "audio" / "transcript.txt").read_text().lower()
        self.assertIn("red", transcript)
        self.assertIn("blue", transcript)
        frames = load_json(packet / "visuals" / "frames.json")["frames"]
        self.assertGreaterEqual(len(frames), 24)
        stamps = [f["timestamp_seconds"] for f in frames]
        self.assertEqual(stamps, sorted(stamps))
        self.assertLessEqual(stamps[0], .1)
        self.assertGreater(stamps[-1], 11)
        from PIL import Image
        with Image.open(packet / "visuals" / frames[0]["file"]) as image:
            red = image.getpixel((100, 100))
        with Image.open(packet / "visuals" / frames[-1]["file"]) as image:
            blue = image.getpixel((100, 100))
        self.assertGreater(red[0], red[2] + 100)
        self.assertGreater(blue[2], blue[0] + 100)
        self.assertTrue(load_json(packet / "audio" / "levels.json")["levels"])
        self.assertEqual(rebuild_index(self.settings)[0]["search_prompt"], provenance["search_prompt"])
        # The same job cannot be rerun or silently overwritten.
        with self.assertRaises(ValueError):
            supervise(path)

    @unittest.skipUnless(INTEGRATION, "--integration")
    def test_silent_partial_and_cancel(self):
        video = self.root / "silent.mp4"
        run_media([binary("ffmpeg"), "-y", "-f", "lavfi", "-i", "color=black:s=160x120:r=10:d=2", "-c:v", "libx264", str(video)])
        job = new_job(str(video), self.settings)
        path = Path(self.settings["output_dir"]) / "jobs" / (job["id"] + ".json")
        self.assertEqual(supervise(path), 0)
        manifest = load_json(Path(job["packet_path"]) / "packet.json")
        self.assertEqual(manifest["status"], "partial")
        self.assertFalse(manifest["coverage"]["audio"]["full_audio_processed"])
        cancelled = new_job(str(video), self.settings)
        folder = Path(cancelled["packet_path"])
        folder.mkdir(parents=True)
        (folder / "STOP").touch()
        path = Path(self.settings["output_dir"]) / "jobs" / (cancelled["id"] + ".json")
        self.assertEqual(supervise(path), 1)
        self.assertEqual(load_json(path)["status"], "cancelled")
        self.assertEqual(load_json(folder / "packet.json")["status"], "cancelled")
        self.assertFalse(load_json(folder / "packet.json")["handoff"]["automatic"])

    @unittest.skipUnless(INTEGRATION, "--integration")
    def test_duration_rejection_preserves_failure(self):
        video = self.root / "long.mp4"
        run_media([binary("ffmpeg"), "-y", "-f", "lavfi", "-i", "color=black:s=160x120:r=1:d=62", "-c:v", "libx264", str(video)])
        settings = {**self.settings, "max_duration_minutes": 1}
        job = new_job(str(video), settings)
        path = Path(settings["output_dir"]) / "jobs" / (job["id"] + ".json")
        self.assertEqual(supervise(path), 1)
        packet = load_json(Path(job["packet_path"]) / "packet.json")
        self.assertEqual(packet["status"], "failed")
        self.assertIsNone(packet["coverage"]["audio"])
        self.assertFalse(packet["handoff"]["automatic"])
        self.assertEqual(rebuild_index(settings)[0]["status"], "failed")

    @unittest.skipUnless(INTEGRATION, "--integration")
    def test_missing_model_keeps_visuals_without_cloud_fallback(self):
        from common import RUNTIME
        if list((RUNTIME / "models" / "hub").glob("models--Systran--faster-whisper-medium/snapshots/*/model.bin")):
            self.skipTest("Medium model already installed")
        video = self.root / "tone.mp4"
        run_media([binary("ffmpeg"), "-y", "-f", "lavfi", "-i", "color=black:s=160x120:r=10:d=2",
                   "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac", str(video)])
        settings = {**self.settings, "model": "medium"}
        job = new_job(str(video), settings)
        path = Path(settings["output_dir"]) / "jobs" / (job["id"] + ".json")
        self.assertEqual(supervise(path), 0)
        packet = load_json(Path(job["packet_path"]) / "packet.json")
        self.assertEqual(packet["status"], "partial")
        self.assertEqual(packet["coverage"]["audio"]["error"], "model_unavailable")
        self.assertTrue(packet["coverage"]["visuals"]["full_timeline_sampled"])
        self.assertEqual(packet["cloud_inference_calls"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
