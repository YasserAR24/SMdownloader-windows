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
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
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
    + [
        (q, f"{q}p (4K)" if q == "2160" else f"{q}p (2K)" if q == "1440" else f"{q}p")
        for q in QUALITIES
    ]
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
MAX_SUBTITLE_PREFLIGHT_BYTES = 16 * 1024 * 1024
SUBTITLE_LANGUAGES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ja": "Japanese",
    "ko": "Korean",
    "zh-Hans": "Chinese (Simplified)",
    "ar": "Arabic",
    "hi": "Hindi",
    "ru": "Russian",
}
SUBTITLE_PREFLIGHT_LANGUAGE_CHOICES = {
    "en": "English",
    "ar": "Arabic",
    "fr": "French",
}
DEFAULT_SUBTITLE_PREFLIGHT_LANGUAGES = ["en"]
WINDOWS_RESERVED_FILENAMES = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


def default_config_dir():
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(appdata, "smdownloader")
    return os.path.join(os.path.expanduser("~"), ".config", "smdownloader")


def default_download_root():
    env_root = os.environ.get("SM_DOWNLOAD_DIR")
    if env_root and env_root.strip():
        return os.path.abspath(os.path.expanduser(env_root.strip()))
    return os.path.join(os.path.expanduser("~"), "Videos", "SMdownloads")


DEFAULT_OUTPUT_ROOT = default_download_root()
SETTINGS_FILE = os.path.join(default_config_dir(), "settings.json")


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


def load_subtitle_preflight_languages():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as settings_file:
            value = json.load(settings_file).get("subtitle_preflight_languages")
        if isinstance(value, list):
            languages = list(dict.fromkeys(
                language for language in value
                if isinstance(language, str) and language in SUBTITLE_PREFLIGHT_LANGUAGE_CHOICES
            ))
            if languages:
                return languages
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_SUBTITLE_PREFLIGHT_LANGUAGES.copy()


OUTPUT_ROOT = load_output_root()
MAX_CONCURRENT_DOWNLOADS = load_max_concurrent_downloads()
SUBTITLE_PREFLIGHT_LANGUAGES = load_subtitle_preflight_languages()
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
reserved_output_paths = set()
lock = threading.Lock()
scheduler_condition = threading.Condition(lock)
work_queue = queue.Queue()
inspect_slots = threading.BoundedSemaphore(4)
FINISHED = ("done", "error", "cancelled")
YTDLP_UPDATE_LOCK = threading.Lock()
ytdlp_update_in_progress = False


def public(job):
    """Fields the browser is allowed to see (never the server-side file path)."""
    keys = ("id", "url", "mode", "quality", "platform", "status",
            "title", "percent", "size_done", "size_total", "speed", "eta", "error",
            "resume_requested", "download_subtitles", "custom_title", "download_range")
    data = {k: job[k] for k in keys}
    data["subtitle_language"] = job["subtitle_language"]
    data["has_file"] = bool(job["file"])
    data["count"] = len(job["entries"]) if job["entries"] else None
    return data


def clean_error(exc, limit=500):
    return ANSI_RE.sub("", str(exc)).strip()[:limit]


def safe_custom_title(title):
    if not title:
        return None

    title = str(title).strip()
    title = "".join(ch if ch not in '<>:"/\\|?*' else "-" for ch in title)
    title = yt_dlp.utils.sanitize_filename(title, restricted=False).strip().rstrip(".")
    title = title.encode("utf-8")[:120].decode("utf-8", "ignore").strip(" .")
    if not title:
        return None
    if os.name == "nt":
        stem = os.path.splitext(title)[0].upper()
        if stem in WINDOWS_RESERVED_FILENAMES:
            title = f"_{title}"
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


