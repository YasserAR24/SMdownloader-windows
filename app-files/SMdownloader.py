"""
SMdownloader - video + audio downloader with a web page (Flask + yt-dlp).

Run:    python SMdownloader.py
Open:   http://127.0.0.1:5000

Needs:  pip install flask yt-dlp     (+ FFmpeg on PATH for MP4 merging / MP3 conversion)

Files (keep this layout):
    SMdownloader.py          this file: the server and the download logic
    templates/index.html     the page
    static/base.css          shared theme (all colors live here)
    static/style.css         page layout
    static/app.js            page behavior
"""

import glob
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from flask import Flask, abort, jsonify, make_response, render_template, request, send_file

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Loopback only. Don't expose this to a network: anyone who can reach it can
# make your machine download things and read the downloaded files.
HOST = "127.0.0.1"
PORT = 5000

APP_NAME = "SMdownloader"

QUALITIES = ["2160", "1440", "1080", "720", "480", "360"]
# "high" = best video + audio the site offers, "audio" = MP3 only
QUALITY_OPTIONS = (
    [("high", "High (best available)")]
    + [(q, f"{q}p") for q in QUALITIES]
    + [("audio", "Audio (MP3)")]
)
QUALITY_CHOICES = [value for value, _ in QUALITY_OPTIONS]
DEFAULT_QUALITY = "high"

REQUIRED_FILES = [
    os.path.join("templates", "index.html"),
    os.path.join("static", "base.css"),
    os.path.join("static", "style.css"),
    os.path.join("static", "app.js"),
]

MAX_PLAYLIST_ITEMS = 200  # longer playlists are cut here (keeps the picker usable)
MAX_CUSTOM_TITLE_LENGTH = 500
DEFAULT_MAX_CONCURRENT_DOWNLOADS = 3

DEFAULT_OUTPUT_ROOT = os.environ.get("SM_DOWNLOAD_DIR") or os.path.join(
    os.path.expanduser("~"), "Videos", "SMdownloads"
)
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".config", "smdownloader", "settings.json")


def load_output_root():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as settings_file:
            saved = json.load(settings_file).get("output_root")
        if isinstance(saved, str) and saved.strip():
            return os.path.abspath(os.path.expanduser(saved.strip()))
    except (OSError, ValueError, AttributeError):
        pass
    return os.path.abspath(os.path.expanduser(DEFAULT_OUTPUT_ROOT))


def load_max_concurrent_downloads():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as settings_file:
            value = json.load(settings_file).get("max_concurrent_downloads")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_MAX_CONCURRENT_DOWNLOADS


OUTPUT_ROOT = load_output_root()
MAX_CONCURRENT_DOWNLOADS = load_max_concurrent_downloads()
os.makedirs(OUTPUT_ROOT, exist_ok=True)

CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "sm-downloader"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_URL_LENGTH = 2048
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# ----------------------------------------------------------------------------
# Job state (in memory; lost when the server stops)
#
# status: inspecting -> (choosing ->) queued -> downloading <-> paused -> processing -> done
#         any step can end as error; the user can end it as cancelled/removed.
# ----------------------------------------------------------------------------
jobs = {}  # id -> job dict, kept in creation order
lock = threading.Lock()
scheduler_condition = threading.Condition(lock)
work_queue = queue.Queue()
inspect_slots = threading.BoundedSemaphore(4)
FINISHED = ("done", "error", "cancelled")


def public(job):
    """Fields the browser is allowed to see (never the server-side file path)."""
    keys = ("id", "url", "mode", "quality", "platform", "status",
            "title", "percent", "size_done", "size_total", "speed", "eta", "error",
            "resume_requested", "download_subtitles", "custom_title", "download_range")
    data = {k: job[k] for k in keys}
    data["has_file"] = bool(job["file"])
    data["count"] = len(job["entries"]) if job["entries"] else None
    return data


def clean_error(exc, limit=500):
    return ANSI_RE.sub("", str(exc)).strip()[:limit]


def safe_custom_title(title):
    if not title:
        return None
    title = yt_dlp.utils.sanitize_filename(title, restricted=False).strip().rstrip(".")
    title = title.encode("utf-8")[:120].decode("utf-8", "ignore").strip(" .")
    return title or None


