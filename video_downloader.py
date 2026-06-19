import argparse
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Link2Vid")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="headless import check for frozen builds",
    )
    args = parser.parse_args(argv)

    from link2vid.core.runtime import bootstrap_runtime, startup_summary

    bootstrap_runtime()

    if args.smoke:
        import customtkinter as ctk  # noqa: F401
        import yt_dlp  # noqa: F401
        import yt_dlp_ejs  # noqa: F401
        from link2vid.ui.main_window import VideoDownloaderApp  # noqa: F401

        print(startup_summary())
        return 0

    import customtkinter as ctk
    from link2vid.ui.main_window import VideoDownloaderApp

    root = ctk.CTk()
    VideoDownloaderApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