def make_job(
    url,
    quality,
    status,
    title=None,
    download_subtitles=False,
    subtitle_language="en",
    custom_title=None,
    download_range=None,
    output_subfolder=None,
):
    """Create and register a job. The caller must hold `lock`."""
    mode = "audio" if quality == "audio" else "video"
    platform = platform_check(url)
    output_dir = os.path.join(OUTPUT_ROOT, platform.lower()) if platform else OUTPUT_ROOT
    safe_subfolder = safe_custom_title(output_subfolder)
    if safe_subfolder:
        output_dir = os.path.join(output_dir, safe_subfolder)
    job = {
        "id": uuid.uuid4().hex[:12],
        "url": url,
        "mode": mode,
        "quality": quality,
        "platform": platform,
        "download_subtitles": bool(
            download_subtitles and mode == "video" and platform == "YouTube"
        ),
        "subtitle_language": subtitle_language,
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
        "subtitle_files": set(),
        "reserved_output_paths": set(),
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
    if (
        host == "linkedin.com"
        or host.endswith(".linkedin.com")
        or host == "lnkd.in"
        or host.endswith(".lnkd.in")
    ):
        return "LinkedIn"

    return None


def should_download_subtitles(job):
    return (
        job.get("download_subtitles")
        and job.get("mode") == "video"
        and job.get("platform") == "YouTube"
    )


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
        if job["mode"] == "video":
            ydl_opts["force_keyframes_at_cuts"] = True

    subtitle_language = job["subtitle_language"]
    subtitle_pattern = f"{re.escape(subtitle_language)}.*"
    if should_download_subtitles(job):
        ydl_opts.update({
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [subtitle_pattern],
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
        if job["platform"] != "YouTube" or q == "high":
            ydl_opts["format"] = "bv*+ba/b"
        else:
            ydl_opts["format"] = f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"

        if should_download_subtitles(job):
            ydl_opts["writesubtitles"] = True
            ydl_opts["writeautomaticsub"] = True
            ydl_opts["subtitleslangs"] = [subtitle_pattern]
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
            job["custom_title"] = None
            job["download_range"] = None
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


def has_requested_subtitles(info, language):
    tracks = (info.get("subtitles") or {}, info.get("automatic_captions") or {})
    pattern = re.compile(f"^{re.escape(language)}.*", re.IGNORECASE)
    return any(
        pattern.match(track_language.replace("_", "-"))
        for collection in tracks
        for track_language in collection
        if isinstance(track_language, str)
    )


def can_download_subtitle(url, language_code):
    with tempfile.TemporaryDirectory(prefix="smdownloader-subtitle-check-") as temp_dir:
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [f"{re.escape(language_code)}.*"],
            "subtitlesformat": "srt/best",
            "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
            "socket_timeout": 5,
            "retries": 1,
            "fragment_retries": 1,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True) or {}

        for requested_language, downloaded in (info.get("requested_subtitles") or {}).items():
            if not isinstance(requested_language, str) or not (
                requested_language.lower() == language_code.lower()
                or requested_language.lower().startswith(language_code.lower() + "-")
                or requested_language.lower().startswith(language_code.lower() + "_")
            ):
                continue
            if not isinstance(downloaded, dict):
                continue
            path = downloaded.get("filepath")
            if not isinstance(path, str) or not os.path.isfile(path):
                continue
            try:
                if os.path.getsize(path) > MAX_SUBTITLE_PREFLIGHT_BYTES:
                    continue
                with open(path, "rb") as subtitle_file:
                    content = subtitle_file.read(MAX_SUBTITLE_PREFLIGHT_BYTES + 1)
            except OSError:
                continue
            if subtitle_content_is_valid(downloaded.get("ext"), content):
                return True
        return False


def subtitle_content_is_valid(extension, content):
    if not content:
        return False
    text = content.decode("utf-8-sig", "replace").strip()
    if extension == "json3":
        try:
            data = json.loads(text)
        except ValueError:
            return False
        return isinstance(data, dict) and any(
            isinstance(event, dict)
            and any(
                isinstance(segment, dict) and bool(segment.get("utf8", "").strip())
                for segment in event.get("segs", [])
            )
            for event in data.get("events", [])
        )
    if extension in ("srv1", "srv2", "srv3", "ttml", "dfxp"):
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return False
        root_name = root.tag.rsplit("}", 1)[-1].lower()
        if root_name not in ("timedtext", "transcript", "tt"):
            return False
        return any(
            node.tag.rsplit("}", 1)[-1].lower() in ("text", "p")
            and any(value.strip() for value in node.itertext())
            and any(key.rsplit("}", 1)[-1].lower() in ("start", "begin", "dur", "end") for key in node.attrib)
            for node in root.iter()
        )
    if extension == "vtt":
        return text.startswith("WEBVTT") and "-->" in text
    if extension == "srt":
        return "-->" in text and bool(re.search(r"\d{2}:\d{2}:\d{2}[,.]\d{3}", text))
    if extension in ("ass", "ssa"):
        return "[Events]" in text and "Dialogue:" in text
    return False


def reserve_output_path(job, info):
    filename = info.get("_filename")
    if not isinstance(filename, str):
        return

    extension = ".mp3" if job["mode"] == "audio" else ".mp4"
    output_path = os.path.realpath(os.path.splitext(filename)[0] + extension)
    with lock:
        if output_path in job["reserved_output_paths"]:
            return
        if os.path.lexists(output_path) or output_path in reserved_output_paths:
            raise yt_dlp.utils.DownloadError(
                f'A file named "{os.path.basename(output_path)}" already exists in the download folder.'
            )
        reserved_output_paths.add(output_path)
        job["reserved_output_paths"].add(output_path)


def rename_output_file(job, source, title):
    source = os.path.realpath(source)
    destination = os.path.join(
        os.path.dirname(source),
        title + os.path.splitext(source)[1],
    )
    if destination == source:
        return source
    if (
        os.path.lexists(destination)
        or (
            destination in reserved_output_paths
            and destination not in job["reserved_output_paths"]
        )
    ):
        raise yt_dlp.utils.DownloadError(
            f'A file named "{os.path.basename(destination)}" already exists in the download folder.'
        )

    os.replace(source, destination)
    return destination


def remove_partials(paths):
    """Delete leftovers of an aborted download (.part, .ytdl, fragment files)."""
    for path in paths:
        for leftover in glob.glob(glob.escape(path) + "*"):
            try:
                os.remove(leftover)
            except OSError:
                pass


def record_subtitle_files(job, info):
    if not isinstance(info, dict):
        return
    requested = info.get("requested_subtitles") or {}
    if not isinstance(requested, dict):
        return
    root = os.path.realpath(job["output_root"])
    for subtitle in requested.values():
        path = subtitle.get("filepath") if isinstance(subtitle, dict) else None
        if not isinstance(path, str):
            continue
        if not os.path.isabs(path):
            path = os.path.join(job["output_dir"], path)
        real_path = os.path.realpath(path)
        try:
            if os.path.commonpath([root, real_path]) == root:
                job["subtitle_files"].add(real_path)
        except ValueError:
            continue


def remove_job_subtitles(job):
    for path in job.get("subtitle_files", set()):
        for leftover in (path, path + ".part", path + ".ytdl"):
            try:
                os.remove(leftover)
            except OSError:
                pass
        for leftover in glob.glob(glob.escape(path) + ".part-Frag*"):
            try:
                os.remove(leftover)
            except OSError:
                pass
    job["subtitle_files"].clear()


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
        if (
            should_download_subtitles(self.tracker.job)
            and self.tracker.job["mode"] == "video"
            and not has_requested_subtitles(
                info,
                self.tracker.job["subtitle_language"],
            )
        ):
            raise yt_dlp.utils.DownloadError(
                f"This video does not have {SUBTITLE_LANGUAGES[self.tracker.job['subtitle_language']]} subtitles."
            )
        reserve_output_path(self.tracker.job, info)
        self.tracker.plan(info)
        return [], info


def run_job(job):
    partials = set()
    tracker = ProgressTracker(job)

    def on_progress(d):
        if d.get("tmpfilename"):
            partials.add(d["tmpfilename"])
        info = d.get("info_dict") or {}
        record_subtitle_files(job, info)
        while not job["resume_event"].wait(timeout=0.25):
            if job["cancel"]:
                raise yt_dlp.utils.DownloadCancelled("Cancelled")
        if job["cancel"]:
            raise yt_dlp.utils.DownloadCancelled("Cancelled")
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
        record_subtitle_files(job, d.get("info_dict"))
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
        record_subtitle_files(job, info)
        path = final_path(info)
        try:
            final_size = os.path.getsize(path) if path else None
        except OSError:
            final_size = None
        with lock:
            job["file"] = path
            job["title"] = job["title"] or (info or {}).get("title")
            if path and job.get("custom_title"):
                path = rename_output_file(job, path, job["custom_title"])
                job["file"] = path
            if final_size:  # the real size on disk (after merging / MP3 conversion)
                job["size_done"] = job["size_total"] = final_size
            job["percent"] = 100.0
            job["speed"] = None
            job["eta"] = None
            job["status"] = "done"
    except yt_dlp.utils.DownloadCancelled:
        remove_partials(partials)
        remove_job_subtitles(job)
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
        with lock:
            for reserved_path in job["reserved_output_paths"]:
                reserved_output_paths.discard(reserved_path)
            job["reserved_output_paths"].clear()


def run_scheduled_job(job):
    try:
        run_job(job)
    finally:
        with scheduler_condition:
            scheduler_condition.notify_all()


def worker():
    while True:
        with scheduler_condition:
            if ytdlp_update_in_progress:
                scheduler_condition.wait()
                continue

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
        subtitle_languages=SUBTITLE_LANGUAGES,
        subtitle_preflight_languages=SUBTITLE_PREFLIGHT_LANGUAGE_CHOICES,
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

    if not YTDLP_UPDATE_LOCK.acquire(blocking=False):
        return error("Wait for the yt-dlp update to finish, then try again.", 409)
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        return error(clean_error(exc), 400)
    finally:
        YTDLP_UPDATE_LOCK.release()

    if info.get("_type") == "playlist":
        return error("Select a single video URL to choose a time range.", 400)
    duration = info.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        return error("Could not read the video's duration.", 400)
    return jsonify({"duration": int(duration), "title": str(info.get("title") or "")[:300]})


@app.post("/api/subtitle-info")
def subtitle_info():
    data = request.get_json(silent=True)
    url = str(data.get("url") or "").strip() if isinstance(data, dict) else ""
    if not url.startswith(("http://", "https://")) or not urlparse(url).hostname:
        return error("Not a valid URL (must start with http:// or https://).", 400)
    if len(url) > MAX_URL_LENGTH:
        return error("URL is too long.", 400)
    if platform_check(url) != "YouTube":
        return jsonify({"languages": []})

    if isinstance(data, dict) and "languages" in data:
        requested_languages = data["languages"]
        if (
            not isinstance(requested_languages, list)
            or not requested_languages
            or any(
                not isinstance(language, str) or language not in SUBTITLE_LANGUAGES
                for language in requested_languages
            )
        ):
            return error("Choose at least one supported subtitle language.", 400)
        languages_to_check = list(dict.fromkeys(requested_languages))
    else:
        with lock:
            languages_to_check = list(SUBTITLE_PREFLIGHT_LANGUAGES)
    if not YTDLP_UPDATE_LOCK.acquire(blocking=False):
        return error("Wait for the yt-dlp update to finish, then try again.", 409)
    try:
        available = []
        for language in languages_to_check:
            try:
                if can_download_subtitle(url, language):
                    available.append({
                        "code": language,
                        "name": SUBTITLE_LANGUAGES[language],
                    })
            except Exception as exc:
                app.logger.info(
                    "%s subtitle preflight failed: %s",
                    SUBTITLE_LANGUAGES[language],
                    clean_error(exc),
                )
    finally:
        YTDLP_UPDATE_LOCK.release()

    return jsonify({"languages": available})


@app.post("/api/playlist-entries")
def playlist_subtitle_entries():
    data = request.get_json(silent=True)
    url = str(data.get("url") or "").strip() if isinstance(data, dict) else ""
    if not url.startswith(("http://", "https://")) or not urlparse(url).hostname:
        return error("Not a valid URL (must start with http:// or https://).", 400)
    if len(url) > MAX_URL_LENGTH:
        return error("URL is too long.", 400)
    if platform_check(url) != "YouTube":
        return jsonify({"is_playlist": False})

    if not YTDLP_UPDATE_LOCK.acquire(blocking=False):
        return error("Wait for the yt-dlp update to finish, then try again.", 409)
    try:
        options = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "extract_flat": "in_playlist",
            "playlistend": MAX_PLAYLIST_ITEMS + 1,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        return error(clean_error(exc), 400)
    finally:
        YTDLP_UPDATE_LOCK.release()

    if info.get("_type") != "playlist":
        return jsonify({"is_playlist": False})

    entries, truncated = collect_entries(info)
    return jsonify({
        "is_playlist": True,
        "title": str(info.get("title") or "")[:300] or "Playlist",
        "truncated": truncated,
        "limit": MAX_PLAYLIST_ITEMS,
        "entries": [
            {
                "n": index,
                "title": entry["title"],
                "url": entry["url"],
                "duration": entry["duration"],
            }
            for index, entry in enumerate(entries, start=1)
        ],
    })


@app.post("/api/playlist-subtitle-download")
def download_playlist_subtitle_selection():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Send a JSON body.", 400)

    videos = data.get("videos")
    if not isinstance(videos, list) or not videos:
        return error("Select at least one video.", 400)
    if len(videos) > MAX_PLAYLIST_ITEMS:
        return error(f"Select no more than {MAX_PLAYLIST_ITEMS} videos.", 400)

    quality = str(data.get("quality") or DEFAULT_QUALITY)
    if quality not in QUALITY_CHOICES:
        return error("Invalid quality. Use: " + ", ".join(QUALITY_CHOICES) + ".", 400)
    if quality == "audio":
        return error("Subtitles are unavailable for audio-only downloads.", 400)
    subtitle_language = data.get("subtitle_language", "en")
    if not isinstance(subtitle_language, str) or subtitle_language not in SUBTITLE_LANGUAGES:
        return error("Choose a supported subtitle language.", 400)
    folder_name = data.get("folder_name")
    if not isinstance(folder_name, str):
        return error("The playlist folder name must be text.", 400)
    folder_name = safe_custom_title(folder_name)
    if not folder_name:
        return error("Enter a valid playlist folder name.", 400)

    normalized_videos = []
    for video in videos:
        if not isinstance(video, dict):
            return error("Invalid video selection.", 400)
        url = video.get("url")
        title = video.get("title")
        if (
            not isinstance(url, str)
            or not url.startswith(("http://", "https://"))
            or not urlparse(url).hostname
            or len(url) > MAX_URL_LENGTH
            or platform_check(url) != "YouTube"
        ):
            return error("Invalid video selection.", 400)
        if not isinstance(title, str):
            return error("Invalid video title.", 400)
        download_subtitles = video.get("download_subtitles", True)
        if not isinstance(download_subtitles, bool):
            return error("The subtitles option must be true or false.", 400)
        normalized_videos.append((url, title[:300], download_subtitles))

    with lock:
        if ytdlp_update_in_progress:
            return error("Wait for the yt-dlp update to finish, then try again.", 409)
        children = [
            make_job(
                url,
                quality,
                "queued",
                title,
                download_subtitles=download_subtitles,
                subtitle_language=subtitle_language,
                output_subfolder=folder_name,
            )
            for url, title, download_subtitles in normalized_videos
        ]
    for child in children:
        work_queue.put(child["id"])
    with scheduler_condition:
        scheduler_condition.notify_all()
    return jsonify({"queued": len(children)})


@app.get("/api/settings")
def get_settings():
    with lock:
        return jsonify({
            "output_root": OUTPUT_ROOT,
            "max_concurrent_downloads": MAX_CONCURRENT_DOWNLOADS,
            "subtitle_preflight_languages": SUBTITLE_PREFLIGHT_LANGUAGES,
        })


@app.post("/api/settings")
def update_settings():
    global OUTPUT_ROOT, MAX_CONCURRENT_DOWNLOADS, SUBTITLE_PREFLIGHT_LANGUAGES
    data = request.get_json(silent=True)
    path = data.get("output_root") if isinstance(data, dict) else None
    if not isinstance(path, str) or not path.strip():
        return error("Enter a download folder path.", 400)
    max_concurrent = data.get("max_concurrent_downloads", MAX_CONCURRENT_DOWNLOADS)
    if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or max_concurrent < 1:
        return error("Maximum simultaneous downloads must be a positive whole number.", 400)
    subtitle_languages = data.get(
        "subtitle_preflight_languages",
        SUBTITLE_PREFLIGHT_LANGUAGES,
    )
    if (
        not isinstance(subtitle_languages, list)
        or not subtitle_languages
        or any(
            not isinstance(language, str) or language not in SUBTITLE_PREFLIGHT_LANGUAGE_CHOICES
            for language in subtitle_languages
        )
    ):
        return error("Select at least one supported subtitle language.", 400)
    subtitle_languages = list(dict.fromkeys(subtitle_languages))

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
                "subtitle_preflight_languages": subtitle_languages,
            }, settings_file)
        os.replace(temporary, SETTINGS_FILE)
    except OSError as exc:
        return error(f"Could not save this folder: {clean_error(exc)}", 400)

    with scheduler_condition:
        OUTPUT_ROOT = path
        MAX_CONCURRENT_DOWNLOADS = max_concurrent
        SUBTITLE_PREFLIGHT_LANGUAGES = subtitle_languages
        scheduler_condition.notify_all()
        return jsonify({
            "output_root": OUTPUT_ROOT,
            "max_concurrent_downloads": MAX_CONCURRENT_DOWNLOADS,
            "subtitle_preflight_languages": SUBTITLE_PREFLIGHT_LANGUAGES,
        })


