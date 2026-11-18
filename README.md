# SMdownloader

Download videos and audio from YouTube, Instagram, Facebook, TikTok, LinkedIn and other sites supported by yt-dlp. It runs on your computer and opens in your browser.

## Installation (first time only)

1. Download the whole code and extract it.
2. Open **Command Prompt** (CMD) and run these three commands: (If you already have python skip the first command)
   `winget install Python.Python.3.13`
   `winget install Gyan.FFmpeg`
   `winget install DenoLand.Deno`
3. **Close Command Prompt.** Double-click `config.bat` and wait until it says "Done".

If Windows shows "Windows protected your PC", click **More info → Run anyway**;Or run it as administrator.

## Running

Double-click `runSMdownloader.bat`. Your browser opens the app. If it doesn't, go to http://127.0.0.1:5000.
To stop, close the black window (close CMD window).

## Updating

Run `config.bat` again. Do this whenever downloads stop working, because yt-dlp needs frequent updates.

## Troubleshooting

- **"Python/FFmpeg is not installed":** close and reopen the window after installing. If it still fails, sign out and back in.
- **YouTube fails or shows few qualities:** make sure Deno is installed, then run `config.bat` again.
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
