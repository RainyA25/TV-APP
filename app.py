import os
import re
import time
import uuid
import threading
from pathlib import Path
from flask import Flask, render_template, request, send_from_directory, url_for
import yt_dlp

app = Flask(__name__)

# --- Config (Render-friendly) ---
# Store downloads in /tmp by default (ephemeral on Render; won't persist across deploys/restarts)
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/tmp/yt_downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Auto-cleanup: delete files older than N seconds (default 2 hours)
MAX_FILE_AGE_SECONDS = int(os.getenv("MAX_FILE_AGE_SECONDS", str(2 * 60 * 60)))

# Cleanup loop interval (default every 10 minutes)
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", str(10 * 60)))


def safe_prefix(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", s)
    return s[:40]


def cleanup_old_files():
    """Delete files older than MAX_FILE_AGE_SECONDS in DOWNLOAD_DIR."""
    now = time.time()
    deleted = 0
    for p in DOWNLOAD_DIR.glob("*"):
        try:
            if p.is_file():
                age = now - p.stat().st_mtime
                if age > MAX_FILE_AGE_SECONDS:
                    p.unlink(missing_ok=True)
                    deleted += 1
        except Exception:
            # Best-effort cleanup; ignore failures
            pass
    return deleted


def cleanup_worker():
    """Background cleanup loop."""
    while True:
        try:
            cleanup_old_files()
        except Exception:
            pass
        time.sleep(CLEANUP_INTERVAL_SECONDS)


# Start background cleanup once when the app boots
threading.Thread(target=cleanup_worker, daemon=True).start()


@app.get("/")
def index():
    return render_template("index.html", message=None, ok=True)


@app.post("/download")
def download():
    # Also run a quick cleanup on each download request (keeps disk tidy even if worker sleeps)
    cleanup_old_files()

    url = (request.form.get("url") or "").strip()
    mode = (request.form.get("mode") or "best").strip()
    prefix = safe_prefix(request.form.get("prefix") or "")

    if not url:
        return render_template("index.html", message="Missing URL.", ok=False)

    job_id = uuid.uuid4().hex[:10]
    outtmpl = str(DOWNLOAD_DIR / f"{prefix}{job_id}_%(title).200s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
    }

    if mode == "audio":
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        })
    else:
        ydl_opts.update({
            "format": "bv*+ba/best",
            "merge_output_format": "mp4",
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        downloaded_path = None
        req = info.get("requested_downloads")
        if isinstance(req, list) and req:
            downloaded_path = req[0].get("filepath") or req[0].get("_filename")

        if not downloaded_path:
            downloaded_path = info.get("_filename")

        if not downloaded_path:
            candidates = sorted(DOWNLOAD_DIR.glob(f"{prefix}{job_id}_*"))
            if candidates:
                downloaded_path = str(candidates[-1])

        if not downloaded_path or not os.path.exists(downloaded_path):
            return render_template(
                "index.html",
                message="Download finished but file was not found. Ensure FFmpeg is installed for merging/extracting.",
                ok=False,
            )

        filename = os.path.basename(downloaded_path)
        link = url_for("get_file", filename=filename)
        msg = f"Done. <a href='{link}'>Click here to download</a>.<br><small>Files auto-delete after ~{MAX_FILE_AGE_SECONDS//60} minutes.</small>"
        return render_template("index.html", message=msg, ok=True)

    except yt_dlp.utils.DownloadError as e:
        return render_template("index.html", message=f"Download error: {e}", ok=False)
    except Exception as e:
        return render_template("index.html", message=f"Unexpected error: {e}", ok=False)


@app.get("/files/<path:filename>")
def get_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)