def pick_directory(title, initial_dir):
    initial_dir = os.path.abspath(os.path.expanduser(initial_dir or "."))
    if sys.platform.startswith("win"):
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                chosen = filedialog.askdirectory(
                    title=title,
                    initialdir=initial_dir,
                    mustexist=True,
                )
            finally:
                root.destroy()
            return chosen or None
        except Exception:
            pass

    zenity = shutil.which("zenity")
    kdialog = shutil.which("kdialog")
    if zenity:
        command = [
            zenity,
            "--file-selection",
            "--directory",
            f"--title={title}",
            f"--filename={initial_dir.rstrip(os.sep)}{os.sep}",
        ]
    elif kdialog:
        command = [kdialog, "--getexistingdirectory", initial_dir, "--title", title]
    else:
        return None

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError:
        return None
    if result.returncode not in (0, 1):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


@app.post("/api/settings/browse")
def browse_settings():
    with lock:
        initial_dir = OUTPUT_ROOT

    destination = pick_directory("Choose download folder", initial_dir)
    if destination is None:
        return error("No system folder picker is available. You can still enter the path manually.", 503)
    return jsonify({"output_root": destination})


@app.post("/api/settings/update-ytdlp")
def update_ytdlp():
    global ytdlp_update_in_progress
    if not YTDLP_UPDATE_LOCK.acquire(blocking=False):
        return error("A yt-dlp update is already in progress.", 409)

    try:
        with scheduler_condition:
            active = any(
                job["status"] not in FINISHED
                for job in jobs.values()
            )
            if active:
                return error("Wait for all queued and active jobs to finish before updating yt-dlp.", 409)
            ytdlp_update_in_progress = True

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            return error("The yt-dlp update timed out. Check your connection and try again.", 504)
        except OSError as exc:
            return error(f"Could not run pip: {clean_error(exc)}", 500)

        if result.returncode:
            details = clean_error(result.stderr or result.stdout)
            message = "Could not update yt-dlp."
            if details:
                message += " " + details
            return error(message, 500)

        return jsonify({
            "message": "yt-dlp updated. Restart SMdownloader to load the updated version."
        })
    finally:
        with scheduler_condition:
            ytdlp_update_in_progress = False
            scheduler_condition.notify_all()
        YTDLP_UPDATE_LOCK.release()


