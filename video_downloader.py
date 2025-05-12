import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import yt_dlp
import os
import tkinter as tk
import sys

ctk.set_appearance_mode("dark")  # "dark" or "light"
ctk.set_default_color_theme("blue")  # "blue", "green", "dark-blue"

class VideoDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title('Video Downloader')
        self.root.geometry('1100x800')
        self.root.minsize(900, 700)
        self.video_entries = []
        self.selected_index = None

        font_big = ("Arial", 22)
        font_med = ("Arial", 16)
        font_small = ("Consolas", 12)

        # Main frame for padding
        main_frame = ctk.CTkFrame(root)
        main_frame.pack(fill="both", expand=True, padx=30, pady=30)

        # URL Section
        url_label = ctk.CTkLabel(main_frame, text='Enter URL:', font=font_big)
        url_label.pack(anchor='w', pady=(0, 8))
        self.url_entry = ctk.CTkEntry(main_frame, width=600, font=font_med)
        self.url_entry.pack(fill="x", pady=(0, 12))
        self.url_entry.bind('<KeyRelease>', self.update_button_states)
        self.fetch_btn = ctk.CTkButton(main_frame, text='Fetch Videos', command=self.fetch_videos, font=font_med, height=40, width=200, state="disabled")
        self.fetch_btn.pack(pady=(0, 18))

        # Video Listbox
        listbox_frame = ctk.CTkFrame(main_frame)
        listbox_frame.pack(fill="both", expand=True, pady=(0, 18))
        self.listbox = tk.Listbox(listbox_frame, width=120, height=12, font=font_small)
        self.listbox.pack(fill="both", expand=True, padx=4, pady=4)
        self.listbox.bind('<<ListboxSelect>>', self.on_select)

        # Folder selection row
        folder_row = ctk.CTkFrame(main_frame)
        folder_row.pack(fill="x", pady=(0, 18))
        ctk.CTkLabel(folder_row, text='Select download folder:', font=font_big).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.folder_entry = ctk.CTkEntry(folder_row, width=400, font=font_med)
        self.folder_entry.grid(row=0, column=1, padx=(0, 10))
        self.folder_entry.bind('<KeyRelease>', self.update_button_states)
        self.browse_btn = ctk.CTkButton(folder_row, text='Browse', command=self.browse_folder, font=font_med, height=32, width=100)
        self.browse_btn.grid(row=0, column=2)
        folder_row.grid_columnconfigure(1, weight=1)

        # Download button
        self.download_btn = ctk.CTkButton(main_frame, text='Download Selected', command=self.download_selected, font=font_big, height=50, width=300, state="disabled")
        self.download_btn.pack(pady=(0, 18))

        # Progress bar
        self.progress = ctk.DoubleVar()
        self.progress_bar = ctk.CTkProgressBar(main_frame, variable=self.progress, width=800, height=24)
        self.progress_bar.pack(fill="x", pady=(0, 18))
        self.progress_bar.set(0)

        # Output box
        self.output_text = ctk.CTkTextbox(main_frame, height=120, font=font_small)
        self.output_text.pack(fill="both", expand=True, pady=(0, 0))
        self.output_text.configure(state='disabled')

        self.update_button_states()

    def log(self, message):
        self.output_text.configure(state='normal')
        self.output_text.insert('end', message + '\n')
        self.output_text.see('end')
        self.output_text.configure(state='disabled')

    def update_button_states(self, event=None):
        url = self.url_entry.get().strip()
        folder = self.folder_entry.get().strip()
        has_url = bool(url)
        has_folder = bool(folder)
        has_selection = self.selected_index is not None and self.selected_index > 1  # skip header lines
        # Fetch Videos button
        if has_url:
            self.fetch_btn.configure(state="normal")
        else:
            self.fetch_btn.configure(state="disabled")
        # Download Selected button
        if has_selection and has_folder:
            self.download_btn.configure(state="normal")
        else:
            self.download_btn.configure(state="disabled")

    def fetch_videos(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning('Input Error', 'Please enter a URL.')
            return
        self.listbox.delete(0, 'end')
        self.listbox.insert('end', 'Fetching...')
        self.root.update()
        try:
            self.video_entries = self.get_video_info(url)
            self.listbox.delete(0, 'end')
            header = f"{'Title':50.50} {'Fmt':>6} {'Ext':>4} {'Size':>10}"
            self.listbox.insert('end', header)
            self.listbox.insert('end', "-" * 80)
            for entry in self.video_entries:
                title = entry.get('title', 'No Title')
                formats = entry.get('formats', [])
                if formats:
                    best = formats[-1]
                    size = best.get('filesize') or best.get('filesize_approx') or 'N/A'
                    size_str = f"{size}" if isinstance(size, int) else size
                    fmt = best.get('format_id', '')
                    ext = best.get('ext', '')
                    item = f"{title[:50]:50.50} {fmt:>6} {ext:>4} {size_str:>10}"
                else:
                    item = f"{title[:50]:50.50} {'':>6} {'':>4} {'':>10}"
                self.listbox.insert('end', item)
            self.log(f"Found {len(self.video_entries)} video(s).")
        except Exception as e:
            self.listbox.delete(0, 'end')
            self.listbox.insert('end', f"Error: {e}")
            self.listbox.itemconfig('end', {'fg': 'red'})
            self.log(f"Error: {e}")
        self.selected_index = None
        self.update_button_states()

    def get_video_info(self, url):
        ydl_opts = {'quiet': True, 'skip_download': True, 'extract_flat': False}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:
                return info['entries']
            else:
                return [info]

    def on_select(self, event):
        selection = self.listbox.curselection()
        if selection:
            self.selected_index = selection[0]
        else:
            self.selected_index = None
        self.update_button_states()

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_entry.delete(0, 'end')
            self.folder_entry.insert(0, folder)
        self.update_button_states()

    def download_selected(self):
        if self.selected_index is None:
            messagebox.showwarning('Selection Error', 'Please select a video from the list.')
            return
        folder = self.folder_entry.get().strip()
        if not folder:
            messagebox.showwarning('Folder Error', 'Please select a download folder.')
            return
        entry = self.video_entries[self.selected_index-2]  # skip header and separator
        url = entry.get('webpage_url', self.url_entry.get().strip())
        formats = entry.get('formats', [])
        if formats:
            best = formats[-1]
            format_id = best.get('format_id')
        else:
            format_id = None
        self.progress.set(0)
        self.progress_bar.set(0)
        self.log(f"Starting download: {entry.get('title', 'No Title')}")
        threading.Thread(target=self.download_video, args=(url, format_id, folder), daemon=True).start()

    def download_video(self, url, format_id, output_path):
        def progress_hook(d):
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
                downloaded = d.get('downloaded_bytes', 0)
                percent = downloaded / total
                self.progress.set(percent)
                self.progress_bar.set(percent)
            elif d['status'] == 'finished':
                self.progress.set(1)
                self.progress_bar.set(1)
                self.log('Download complete!')
        ydl_opts = {
            'format': format_id,
            'outtmpl': os.path.join(output_path, '%(title)s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'quiet': True
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            self.log(f"Download error: {e}")

if __name__ == '__main__':
    root = ctk.CTk()
    app = VideoDownloaderApp(root)
    root.mainloop()
    sys.exit(0) 