def parse_time_input(value):
    parts = value.split(":")
    if len(parts) not in (2, 3) or any(not part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if len(numbers) == 2:
        minutes, seconds = numbers
        if seconds >= 60:
            return None
        return minutes * 60 + seconds
    hours, minutes, seconds = numbers
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def make_job(url, quality, status, title=None, download_subtitles=False, custom_title=None, download_range=None):
    """Create and register a job. The caller must hold `lock`."""
    mode = "audio" if quality == "audio" else "video"
    platform = platform_check(url)
    output_dir = os.path.join(OUTPUT_ROOT, platform.lower()) if platform else OUTPUT_ROOT
    job = {
        "id": uuid.uuid4().hex[:12],
        "url": url,
        "mode": mode,
        "quality": quality,
        "platform": platform,
        "download_subtitles": bool(download_subtitles),
        "custom_title": safe_custom_title(custom_title),
        "download_range": download_range,
        "output_root": OUTPUT_ROOT,
        "output_dir": output_dir,
        "status": status,
        "title": title,
        "percent": 0.0,
        "size_done": 0,          # bytes downloaded so far (all streams together)
        "size_total": None,      # total bytes if the site tells us, else None; final file size when done
        "speed": None,
        "eta": None,
        "error": None,
        "file": None,
        "cancel": False,
        "resume_event": threading.Event(),
        "resume_requested": False,
        "entries": None,         # playlist items, while status == "choosing"
        "playlist_title": None,
        "truncated": False,
    }
    job["resume_event"].set()
    jobs[job["id"]] = job
    return job


# ----------------------------------------------------------------------------
# Platform + options
# ----------------------------------------------------------------------------
def platform_check(url):
    host = (urlparse(url).hostname or "").lower()

    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        return "YouTube"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "Instagram"
    if host == "facebook.com" or host.endswith(".facebook.com") or host == "fb.watch":
        return "Facebook"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "TikTok"

    return None


def build_opts(job, on_progress, on_postprocess):
    out_dir = job["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    ydl_opts = {
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        # one job = one video: playlists are expanded by the picker, not here
        "noplaylist": True,
        "playlistend": 1,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # yt-dlp retries only transient errors (network, timeouts)
        "retries": 5,
        "fragment_retries": 5,
        "retry_sleep_functions": {"http": lambda n: 2, "fragment": lambda n: 2},
        "progress_hooks": [on_progress],
        "postprocessor_hooks": [on_postprocess],
    }

    if job.get("download_range"):
        ydl_opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [job["download_range"]])
        ydl_opts["force_keyframes_at_cuts"] = True

    if job.get("download_subtitles"):
        ydl_opts.update({
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "best",
        })

    if job.get("custom_title"):
        filename_title = job["custom_title"].replace("%", "%%")
        ydl_opts["outtmpl"] = os.path.join(out_dir, f"{filename_title}.%(ext)s")
    elif job["platform"] == "Instagram":
        ydl_opts["outtmpl"] = os.path.join(out_dir, "%(title)s [%(id)s].%(ext)s")

    if job["mode"] == "audio":
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    else:
        # always output MP4 (merge video+audio into MP4, remux single files to MP4)
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
        q = job["quality"]
        if q == "high":
            ydl_opts["format"] = "bv*+ba/b"
        else:
            # works on any site; falls back to "best" when it has no such stream/height
            ydl_opts["format"] = f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"

        if job.get("download_subtitles"):
            ydl_opts["writesubtitles"] = True
            ydl_opts["writeautomaticsub"] = True
            ydl_opts["subtitleslangs"] = ["en", "en-US"]
            ydl_opts["subtitlesformat"] = "srt/best"
            ydl_opts["postprocessors"].append({"key": "FFmpegEmbedSubtitle"})

    return ydl_opts


# ----------------------------------------------------------------------------
# Step 1: look at the link (single video or playlist?)
# ----------------------------------------------------------------------------
def entry_url(entry):
    for key in ("url", "webpage_url"):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    if entry.get("ie_key") == "Youtube" and entry.get("id"):
        return f"https://www.youtube.com/watch?v={entry['id']}"
    return None


