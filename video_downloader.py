import argparse
import os
import sys


SMOKE_IMPORTS = (
    "webview",
    "yt_dlp",
    "yt_dlp_ejs",
    "faster_whisper",
    "ctranslate2",
    "av",
    "onnxruntime",
    "tokenizers",
    "huggingface_hub",
)


def _run_smoke():
    """Headless import check for source and frozen builds.

    Imports the Whisper native/dynamic closure without starting the UI or
    loading/downloading a model.
    """
    import importlib

    for name in SMOKE_IMPORTS:
        try:
            importlib.import_module(name)
        except Exception as exc:
            print(f"[ERROR] Smoke import failed for {name}: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    for name in ("doggybag.app.controller", "doggybag.ui.webview_app"):
        try:
            importlib.import_module(name)
        except Exception as exc:
            print(f"[ERROR] Smoke import failed for {name}: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    from doggybag.ui import WEB_ROOT

    for asset in ("index.html", "styles.css", "js/main.js", "vendor/preact-htm.module.js", "assets/mark.png", "assets/dog.png"):
        if not (WEB_ROOT / asset).is_file():
            print(f"[ERROR] Smoke check failed: missing UI asset {WEB_ROOT / asset}", file=sys.stderr)
            raise SystemExit(1)

    from doggybag.core.runtime import startup_summary

    print(startup_summary())
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Doggybag")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="headless import check for frozen builds",
    )
    parser.add_argument(
        "--devtools",
        action="store_true",
        help="open the WebView developer tools",
    )
    args = parser.parse_args(argv)

    from doggybag.core.runtime import bootstrap_runtime

    bootstrap_runtime()

    if args.smoke:
        return _run_smoke()

    from doggybag.core.runtime import migrate_legacy_data
    from doggybag.ui.webview_app import run

    code = run(debug=args.devtools, startup_notes=migrate_legacy_data())
    # Closing the window ends the app. Fetch/download workers are not
    # interruptible, so do not let interpreter shutdown wait on them.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:  # None under pythonw / windowed builds
            stream.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
