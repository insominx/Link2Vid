import sys

import customtkinter as ctk

from link2vid.ui.main_window import VideoDownloaderApp


if __name__ == '__main__':
    root = ctk.CTk()
    app = VideoDownloaderApp(root)
    root.mainloop()
    sys.exit(0)
