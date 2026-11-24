# SMdownloader

**Social Media Downloader**, Download videos and audio from YouTube, Instagram, Facebook, TikTok, LinkedIn and other sites supported by yt-dlp. It runs on your computer and opens in your browser.

## Installation (first time only)

1. Right click `install-requirements.bat` file and choose **Run as administrator**; Wait until the download is complete. 
2. Make the same thing to`config.bat` file.

## Running

- run `runSMdownloader.bat` file as administrator.A CMD and browser windows will open (the browser window contains the app GUI, and the CMD window contains the local server)
- if the server still ran, you can open the app GUI window again with this link http://127.0.0.1:5000.

- To stop the app, close the CMD window.

## Updating

Run `config.bat` again. Do this whenever downloads stop working, because yt-dlp needs frequent updates.

## Troubleshooting

- **Python/FFmpeg is not found:** try to open `install-requirements.bat` file again; if they are still not found try to install them manually.
- **Downloading fails** make sure Deno is installed, then run `config.bat` again.
- **Port 5000 in use:** the app is already running, or another program uses that port.

## Features

- Download a single video or pick videos from a playlist.
- Quality
    - YouTube: High (best available), 2160p, 1440p, 1080p, 720p, 480p, 360p, or audio only (MP3, 192 kbps)
    - Other platforms: only High and audio.
- Video output is always MP4
- Clip a section with a timeline slider or manual start/end times
- Optional custom file name; playlist downloads can use a custom folder name
- Subtitles (YouTube only), embedded into the MP4
- Queue with live progress, speed, ETA, pause/resume, cancel and retry
- Configurable download folder and maximum simultaneous downloads (default 3)
- Files are sorted into subfolders by platform (YouTube, Instagram, Facebook, TikTok, LinkedIn). Other sites go in the root download folder.
    - **the root folder:  Videos\SMdownloads**

## How it works

- Front end: HTML, CSS, JavaScript
- Back end: Python with Flask (web server) and yt-dlp (downloads)
- FFmpeg merges video and audio, converts to MP3 and embeds subtitles
- The app listens only on 127.0.0.1, port 5000

## Legal

Download only content you own or have permission to download.
