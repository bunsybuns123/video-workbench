# Video Workbench

Video Workbench is a desktop app for collecting and organizing evidence from supplied YouTube and TikTok links. Add one link at a time or select local video files, review the selected project's queue, then analyze it. Speech recognition and screen text extraction run on the Mac. No model API or Codex callback is used.

## Download for Apple Silicon

1. Open [Releases](https://github.com/bunsybuns123/video-workbench/releases) and download `Video-Workbench-macOS-Apple-Silicon.dmg`.
2. Open the disk image and drag **Video Workbench** into **Applications**.
3. Open the app. On first launch, macOS may ask you to confirm because the app is not signed and notarized. Control-click the app, choose **Open**, then confirm **Open**.
4. A Terminal window installs Python 3.12, Tk and the app's Python packages. Chromium downloads only if a site later requires sign-in. If Homebrew is missing, install it from [brew.sh](https://brew.sh) and open Video Workbench again.
5. Choose whether to install the optional Codex skill. You can change that later in Settings.

The first setup downloads Python packages. Replacing the app with a newer release does not repeat setup when the runtime is already installed. In Settings, select a speech model and press **Cache model** once while online. It is stored locally and works offline after that. The default Base model and three CPU threads are chosen for an 8 GB Apple Silicon Mac. Tiny uses less memory. Small and Medium use more memory and take longer on CPU.

## Connect Obsidian

In Settings, choose your vault folder, select **Use vault for library**, then **Save**. New projects and packets go into the vault's `Video Workbench` folder as ordinary Markdown and evidence files. Existing library data is not moved. You can leave the vault setting blank and keep the default Documents library.

## Connect Codex

Choose **Install Codex skill** in Settings, then start a new Codex chat and copy a project's folder path from the Library tab. Give that path to Codex when you want it to review the packet contents. The skill describes the packet format and review steps. Video Workbench never sends packets to Codex automatically, and installing the skill does not select any packets for review.

## Requirements and limitations

- macOS Sequoia 15 or newer on Apple Silicon. The app is built and checked on a native Apple Silicon GitHub runner. The target MacBook itself has not been physically tested here.
- Homebrew is used to install Python 3.12 and its Tk interface when a working Python 3.12 is not already available.
- The app processes speech with Whisper locally. On an M2 with 8 GB of memory, Base or Tiny is the practical starting point. Processing uses CPU and is slower than a desktop GPU.
- Video downloads need an internet connection. If a site explicitly requires sign-in, a button appears in Settings to open a separate app browser. Its profile is used only for that site's queued download.
- Speech recognition and OCR can make mistakes. Packets retain coverage and source images so the user can inspect what was actually captured.
- macOS Gatekeeper requires one manual confirmation because this project does not have an Apple Developer signing and notarization identity.

See [the local app guide](references/LOCAL_APP.md) and [the skill guide](SKILL.md) for settings, packet layout and evidence review.
