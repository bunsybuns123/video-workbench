# Validation

## Queue and download repair, 2026-11-21

Version 0.2.0 replaces multi-link submission and the separate Run queue action with single-link Add to queue, Add files and one project-scoped Analyze action. Queue insertion is timestamped to preserve order, pending entries are shown first, and the Analyzer keeps the 20 most recent terminal attempts visible. Projects created from Analyzer and Library use the same name dialog. Existing project and session folders are retained.

The keyword Scroller and its processor were removed. Earlier checks below that describe Scroller UI, browser search or synthetic sessions refer to version 0.1.3. Sign-in is now offered only after a sign-in-required result. It opens a dedicated app-owned browser profile, and yt-dlp uses that profile on a later retry. Explicit downloader retry counts and socket timeout replace the former zero-retry setup. Error classification gives explicit sign-in messages precedence over broad bot and HTTP block wording. Failure details are stored in the job with query strings redacted.

The tagged GitHub workflow checks Python syntax and builds the Mac app package. Its result for this revision is recorded after publication. A physical MacBook M2 download and live platform download remain unverified.

## Stop and restart repair

The reported failure was reproduced in the desktop UI. The rebuild log recorded Windows error 5 while atomically replacing evidence/status.json, followed by the same failure in error reporting. Atomic writes now retry transient PermissionError with a bounded backoff, preserve the prior file on permanent failure, and clean temporary files. A restart requested while cancellation finishes is queued once instead of silently ignored. Completed reader snapshots remain available after an interrupted subsequent build. Regression coverage checks these conditions, in addition to the existing evidence and collection checks.

## Readable evidence update, 2026-10-30

The current checks comprise 18 core/integration tests and five readable-evidence tests. The core suite includes controlled YouTube and TikTok browser sessions, gate detection, separate session packets, partial/missing-model outcomes, cancellation, caption failure handling, project transfers and UI initialization. These tests use synthetic fixtures and isolated profiles. They do not establish unrestricted live TikTok or YouTube search access.

Readable-evidence checks use a real encoded 1920x1080 fixture with 22-pixel text. Both known numerical values are recovered by Windows OCR. Tests verify small-region changes, crop coverage without missing pixels, a transcript spanning a screen transition, actual decoded timestamps, nearest-sample offsets, OCR cache reuse, explicit partial/full review records and invalidation after a changed transcript. A browser test checks timestamp navigation, OCR search, audio/video switching, empty results and script-injection escaping. Audio tests detect a deliberately introduced 300 ms delay. Removing a packet cannot cause the failed builder to recreate its folder. Active reading locks block Move and Remove.

The five selected real packets initially contained approximately 360p video. Higher-resolution acquisition was demonstrated on Koyfin and The Plain Bagel. A 1080p cash-flow statement crop is readable enough to inspect labels, years and figures; OCR still makes mistakes with punctuation and some digits. On one screen, the initial RapidOCR crop method took approximately 14 seconds and Windows OCR approximately 0.2 seconds. This is a single-machine measurement, not an accuracy comparison or general performance guarantee.

The remaining Plain Bagel packet was upgraded end to end. Its refreshed audio matched reference waveforms at three landmarks, with correlations approximately 0.94-0.98 and a consistent offset of about -36 ms. The offset is retained and applied to media navigation; raw ASR timestamps remain available. This is sampled signal validation, not full audio listening. Four other packets were removed from Library during the upgrade and were not restored or reviewed from Trash. Their removal does not constitute successful upgrading.

Collection status, reading readiness and research review are independent. No upgrade marks semantic research complete. Existing transcript errors are not silently rewritten. A full native-crop research review of the upgraded real video has not been performed as part of the engineering checks.

The historical checks below describe earlier versions and their limitations.

Checked locally on Windows, 2026-10-29.

The synthetic integration suite checks actual speech recognition and red/blue pixel evidence across the complete test clip, chronological source frame timestamps, contact sheet creation, audio measurements, preserved exact search prompts and times, canonical URLs, invalid-source rejection, setting bounds, OS-held exclusive locks, filtered worker environments, cancellation of real worker processes, silent partial packets, duration rejection, missing-model partial packets, and browser gate detection. No user research packet is consumed by these tests. The skill validator and runtime dependency check also pass.

The version 0.1.3 native UI was visually inspected in Analyzer, Scroller, Settings and Library. The app ran from its installed skill folder. The color palette and local system fonts required no remote assets.

All 17 automated checks pass. A real Chromium session with network requests intercepted by local fixture pages also verifies keyword navigation, automatic scrolling, pause/resume, exact multi-query provenance, completion status, and creation of no video packets. This controlled browser test is distinct from the live platform checks below.

Live public-search smoke checks used isolated temporary profiles and the test query `Blender official tutorial`. YouTube reported a sign-in gate; TikTok reported a verification gate. Both stopped without creating video packets. Therefore live discovery beyond those gates is not verified on this machine. The synthetic browser tests establish detection of visible sign-in, consent and verification gates, and that a normal sign-in header does not by itself block public browsing. They do not prove every future platform dialog will be detected.

Public YouTube/TikTok downloads depend on yt-dlp and platform access. The revised pipeline's complete media path was tested with synthetic local videos. Browser sign-in is separate from public downloading; no account cookies are exported. No access gate was bypassed. Whole-video semantic understanding, transcript accuracy on arbitrary videos, and every-frame visual coverage are not claimed.

Packet collection, indexing and the desktop UI contain no Codex callback, LLM client, API key or inference endpoint. The local speech recognizer is offline-only. This is a code and dependency-path check, not a full network traffic audit. Platform browsing/downloads and deliberate dependency setup require network access. Building or reviewing packets with Codex consumes Codex tokens.

## Caption failure repair

The reported failure was an HTTP 429 when requesting Finnish subtitles, not a sign-in requirement. YouTube caption selection now excludes automatically translated tracks, and a narrow subtitle hook records optional caption failures without aborting media acquisition. Media access errors remain fatal; HTTP 429 has its own rate_limited status. Tests reproduce the subtitle failure and verify real media errors remain fatal. One bounded download of the reported video then succeeded with original English captions and no login. Its transcript and visual contents were not read by Codex. Small was deliberately cached locally to match the selected setting.

## Project and session update

The reported partial packet had incomplete speech processing and complete visual sampling. Its original exception was not retained, so the historical cause cannot be established precisely. The saved audio was successfully transcribed with the currently installed Small model. A fresh recovery attempt reusing its source and frames finished complete, with full audio processing and visual sampling and no warnings. Actual speech failures now preserve diagnostics.

New tests cover project separation, whole-session moves with child job path updates, prevention of moving/removing active items, Remove/Undo, initialization of every UI page, and session queues producing distinct per-video packets. Browser interaction beyond real sign-in or verification gates remains unverified; controlled browser fixtures exercise navigation, scrolling and pause/resume. No speedup percentage is claimed. Avoided work is observable in cached retries; throttling reduces repeated UI rebuilds, progress writes and directory traversals.
