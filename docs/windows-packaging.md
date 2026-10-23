# Windows packaging (PyInstaller onedir)

Doggybag can be built as a portable Windows folder. This is optional; the source + venv workflow (`setup_doggybag.bat`, `run_doggybag.bat`) remains unchanged.

## Where the app lives after a build

| Path | Purpose |
|---|---|
| `release/Doggybag/` | **The packaged app** — zip or copy this folder |
| `release/Doggybag/Doggybag.bat` | **Double-click this to start** |
| `build/` | Temporary PyInstaller output — **not runnable**, cleaned after each build |

There is no valid `Doggybag.exe` under `build/` after `build_windows.bat` finishes.

## Prerequisites

**Build machine (Windows 10+ x64):**

- Python 3.10+ with project dependencies installed (`setup_doggybag.bat` or equivalent venv + `pip install -r requirements.txt`)
- PyInstaller is installed automatically by `build_windows.bat` from `packaging/requirements-build.txt`

**End-user machine (frozen build):**

- **Chrome** — required for Selenium fallback on authenticated pages
- **JS runtime** — `deno`, `node`, or `bun` on PATH or in `<app_dir>/bin/` for reliable EJS challenge handling
- **ffmpeg** — on PATH or in `<app_dir>/bin/` for HLS merges and Best (A+V) flows
- Installed browsers — for automatic cookie extraction (same as dev mode)
- Network access on first use of each Whisper model; weights are cached at `%LOCALAPPDATA%\Doggybag\models`
- Optional compatible NVIDIA driver/CUDA runtime for GPU transcription; Doggybag selects CPU when CTranslate2 reports no CUDA device

The build script stages `ffmpeg.exe` and `ffprobe.exe` into `release/Doggybag/bin/` when available. On Windows it prefers the real Chocolatey package binaries under `%ChocolateyInstall%\lib\ffmpeg\tools\ffmpeg\bin\` when present, because Chocolatey's PATH shims are not runnable outside that toolchain. It falls back to the first `where ffmpeg` / `where ffprobe` hits, then runs `-version` on both staged copies and removes them if validation fails. If the builder lacks ffmpeg, the build still succeeds; place binaries manually before distributing.

If `developer.json` exists in the repo root at build time, it is copied into `release/Doggybag/` automatically.

Layout:

```
release/Doggybag/
  Doggybag.bat      # start here
  Doggybag.exe
  README.txt
  developer.json    # optional, copied from repo root when present
  bin/
    ffmpeg.exe
    ffprobe.exe
    deno.exe        # optional, manual
  _internal/        # PyInstaller payload (required)
```

At startup, `doggybag/core/runtime.py` prepends `<app_dir>/bin` to `PATH` so existing `shutil.which("ffmpeg")` checks find sidecars without code changes elsewhere.

## Build steps

From the repo root:

```bat
build_windows.bat
```

This will:

1. Install pinned PyInstaller into the active venv (or system Python)
2. Build into `release/Doggybag/`
3. Add `Doggybag.bat`, `README.txt`, and optional `developer.json`
4. Stage ffmpeg/ffprobe into `bin/` when on the builder PATH
5. Run an import smoke test, including Faster Whisper and its native/dynamic dependency closure
6. Open `release/Doggybag` in Explorer

Post-build smoke (also run automatically by `build_windows.bat`):

```bat
packaging\smoke_frozen.bat
```

**Launch the app:**

```bat
release\Doggybag\Doggybag.bat
```

Or from the repo root: `run_doggybag_packaged.bat`

Desktop shortcut: `create_doggybag_release_shortcut.ps1`

## Distribution

Zip the entire `release/Doggybag/` folder for a portable distribution. There is no installer or code signing in v1.

Whisper model weights are deliberately not in that folder. Source and frozen builds share `%LOCALAPPDATA%\Doggybag\models`, so distributing a new build does not duplicate models. A machine that has not cached the selected model must be online for its first transcription.

Windows SmartScreen may warn on unsigned executables. Users can choose "More info" → "Run anyway", or you can code-sign in a follow-up release process.

## Configuration (`developer.json`)

Schema is unchanged from dev mode. Discovery order:

| mode | search order |
|---|---|
| frozen | 1. `<exe_dir>/developer.json` 2. `%APPDATA%/Doggybag/developer.json` |
| dev | 1. current working directory 2. repo/entry script directory 3. `%APPDATA%/Doggybag/developer.json` |

Frozen mode does not depend on shortcut "Start in" directory. For dev shortcuts, keep working directory at the repo root (see `create_doggybag_shortcut.ps1`).

On first launch, the startup log includes a bootstrap line:

```
runtime: frozen=True app_dir=... developer.json=...|none
```

## Desktop shortcut (frozen build)

Use `create_doggybag_release_shortcut.ps1`, or point a shortcut at `Doggybag.bat` with "Start in" set to the `release/Doggybag` folder.

## Troubleshooting

| symptom | likely cause | action |
|---|---|---|
| Fetch fails immediately after build | missing hidden import | update `packaging/doggybag.spec` and `tests/test_packaging_contract.py`; rebuild |
| JS-runtime-required downloads fail, startup warning shown | no deno/node/bun | install runtime or place `deno.exe` in `bin/` |
| HLS / merge fails | no ffmpeg | stage `ffmpeg.exe` + `ffprobe.exe` in `bin/` |
| Selenium fallback fails | Chrome not installed | install Chrome |
| Whisper controls are disabled | Faster Whisper native import/probe failed | run `Doggybag.exe --smoke`; rebuild and inspect the first failed module |
| First Whisper run is slow or fails offline | selected model is not cached | connect once and retry; keep `%LOCALAPPDATA%\Doggybag\models` for later offline use |
| Whisper reports a CUDA model-loading error | CTranslate2 reported a GPU, but its driver/runtime is incompatible | install a CTranslate2-compatible NVIDIA driver/runtime; these errors are surfaced rather than retried |
| `developer.json` not loaded (frozen) | file not beside exe or in AppData | rebuild with repo-root `developer.json`, or copy to `release/Doggybag/` or `%APPDATA%\Doggybag\` |
| `Failed to load Python DLL` under `build\` | ran a PyInstaller stub before cleanup, or old build folder | run `build_windows.bat` again; start via `release\Doggybag\Doggybag.bat` |
| Antivirus quarantine | unsigned PyInstaller build | restore from AV; consider code signing |

## Maintainer notes

- Bundle composition lives in `packaging/doggybag.spec` (committed; exception in `.gitignore`).
- The spec collects the Faster Whisper, CTranslate2, PyAV, ONNX Runtime, Tokenizers, and Hugging Face import/data/native closure. Do not add model weights or NVIDIA runtime packages to the portable payload.
- When adding runtime Python dependencies, update the spec hidden imports and `tests/test_packaging_contract.py` together.
- Build outputs (`release/`, `build/`) stay local and are gitignored.