def collect_entries(info):
    """(entries, truncated) for a playlist info dict. Entries without a usable URL are dropped."""
    entries = []
    truncated = False
    for raw in info.get("entries") or []:
        if len(entries) >= MAX_PLAYLIST_ITEMS:
            truncated = True
            break
        if not raw:
            continue
        url = entry_url(raw)
        if not url:
            continue
        duration = raw.get("duration")
        entries.append({
            "title": str(raw.get("title") or url)[:300],
            "url": url,
            "duration": duration if isinstance(duration, (int, float)) else None,
        })
    return entries, truncated


def inspect_url(job_id):
    with lock:
        job = jobs.get(job_id)
        if job is None or job["status"] != "inspecting":
            return
        url = job["url"]

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "extract_flat": "in_playlist",           # list playlist items without resolving each one
        "playlistend": MAX_PLAYLIST_ITEMS + 1,   # +1 so we can tell the list was cut
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
            is_playlist = info.get("_type") == "playlist"
            entries, truncated = collect_entries(info) if is_playlist else ([], False)
    except Exception as e:  # DownloadError, or anything unexpected: never kill the thread silently
        with lock:
            job = jobs.get(job_id)
            if job is not None and job["status"] == "inspecting":
                job["status"] = "error"
                job["error"] = clean_error(e)
        return

    with lock:
        job = jobs.get(job_id)
        if job is None or job["status"] != "inspecting":
            return  # removed while we were looking
        if is_playlist and len(entries) > 1:
            job["entries"] = entries
            job["truncated"] = truncated
            job["playlist_title"] = str(info.get("title") or "")[:300] or None
            job["title"] = job["playlist_title"]
            job["status"] = "choosing"  # waits for the user's selection
            return
        if is_playlist and not entries:
            job["status"] = "error"
            job["error"] = "No videos were found at this link."
            return
        # one video (or a playlist with a single video): download the link as given
        title = entries[0]["title"] if entries else info.get("title")
        job["title"] = str(title)[:300] if title else None
        job["status"] = "queued"
    work_queue.put(job_id)
    with scheduler_condition:
        scheduler_condition.notify_all()


def start_inspect(job_id):
    def run():
        with inspect_slots:
            inspect_url(job_id)

    threading.Thread(target=run, daemon=True, name="inspect").start()


# ----------------------------------------------------------------------------
# Step 2: download (one worker, one video at a time)
# ----------------------------------------------------------------------------
def final_path(info):
    """Path of the finished file (after merge/convert), or None."""
    if not info:
        return None
    for item in info.get("requested_downloads") or []:
        if item.get("filepath"):
            return item["filepath"]
    for entry in info.get("entries") or []:
        path = final_path(entry)
        if path:
            return path
    return info.get("filepath")


def remove_partials(paths):
    """Delete leftovers of an aborted download (.part, .ytdl, fragment files)."""
    for path in paths:
        for leftover in glob.glob(glob.escape(path) + "*"):
            try:
                os.remove(leftover)
            except OSError:
                pass


