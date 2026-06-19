# Windows packaging (PyInstaller onedir)

Link2Vid can be built as a portable Windows folder. This is optional; the source + venv workflow (`setup_link2vid.bat`, `run_link2vid.bat`) remains unchanged.

## Where the app lives after a build

| Path | Purpose |
|---|---|
| `release/Link2Vid/` | **The packaged app** — zip or copy this folder |
| `release/Link2Vid/Link2Vid.bat` | **Double-click this to start** |
| `build/` | Temporary PyInstaller output — **not runnable**, cleaned after each build |

There is no valid `Link2Vid.exe` under `build/` after `build_windows.bat` finishes.

## Prerequisites

**Build machine (Windows 10+ x64):**

- Python 3.8+ with project dependencies installed (`setup_link2vid.bat` or equivalent venv + `pip install -r requirements.txt`)
- PyInstaller is installed automatically by `build_windows.bat` from `packaging/requirements-build.txt`

**End-user machine (frozen build):**

- **Chrome** — required for Selenium fallback on authenticated pages
- **JS runtime** — `deno`, `node`, or `bun` on PATH or in `<app_dir>/bin/` for reliable EJS challenge handling
- **ffmpeg** — on PATH or in `<app_dir>/bin/` for HLS merges and Best (A+V) flows
- Installed browsers — for automatic cookie extraction (same as dev mode)

The build script copies `ffmpeg.exe` and `ffprobe.exe` from the builder's PATH into `release/Link2Vid/bin/` when available. If the builder lacks ffmpeg, the build still succeeds; place binaries manually before distributing.

If `developer.json` exists in the repo root at build time, it is copied into `release/Link2Vid/` automatically.

Layout:

```
release/Link2Vid/
  Link2Vid.bat      # start here
  Link2Vid.exe
  README.txt
  developer.json    # optional, copied from repo root when present
  bin/
    ffmpeg.exe
    ffprobe.exe
    deno.exe        # optional, manual
  _internal/        # PyInstaller payload (required)
```

At startup, `link2vid/core/runtime.py` prepends `<app_dir>/bin` to `PATH` so existing `shutil.which("ffmpeg")` checks find sidecars without code changes elsewhere.

## Build steps

From the repo root:

```bat
build_windows.bat
```

This will:

1. Install pinned PyInstaller into the active venv (or system Python)
2. Build into `release/Link2Vid/`
3. Add `Link2Vid.bat`, `README.txt`, and optional `developer.json`
4. Stage ffmpeg/ffprobe into `bin/` when on the builder PATH
5. Run an import smoke test
6. Open `release/Link2Vid` in Explorer

Post-build smoke (also run automatically by `build_windows.bat`):

```bat
packaging\smoke_frozen.bat
```

**Launch the app:**

```bat
release\Link2Vid\Link2Vid.bat
```

Or from the repo root: `run_link2vid_packaged.bat`

Desktop shortcut: `create_link2vid_release_shortcut.ps1`

## Distribution

Zip the entire `release/Link2Vid/` folder for a portable distribution. There is no installer or code signing in v1.

Windows SmartScreen may warn on unsigned executables. Users can choose "More info" → "Run anyway", or you can code-sign in a follow-up release process.

## Configuration (`developer.json`)

Schema is unchanged from dev mode. Discovery order:

| mode | search order |
|---|---|
| frozen | 1. `<exe_dir>/developer.json` 2. `%APPDATA%/Link2Vid/developer.json` |
| dev | 1. current working directory 2. repo/entry script directory 3. `%APPDATA%/Link2Vid/developer.json` |

Frozen mode does not depend on shortcut "Start in" directory. For dev shortcuts, keep working directory at the repo root (see `create_link2vid_shortcut.ps1`).

On first launch, the startup log includes a bootstrap line:

```
runtime: frozen=True app_dir=... developer.json=...|none
```

## Desktop shortcut (frozen build)

Use `create_link2vid_release_shortcut.ps1`, or point a shortcut at `Link2Vid.bat` with "Start in" set to the `release/Link2Vid` folder.

## Troubleshooting

| symptom | likely cause | action |
|---|---|---|
| Fetch fails immediately after build | missing hidden import | update `packaging/link2vid.spec` and `tests/test_packaging_contract.py`; rebuild |
| JS-runtime-required downloads fail, startup warning shown | no deno/node/bun | install runtime or place `deno.exe` in `bin/` |
| HLS / merge fails | no ffmpeg | stage `ffmpeg.exe` + `ffprobe.exe` in `bin/` |
| Selenium fallback fails | Chrome not installed | install Chrome |
| `developer.json` not loaded (frozen) | file not beside exe or in AppData | rebuild with repo-root `developer.json`, or copy to `release/Link2Vid/` or `%APPDATA%\Link2Vid\` |
| `Failed to load Python DLL` under `build\` | ran a PyInstaller stub before cleanup, or old build folder | run `build_windows.bat` again; start via `release\Link2Vid\Link2Vid.bat` |
| Antivirus quarantine | unsigned PyInstaller build | restore from AV; consider code signing |

## Maintainer notes

- Bundle composition lives in `packaging/link2vid.spec` (committed; exception in `.gitignore`).
- When adding runtime Python dependencies, update the spec hidden imports and `tests/test_packaging_contract.py` together.
- Build outputs (`release/`, `build/`) stay local and are gitignored.
