from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, abort, redirect, render_template, request, url_for

app = Flask(__name__)

CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
STREAMS_URL = "https://iptv-org.github.io/api/streams.json"

CACHE_DIR = "cache"
CACHE_FILE = os.path.join(CACHE_DIR, "iptv_cache.json")
CACHE_TTL_SECONDS = 60 * 30  # 30 minutes
DEFAULT_COUNTRY = os.environ.get("DEFAULT_COUNTRY", "MX")


# -----------------------------
# Data models
# -----------------------------
@dataclass
class Channel:
    id: str
    name: str
    country: Optional[str]
    categories: List[str]


@dataclass
class Stream:
    channel: str
    url: str
    title: Optional[str] = None
    quality: Optional[str] = None
    referrer: Optional[str] = None
    user_agent: Optional[str] = None


# -----------------------------
# Cache + fetch
# -----------------------------
def _ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def _is_cache_fresh(path: str, ttl: int) -> bool:
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < ttl


def fetch_json(url: str, timeout: int = 20) -> Any:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def load_data() -> Tuple[Dict[str, Channel], Dict[str, List[Stream]]]:
    """
    Returns:
      channels_by_id: {channel_id: Channel}
      streams_by_channel: {channel_id: [Stream, ...]}
    """
    _ensure_cache_dir()

    payload: Optional[Dict[str, Any]] = None

    # Use cached data if it's still fresh
    if _is_cache_fresh(CACHE_FILE, CACHE_TTL_SECONDS):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        try:
            channels_raw = fetch_json(CHANNELS_URL)
            streams_raw = fetch_json(STREAMS_URL)
            payload = {"channels": channels_raw, "streams": streams_raw}
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception:
            # If the network fetch fails, try to fall back to any existing cache
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            else:
                raise

    if payload is None:
        raise RuntimeError("IPTV data could not be loaded from API or cache.")

    channels_by_id: Dict[str, Channel] = {}
    for c in payload["channels"]:
        channels_by_id[c["id"]] = Channel(
            id=c["id"],
            name=c.get("name") or c["id"],
            country=c.get("country"),
            categories=c.get("categories") or [],
        )

    streams_by_channel: Dict[str, List[Stream]] = {}
    for s in payload["streams"]:
        channel_id = s.get("channel")
        url = s.get("url")
        if not channel_id or not url:
            continue
        stream = Stream(
            channel=channel_id,
            url=url,
            title=s.get("title"),
            quality=s.get("quality"),
            referrer=s.get("referrer"),
            user_agent=s.get("user_agent"),
        )
        streams_by_channel.setdefault(channel_id, []).append(stream)

    # Keep only channels that exist in channels list (optional but cleaner)
    streams_by_channel = {k: v for k, v in streams_by_channel.items() if k in channels_by_id}

    return channels_by_id, streams_by_channel


def refresh_cache() -> None:
    _ensure_cache_dir()
    channels_raw = fetch_json(CHANNELS_URL)
    streams_raw = fetch_json(STREAMS_URL)
    payload = {"channels": channels_raw, "streams": streams_raw}
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def index():
    error_message: Optional[str] = None
    try:
        channels_by_id, streams_by_channel = load_data()
    except Exception as exc:  # pylint: disable=broad-except
        error_message = f"Unable to load channels right now: {exc}"
        channels_by_id, streams_by_channel = {}, {}

    # Query params
    q = (request.args.get("q") or "").strip().lower()
    country = (request.args.get("country") or DEFAULT_COUNTRY).strip()
    category = (request.args.get("category") or "").strip()

    # Build filter vocab
    all_countries = sorted({c.country for c in channels_by_id.values() if c.country})
    all_categories = sorted({cat for c in channels_by_id.values() for cat in c.categories})

    # Only channels that have streams
    items = []
    for ch_id, ch in channels_by_id.items():
        if ch_id not in streams_by_channel:
            continue

        if country and (ch.country != country):
            continue

        if category and (category not in ch.categories):
            continue

        if q and (q not in ch.name.lower() and q not in ch.id.lower()):
            continue

        items.append({
            "channel": ch,
            "streams": streams_by_channel[ch_id],
        })

    # Sort by name
    items.sort(key=lambda x: x["channel"].name.lower())

    return render_template(
        "index.html",
        items=items,
        q=q,
        country=country,
        category=category,
        countries=all_countries,
        categories=all_categories,
        results_count=len(items),
        error_message=error_message,
    )


@app.get("/channel/<channel_id>")
def channel_detail(channel_id: str):
    try:
        channels_by_id, streams_by_channel = load_data()
    except Exception as exc:  # pylint: disable=broad-except
        abort(503, description=f"Channel list unavailable: {exc}")
    ch = channels_by_id.get(channel_id)
    if not ch:
        abort(404)

    streams = streams_by_channel.get(channel_id, [])
    return render_template("channel.html", ch=ch, streams=streams)


@app.get("/watch/<channel_id>/<int:stream_index>")
def watch(channel_id: str, stream_index: int):
    try:
        channels_by_id, streams_by_channel = load_data()
    except Exception as exc:  # pylint: disable=broad-except
        abort(503, description=f"Channel list unavailable: {exc}")

    ch = channels_by_id.get(channel_id)
    if not ch:
        abort(404)

    streams = streams_by_channel.get(channel_id, [])
    if stream_index < 0 or stream_index >= len(streams):
        abort(404)

    stream = streams[stream_index]
    return render_template(
        "watch.html",
        ch=ch,
        stream=stream,
        stream_index=stream_index,
        total_streams=len(streams),
    )


@app.post("/refresh")
def force_refresh():
    try:
        refresh_cache()
    except Exception as exc:  # pylint: disable=broad-except
        abort(503, description=f"Unable to refresh cache: {exc}")
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