class ProgressTracker:
    """Turns yt-dlp's per-file progress events into one overall figure.

    A merged download (video + audio) arrives as two separate files. Read naively,
    the bar would run to 100% for the video and restart for the audio, and the size
    would only describe the file currently downloading. This adds the finished files
    to the current one, and adds the sizes the site announced for the files still to come.
    """

    def __init__(self, job):
        self.finished_bytes = 0   # bytes of the files already downloaded
        self.streams_done = 0     # how many files are already downloaded
        self.announced = []       # size the site announced for each file to download (None = unknown)
        self.job = job
        self.range_monitor_stop = threading.Event()
        self.range_monitor = None
        self.range_temp_path = None
        self.range_final_path = None
        self.range_total = None

    def plan(self, info):
        """Called once, before the download starts, with yt-dlp's full plan (it may be 2 files)."""
        streams = info.get("requested_formats") or [info]
        self.announced = [s.get("filesize") or s.get("filesize_approx") for s in streams]
        if not self.job.get("download_range") or self.range_monitor is not None:
            return

        start, end = self.job["download_range"]
        duration = info.get("duration") or end
        full_size = sum(
            size for stream in streams
            if isinstance((size := stream.get("filesize") or stream.get("filesize_approx")), (int, float))
        )
        if full_size:
            self.range_total = max(1, int(full_size * (end - start) / duration))
        else:
            bitrate = sum(
                stream.get("tbr") or 0
                for stream in streams
                if isinstance(stream.get("tbr"), (int, float))
            )
            if bitrate > 0:
                self.range_total = max(1, int(bitrate * 1000 * (end - start) / 8))

        filename = info.get("_filename")
        if not isinstance(filename, str):
            return
        self.range_final_path = filename
        self.range_temp_path = filename + ".part"
        with lock:
            self.job["size_total"] = self.range_total
        self.range_monitor = threading.Thread(
            target=self._watch_range_file,
            daemon=True,
            name="range-progress",
        )
        self.range_monitor.start()

    def _watch_range_file(self):
        previous_size = 0
        previous_time = time.monotonic()
        while not self.range_monitor_stop.wait(0.5):
            if self.job["status"] not in ("downloading", "paused"):
                return
            size = 0
            for path in (self.range_temp_path, self.range_final_path):
                try:
                    size = os.path.getsize(path)
                    break
                except OSError:
                    continue

            now = time.monotonic()
            elapsed = max(now - previous_time, 0.001)
            if not self.job["resume_event"].is_set():
                previous_size, previous_time = size, now
                continue

            speed = max(0, size - previous_size) / elapsed
            with lock:
                if self.job["status"] == "downloading":
                    self.job["size_done"] = size
                    self.job["speed"] = speed or None
                    if self.range_total:
                        self.job["size_total"] = self.range_total
                        percent = min(99.0, size * 100.0 / self.range_total)
                        self.job["percent"] = max(self.job["percent"], percent)
                        self.job["eta"] = max(0.0, (self.range_total - size) / speed) if speed else None
            previous_size, previous_time = size, now

    def stop_range_monitor(self):
        self.range_monitor_stop.set()

    def update(self, d):
        """Return the new job figures for one yt-dlp event, or None if there is nothing to show."""
        status = d.get("status")
        if status == "finished":
            self.finished_bytes += d.get("total_bytes") or d.get("downloaded_bytes") or 0
            self.streams_done += 1
            return {"size_done": self.finished_bytes}
        if status != "downloading":
            return None

        info = d.get("info_dict") or {}
        # progress events only describe the file being downloaded, never the whole plan
        announced = self.announced or [info.get("filesize") or info.get("filesize_approx")]
        speed = d.get("speed")
        done = self.finished_bytes + (d.get("downloaded_bytes") or 0)

        this_total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if not this_total and self.streams_done < len(announced):
            this_total = announced[self.streams_done]
        later = announced[self.streams_done + 1:]

        result = {"size_done": done, "speed": speed, "size_total": None, "percent": None, "eta": None}
        if this_total and all(later):   # every remaining file has a known size
            total = self.finished_bytes + this_total + sum(later)
            result["size_total"] = total
            result["percent"] = min(100.0, done * 100.0 / total)
            result["eta"] = max(0.0, (total - done) / speed) if speed else d.get("eta")
        return result


class SizePlannerPP(PostProcessor):
    """Hands yt-dlp's download plan to the ProgressTracker just before the download starts.

    This is the only moment the full plan (video file + audio file) is visible:
    yt-dlp's progress events only carry the file currently downloading.
    """

    def __init__(self, tracker):
        super().__init__(None)
        self.tracker = tracker

    def run(self, info):
        self.tracker.plan(info)
        return [], info


