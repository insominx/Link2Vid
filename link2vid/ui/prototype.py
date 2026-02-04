"""Prototype window for Link2Vid UI components."""

from __future__ import annotations

import customtkinter as ctk

from .components import FooterBar, LogDrawer, VideoCard


def run() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("Link2Vid UI Prototype")
    root.geometry("1100x800")
    root.minsize(900, 700)

    main = ctk.CTkFrame(root)
    main.pack(fill="both", expand=True, padx=24, pady=24)

    header = ctk.CTkLabel(main, text="Link2Vid UI Prototype", font=("Arial", 20, "bold"))
    header.pack(anchor="w", pady=(0, 12))

    scroll = ctk.CTkScrollableFrame(main)
    scroll.pack(fill="both", expand=True, pady=(0, 16))

    log_drawer = LogDrawer(main, collapsed=True)

    def handle_download(card: VideoCard, fmt: str) -> None:
        card.set_status(f"Queued ({fmt})")
        log_drawer.append(f"Queued: {card.get_title()} [{fmt}]")

    format_options = [
        ("Best (A+V)", "bestvideo+bestaudio/best"),
        ("Best video", "bestvideo"),
        ("Best audio", "bestaudio"),
    ]

    samples = [
        {
            "title": "Sample video title with a longer line that wraps naturally",
            "meta": "YouTube · 12:34 · Channel Name",
        },
        {
            "title": "Short clip title",
            "meta": "Vimeo · 02:10 · Studio",
        },
        {
            "title": "Playlist item example",
            "meta": "X/Twitter · 00:42 · Account",
        },
    ]

    for item in samples:
        card = VideoCard(
            scroll,
            title=item["title"],
            metadata=item["meta"],
            format_options=format_options,
            on_download=handle_download,
        )
        card.pack(fill="x", padx=8, pady=8)

    footer = FooterBar(
        main,
        output_path=r"C:\\Downloads",
        cookies_path="cookies.txt",
        on_change_output=lambda: log_drawer.append("Output change clicked."),
        on_change_cookies=lambda: log_drawer.append("Cookies change clicked."),
        on_copy_diagnostics=lambda: log_drawer.append("Diagnostics copied."),
    )
    footer.pack(fill="x", pady=(0, 12))

    log_drawer.pack(fill="x")
    log_drawer.append("Prototype ready.")

    root.mainloop()


if __name__ == "__main__":
    run()
