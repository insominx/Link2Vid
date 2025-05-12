# Link2Vid

Link2Vid is a desktop application for downloading videos from various platforms using a simple and modern graphical interface. It leverages [yt-dlp](https://github.com/yt-dlp/yt-dlp) for video extraction and download, and is built with [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) for a sleek, dark-themed UI.

## Features
- Download videos from a wide range of sites supported by yt-dlp
- Modern, dark-themed GUI
- Select and preview available video formats
- Choose download directory
- Progress bar and log output

## Requirements
- Python 3.8+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)

Install dependencies with:
```bash
pip install -r requirements.txt
```

## Usage
1. Run the application:
   ```bash
   @echo off
   pythonw video_downloader.py
   ```
2. Paste a video or playlist URL into the input field.
3. Click **Fetch Videos** to load available videos and formats.
4. Select a video from the list.
5. Choose a download folder.
6. Click **Download Selected** to start downloading.

## License
This project is licensed under the MIT License. See [LICENSE.md](LICENSE.md) for details.