def run_job(job):
    partials = set()
    tracker = ProgressTracker(job)

    def on_progress(d):
        if d.get("tmpfilename"):
            partials.add(d["tmpfilename"])
        while not job["resume_event"].wait(timeout=0.25):
            if job["cancel"]:
                raise yt_dlp.utils.DownloadCancelled("Cancelled")
        if job["cancel"]:
            raise yt_dlp.utils.DownloadCancelled("Cancelled")
        info = d.get("info_dict") or {}
        figures = tracker.update(d)
        with lock:
            if info.get("title"):
                job["title"] = info["title"]
            if figures:
                job["size_done"] = max(job["size_done"], figures["size_done"])
                if "size_total" in figures:   # a "downloading" event
                    if figures["size_total"] is not None or not job.get("download_range"):
                        job["size_total"] = figures["size_total"]
                        job["speed"] = figures["speed"]
                        job["eta"] = figures["eta"]
                        if figures["percent"] is not None:
                            job["percent"] = max(job["percent"], figures["percent"])  # never moves backwards

    def on_postprocess(d):
        if d.get("status") == "started" and d.get("postprocessor") != "SizePlanner":
            tracker.stop_range_monitor()
            with lock:
                job["resume_event"].set()
                job["status"] = "processing"
                job["percent"] = 100.0
                job["speed"] = None
                job["eta"] = None

    try:
        with yt_dlp.YoutubeDL(build_opts(job, on_progress, on_postprocess)) as ydl:
            ydl.add_post_processor(SizePlannerPP(tracker), when="before_dl")
            info = ydl.extract_info(job["url"], download=True)
        path = final_path(info)
        try:
            final_size = os.path.getsize(path) if path else None
        except OSError:
            final_size = None
        with lock:
            job["file"] = path
            job["title"] = job["title"] or (info or {}).get("title")
            if final_size:  # the real size on disk (after merging / MP3 conversion)
                job["size_done"] = job["size_total"] = final_size
            job["percent"] = 100.0
            job["speed"] = None
            job["eta"] = None
            job["status"] = "done"
    except yt_dlp.utils.DownloadCancelled:
        remove_partials(partials)
        with lock:
            job["status"] = "cancelled"
            job["speed"] = None
            job["eta"] = None
    except yt_dlp.utils.DownloadError as e:
        with lock:
            job["status"] = "error"
            job["error"] = clean_error(e)
    except Exception as e:  # keep the worker thread alive whatever happens
        with lock:
            job["status"] = "error"
            job["error"] = f"Unexpected error: {clean_error(e, 400)}"
    finally:
        tracker.stop_range_monitor()


def run_scheduled_job(job):
    try:
        run_job(job)
    finally:
        with scheduler_condition:
            scheduler_condition.notify_all()


def worker():
    while True:
        with scheduler_condition:
            active = sum(job["status"] == "downloading" for job in jobs.values())
            if active >= MAX_CONCURRENT_DOWNLOADS:
                scheduler_condition.wait()
                continue

            resume_job = next((
                job for job in jobs.values()
                if job["status"] == "paused" and job["resume_requested"]
            ), None)
            if resume_job is not None:
                resume_job["resume_requested"] = False
                resume_job["status"] = "downloading"
                resume_job["resume_event"].set()
                continue

            try:
                job_id = work_queue.get_nowait()
            except queue.Empty:
                scheduler_condition.wait()
                continue

            job = jobs.get(job_id)
            if job is None or job["status"] != "queued":
                continue
            job["status"] = "downloading"
            threading.Thread(
                target=run_scheduled_job,
                args=(job,),
                daemon=True,
                name="download-job",
            ).start()


def start_worker():
    threading.Thread(target=worker, daemon=True, name="download-scheduler").start()


# ----------------------------------------------------------------------------
# Web app
# ----------------------------------------------------------------------------
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
    static_url_path="/static",
)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0   # edit a css/js file, refresh, see the change


def error(message, status):
    return jsonify({"error": message}), status


@app.before_request
def guard():
    # DNS-rebinding protection: only answer to loopback host names.
    if HOST in LOOPBACK_HOSTS:
        host = (urlparse("//" + request.host).hostname or "").lower()
        if host not in LOOPBACK_HOSTS:
            abort(403)
    # CSRF protection: a custom header forces a CORS preflight, which other
    # sites can't pass, so only this page can change state.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            abort(403)


@app.get("/")
def index():
    response = make_response(render_template(
        "index.html",
        app_name=APP_NAME,
        output_root=OUTPUT_ROOT,
        max_concurrent_downloads=MAX_CONCURRENT_DOWNLOADS,
        quality_options=QUALITY_OPTIONS,
        default_quality=DEFAULT_QUALITY,
    ))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/jobs")
