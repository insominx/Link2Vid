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
  _internal\       - bundled Python libraries (required; do not delete)

BUILD AGAIN
  From the repo root: build_windows.bat
  Temporary PyInstaller files live under build\ and are not runnable.