@app.post("/api/jobs")
def create_job():
    with lock:
        if ytdlp_update_in_progress:
            return error("Wait for the yt-dlp update to finish, then try again.", 409)

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
    subtitle_language = data.get("subtitle_language", "en")
    if not isinstance(subtitle_language, str) or subtitle_language not in SUBTITLE_LANGUAGES:
        return error("Choose a supported subtitle language.", 400)
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
        download_range = (start_seconds, end_seconds)

    with lock:
        if ytdlp_update_in_progress:
            return error("Wait for the yt-dlp update to finish, then try again.", 409)
        job = make_job(
            url,
            quality,
            "inspecting",
            download_subtitles=download_subtitles,
            subtitle_language=subtitle_language,
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
    with lock:
        if ytdlp_update_in_progress:
            return error("Wait for the yt-dlp update to finish, then try again.", 409)

    data = request.get_json(silent=True)
    indexes = data.get("indexes") if isinstance(data, dict) else None
    if not isinstance(indexes, list) or not indexes:
        return error("Select at least one video.", 400)
    folder_name = data.get("folder_name") if isinstance(data, dict) else None
    if not isinstance(folder_name, str):
        return error("The playlist folder name must be text.", 400)
    folder_name = safe_custom_title(folder_name)
    if not folder_name:
        return error("Enter a valid playlist folder name.", 400)

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
                subtitle_language=job.get("subtitle_language", "en"),
                output_subfolder=folder_name,
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
            remove_job_subtitles(job)
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


@app.post("/api/jobs/pause-all")
def set_all_jobs_paused():
    data = request.get_json(silent=True)
    action = data.get("action") if isinstance(data, dict) else None
    if action not in ("pause", "resume"):
        return error("Choose whether to pause or resume downloads.", 400)

    updated = 0
    with scheduler_condition:
        for job in jobs.values():
            if action == "pause" and job["status"] == "downloading":
                job["resume_event"].clear()
                job["status"] = "paused"
                job["resume_requested"] = False
                job["speed"] = None
                job["eta"] = None
                updated += 1
            elif action == "resume" and job["status"] == "paused" and not job["resume_requested"]:
                job["resume_requested"] = True
                updated += 1
        scheduler_condition.notify_all()
    return jsonify({"action": action, "updated": updated})


@app.post("/api/jobs/<job_id>/name")
def edit_job_name(job_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return error("The video name must be text.", 400)

    name = data["name"].strip()
    if len(name) > MAX_CUSTOM_TITLE_LENGTH:
        return error(f"The video name must be {MAX_CUSTOM_TITLE_LENGTH} characters or fewer.", 400)
    custom_title = safe_custom_title(name)

    with lock:
        job = jobs.get(job_id)
        if job is None:
            return error("No such job.", 404)
        if job["status"] not in ("inspecting", "queued", "downloading", "paused", "error", "done"):
            return error("The name can't be changed while this job is converting.", 409)

        if job["status"] == "done":
            source = job["file"]
            if not source or not os.path.isfile(source):
                return error("The downloaded file is no longer available.", 409)

            title = custom_title or safe_custom_title(job.get("title"))
            if not title:
                return error("A video name is required.", 400)

            try:
                job["file"] = rename_output_file(job, source, title)
            except yt_dlp.utils.DownloadError as exc:
                return error(clean_error(exc), 409)
            except OSError as exc:
                return error(f"Could not rename the downloaded file: {clean_error(exc)}", 500)
        elif job["status"] in ("downloading", "paused") and custom_title:
            extension = ".mp3" if job["mode"] == "audio" else ".mp4"
            destination = os.path.realpath(
                os.path.join(job["output_dir"], custom_title + extension)
            )
            if (
                destination not in job["reserved_output_paths"]
                and (
                    os.path.lexists(destination)
                    or destination in reserved_output_paths
                )
            ):
                return error(
                    f'A file named "{os.path.basename(destination)}" already exists in the download folder.',
                    409,
                )
            reserved_output_paths.add(destination)
            job["reserved_output_paths"].add(destination)

        job["custom_title"] = custom_title
        return jsonify(public(job))


@app.post("/api/jobs/<job_id>/retry")
def retry_job(job_id):
    with lock:
        if ytdlp_update_in_progress:
            return error("Wait for the yt-dlp update to finish, then try again.", 409)
        job = jobs.get(job_id)
        if job is None:
            return error("No such job.", 404)
        if job["status"] != "error":
            return error("Only failed jobs can be retried.", 409)

        prepare_job_retry(job)

    start_inspect(job_id)
    return jsonify({"status": "inspecting"})


def prepare_job_retry(job):
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


@app.post("/api/jobs/retry-all")
def retry_all_jobs():
    with lock:
        if ytdlp_update_in_progress:
            return error("Wait for the yt-dlp update to finish, then try again.", 409)
        job_ids = []
        for job in jobs.values():
            if job["status"] == "error":
                prepare_job_retry(job)
                job_ids.append(job["id"])

    for job_id in job_ids:
        start_inspect(job_id)
    return jsonify({"retried": len(job_ids)})


@app.post("/api/jobs/<job_id>/skip-subtitles")
def skip_subtitles_job(job_id):
    with lock:
        if ytdlp_update_in_progress:
            return error("Wait for the yt-dlp update to finish, then try again.", 409)
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
            remove_job_subtitles(jobs[job_id])
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


@app.post("/api/jobs/<job_id>/open")
def open_job_folder(job_id):
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

    folder = os.path.dirname(real)
    if sys.platform.startswith("win"):
        command = ["explorer", folder]
    else:
        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:
            return error("No system folder opener is available.", 503)
        command = [opener, "open", folder] if os.path.basename(opener) == "gio" else [opener, folder]

    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return error(f"Could not open the download folder: {clean_error(exc)}", 500)
    return jsonify({"opened": True})


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