def list_jobs():
    with lock:
        return jsonify([public(j) for j in jobs.values()])


@app.post("/api/video-info")
def video_info():
    data = request.get_json(silent=True)
    url = str(data.get("url") or "").strip() if isinstance(data, dict) else ""
    if not url.startswith(("http://", "https://")) or not urlparse(url).hostname:
        return error("Not a valid URL (must start with http:// or https://).", 400)
    if len(url) > MAX_URL_LENGTH:
        return error("URL is too long.", 400)

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        return error(clean_error(exc), 400)

    if info.get("_type") == "playlist":
        return error("Select a single video URL to choose a time range.", 400)
    duration = info.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        return error("Could not read the video's duration.", 400)
    return jsonify({"duration": int(duration), "title": str(info.get("title") or "")[:300]})


@app.get("/api/settings")
def get_settings():
    with lock:
        return jsonify({
            "output_root": OUTPUT_ROOT,
            "max_concurrent_downloads": MAX_CONCURRENT_DOWNLOADS,
        })


@app.post("/api/settings")
def update_settings():
    global OUTPUT_ROOT, MAX_CONCURRENT_DOWNLOADS
    data = request.get_json(silent=True)
    path = data.get("output_root") if isinstance(data, dict) else None
    if not isinstance(path, str) or not path.strip():
        return error("Enter a download folder path.", 400)
    max_concurrent = data.get("max_concurrent_downloads", MAX_CONCURRENT_DOWNLOADS)
    if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or max_concurrent < 1:
        return error("Maximum simultaneous downloads must be a positive whole number.", 400)

    path = os.path.abspath(os.path.expanduser(path.strip()))
    try:
        os.makedirs(path, exist_ok=True)
        if not os.path.isdir(path) or not os.access(path, os.W_OK):
            return error("The folder is not writable.", 400)
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        temporary = SETTINGS_FILE + ".tmp"
        with open(temporary, "w", encoding="utf-8") as settings_file:
            json.dump({
                "output_root": path,
                "max_concurrent_downloads": max_concurrent,
            }, settings_file)
        os.replace(temporary, SETTINGS_FILE)
    except OSError as exc:
        return error(f"Could not save this folder: {clean_error(exc)}", 400)

    with scheduler_condition:
        OUTPUT_ROOT = path
        MAX_CONCURRENT_DOWNLOADS = max_concurrent
        scheduler_condition.notify_all()
        return jsonify({
            "output_root": OUTPUT_ROOT,
            "max_concurrent_downloads": MAX_CONCURRENT_DOWNLOADS,
        })


@app.post("/api/settings/browse")
def browse_settings():
    with lock:
        initial_dir = OUTPUT_ROOT

    zenity = shutil.which("zenity")
    kdialog = shutil.which("kdialog")
    if zenity:
        command = [
            zenity,
            "--file-selection",
            "--directory",
            "--title=Choose download folder",
            f"--filename={initial_dir.rstrip(os.sep)}{os.sep}",
        ]
    elif kdialog:
        command = [kdialog, "--getexistingdirectory", initial_dir, "--title", "Choose download folder"]
    else:
        return error("No system folder picker is available. You can still enter the path manually.", 503)

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        return error(f"Could not open the folder picker: {clean_error(exc)}", 500)
    if result.returncode not in (0, 1):
        return error(clean_error(result.stderr) or "The folder picker could not be opened.", 500)
    return jsonify({"output_root": result.stdout.strip() if result.returncode == 0 else ""})


