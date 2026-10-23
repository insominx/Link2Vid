Link2Vid (Windows portable build)
=================================

START HERE
  Double-click Link2Vid.bat in this folder.

WHAT THIS FOLDER IS
  The complete packaged app. Zip this entire folder to move or share it.

FILES
  Link2Vid.bat     - start the app (use this)
  Link2Vid.exe     - engine (called by Link2Vid.bat; do not run files under build\)
  developer.json   - optional settings (copied here during build if present in repo root)
  bin\             - optional ffmpeg / deno sidecars
  _internal\       - bundled Python libraries and the app UI (required; do not delete)

REQUIREMENTS
  Microsoft Edge WebView2 Runtime (preinstalled on Windows 11 and current
  Windows 10). If the window stays blank, install it from
  https://developer.microsoft.com/microsoft-edge/webview2/

WHISPER TRANSCRIPTION
  The first use of each model downloads weights to:
    %LOCALAPPDATA%\Link2Vid\models
  Model weights are not bundled in this folder. Keep that cache for offline use.
  A compatible NVIDIA setup can accelerate transcription. Link2Vid selects CPU
  when CTranslate2 reports no CUDA device; CUDA loading errors are shown.
  Successful runs save both .txt and .srt files.

BUILD AGAIN
  From the repo root: build_windows.bat
  Temporary PyInstaller files live under build\ and are not runnable.
