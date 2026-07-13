import argparse
import sys


SMOKE_IMPORTS = (
    "customtkinter",
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

    try:
        from link2vid.ui.main_window import VideoDownloaderApp  # noqa: F401
    except Exception as exc:
        print(f"[ERROR] Smoke import failed for link2vid.ui.main_window: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    from link2vid.core.runtime import startup_summary

    print(startup_summary())
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Link2Vid")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="headless import check for frozen builds",
    )
    args = parser.parse_args(argv)

    from link2vid.core.runtime import bootstrap_runtime

    bootstrap_runtime()

    if args.smoke:
        return _run_smoke()

    import customtkinter as ctk
    from link2vid.ui.main_window import VideoDownloaderApp

    root = ctk.CTk()
    VideoDownloaderApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