@app.post("/api/jobs")
def create_job():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Send a JSON body.", 400)

    url = str(data.get("url") or "").strip()
    if not url.startswith(("http://", "https://")) or not urlparse(url).hostname:
        return error("Not a valid URL (must start with http:// or https://).", 400)
    if len(url) > MAX_URL_LENGTH:
        return error("URL is too long.", 400)

    quality = str(data.get("quality") or DEFAULT_QUALITY)
    if quality not in QUALITY_CHOICES:
        return error("Invalid quality. Use: " + ", ".join(QUALITY_CHOICES) + ".", 400)

    download_subtitles = data.get("download_subtitles", False) if isinstance(data, dict) else False
    if not isinstance(download_subtitles, bool):
        return error("The subtitles option must be true or false.", 400)
    custom_title = data.get("custom_title", "")
    if not isinstance(custom_title, str):
        return error("The custom title must be text.", 400)
    custom_title = custom_title.strip()
    if len(custom_title) > MAX_CUSTOM_TITLE_LENGTH:
        return error(f"The custom title must be {MAX_CUSTOM_TITLE_LENGTH} characters or fewer.", 400)

    start_text = data.get("start_time", "")
    end_text = data.get("end_time", "")
    if not isinstance(start_text, str) or not isinstance(end_text, str):
        return error("Start and end times must be text in M:SS or H:MM:SS format.", 400)
    start_text = start_text.strip()
    end_text = end_text.strip()
    if bool(start_text) != bool(end_text):
        return error("Enter both a start time and an end time.", 400)
    download_range = None
    if start_text:
        start_seconds = parse_time_input(start_text)
        end_seconds = parse_time_input(end_text)
        if start_seconds is None or end_seconds is None:
            return error("Use M:SS or H:MM:SS for start and end times.", 400)
        if end_seconds <= start_seconds:
            return error("End time must be after start time.", 400)
        if quality == "audio":
            return error("Time selection is available for video downloads only.", 400)
        download_range = (start_seconds, end_seconds)

    with lock:
        job = make_job(
            url,
            quality,
            "inspecting",
            download_subtitles=download_subtitles,
            custom_title=custom_title,
            download_range=download_range,
        )
        view = public(job)
    start_inspect(job["id"])
    return jsonify(view), 201


@app.get("/api/jobs/<job_id>/entries")
def job_entries(job_id):
    with lock:
        job = jobs.get(job_id)
        if job is None or job["status"] != "choosing":
            return error("That job is not waiting for a selection.", 404)
        return jsonify({
            "title": job["playlist_title"],
            "truncated": job["truncated"],
            "limit": MAX_PLAYLIST_ITEMS,
            "entries": [
                {"n": n, "title": e["title"], "duration": e["duration"]}
                for n, e in enumerate(job["entries"], start=1)
            ],
        })


@app.post("/api/jobs/<job_id>/select")
def select_entries(job_id):
    data = request.get_json(silent=True)
    indexes = data.get("indexes") if isinstance(data, dict) else None
    if not isinstance(indexes, list) or not indexes:
        return error("Select at least one video.", 400)

    with lock:
        job = jobs.get(job_id)
        if job is None:
            return error("No such job.", 404)
        if job["status"] != "choosing":
            return error("This playlist is no longer waiting for a selection.", 409)

        total = len(job["entries"])
        chosen = set()
        for i in indexes:
            if isinstance(i, bool) or not isinstance(i, int) or not 1 <= i <= total:
                return error("Invalid selection.", 400)
            chosen.add(i)

        quality = job["quality"]
        del jobs[job_id]  # the playlist entry is replaced by one job per chosen video
        children = [
            make_job(
                job["entries"][i - 1]["url"],
                quality,
                "queued",
                job["entries"][i - 1]["title"],
                download_subtitles=job.get("download_subtitles", False),
                custom_title=job.get("custom_title"),
                download_range=job.get("download_range"),
            )
            for i in sorted(chosen)
        ]
        ids = [c["id"] for c in children]
    for child_id in ids:
        work_queue.put(child_id)
    with scheduler_condition:
        scheduler_condition.notify_all()
    return jsonify({"queued": len(ids)})


@app.delete("/api/jobs/<job_id>")
def delete_job(job_id):
    with lock:
        job = jobs.get(job_id)
        if job is None:
            return error("No such job.", 404)
        if job["status"] in ("downloading", "paused"):
            job["cancel"] = True  # picked up by the progress hook
            job["resume_requested"] = False
            job["resume_event"].set()
        elif job["status"] == "processing":
            return error("Can't cancel while converting. Remove it once it finishes.", 409)
        else:
            # queued / inspecting / choosing / finished: just drop it. The worker and the
            # inspector both skip ids they can't find.
            del jobs[job_id]
    return jsonify({"ok": True})


@app.post("/api/jobs/<job_id>/pause")
def toggle_pause_job(job_id):
    with scheduler_condition:
        job = jobs.get(job_id)
        if job is None:
            return error("No such job.", 404)
        if job["status"] == "downloading":
            job["resume_event"].clear()
            job["status"] = "paused"
            job["resume_requested"] = False
            job["speed"] = None
            job["eta"] = None
        elif job["status"] == "paused":
            job["resume_requested"] = True
        else:
            return error("This job cannot be paused right now.", 409)
        scheduler_condition.notify_all()
        return jsonify({"status": job["status"]})


@app.post("/api/jobs/<job_id>/retry")
def retry_job(job_id):
    with lock:
        job = jobs.get(job_id)
        if job is None:
            return error("No such job.", 404)
        if job["status"] != "error":
            return error("Only failed jobs can be retried.", 409)

        job.update({
            "status": "inspecting",
            "title": None,
            "percent": 0.0,
            "size_done": 0,
            "size_total": None,
            "speed": None,
            "eta": None,
            "error": None,
            "file": None,
            "cancel": False,
            "entries": None,
            "playlist_title": None,
            "truncated": False,
        })
        job["resume_event"].set()

    start_inspect(job_id)
    return jsonify({"status": "inspecting"})


@app.post("/api/jobs/<job_id>/skip-subtitles")
def skip_subtitles_job(job_id):
    with lock:
        job = jobs.get(job_id)
        if job is None:
            return error("No such job.", 404)
        if job["status"] != "error":
            return error("Only failed jobs can be retried without subtitles.", 409)
        if not job.get("download_subtitles"):
            return error("Subtitles are already disabled for this job.", 409)

        job["download_subtitles"] = False
        job.update({
            "status": "inspecting",
            "title": None,
            "percent": 0.0,
            "size_done": 0,
            "size_total": None,
            "speed": None,
            "eta": None,
            "error": None,
            "file": None,
            "cancel": False,
            "entries": None,
            "playlist_title": None,
            "truncated": False,
        })
        job["resume_event"].set()

    start_inspect(job_id)
    return jsonify({"status": "inspecting", "download_subtitles": False})


@app.post("/api/jobs/clear")
def clear_jobs():
    with lock:
        for job_id in [i for i, j in jobs.items() if j["status"] in FINISHED]:
            del jobs[job_id]
    return jsonify({"ok": True})


@app.get("/api/jobs/<job_id>/file")
def job_file(job_id):
    with lock:
        job = jobs.get(job_id)
        path = job["file"] if job else None
        output_root = job["output_root"] if job else None
    if not path or not os.path.isfile(path):
        abort(404)
    root = os.path.realpath(output_root)
    real = os.path.realpath(path)
    try:
        inside = os.path.commonpath([root, real]) == root
    except ValueError:  # different drives on Windows
        inside = False
    if not inside:
        abort(403)
    return send_file(real, as_attachment=True)


@app.get("/api/jobs/<job_id>/open")
def open_job_file(job_id):
    with lock:
        job = jobs.get(job_id)
        path = job["file"] if job else None
        output_root = job["output_root"] if job else None
    if not path or not os.path.isfile(path):
        abort(404)
    root = os.path.realpath(output_root)
    real = os.path.realpath(path)
    try:
        inside = os.path.commonpath([root, real]) == root
    except ValueError:
        inside = False
    if not inside:
        abort(403)
    return send_file(real, as_attachment=False)


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    if not shutil.which("ffmpeg"):
        print("WARNING: FFmpeg was not found on PATH. MP4 merging and MP3 conversion will fail.")
    missing = [f for f in REQUIRED_FILES if not os.path.isfile(os.path.join(BASE_DIR, f))]
    if missing:
        sys.exit(
            "Missing files next to SMdownloader.py:\n  " + "\n  ".join(missing)
            + "\nKeep the templates/ and static/ folders beside this file."
        )

    start_worker()
    print(f"{APP_NAME} -> open http://{HOST}:{PORT}")
    print(f"Downloads -> {OUTPUT_ROOT}")
    app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)
