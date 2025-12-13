from __future__ import annotations

import os
import time
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
from flask import Flask, render_template, request, abort, redirect, url_for

app = Flask(__name__)

CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
STREAMS_URL = "https://iptv-org.github.io/api/streams.json"

# Render: filesystem is ephemeral. Use /tmp for cache.
CACHE_DIR = os.environ.get("CACHE_DIR", "/tmp/iptv_cache")
CACHE_FILE = os.path.join(CACHE_DIR, "iptv_cache.json")
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "1800"))  # 30 min

DEFAULT_COUNTRY = os.environ.get("DEFAULT_COUNTRY", "MX")


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


def ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def is_cache_fresh(path: str, ttl: int) -> bool:
    if not os.path.exists(path):
        return False
    return (time.time() - os.path.getmtime(path)) < ttl


def fetch_json(url: str, timeout: int = 25):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def refresh_cache() -> None:
    ensure_cache_dir()
    payload = {
        "channels": fetch_json(CHANNELS_URL),
        "streams": fetch_json(STREAMS_URL),
        "fetched_at": int(time.time()),
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def load_data() -> Tuple[Dict[str, Channel], Dict[str, List[Stream]]]:
    ensure_cache_dir()
    if not is_cache_fresh(CACHE_FILE, CACHE_TTL_SECONDS):
        refresh_cache()

    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)

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
        ch = s.get("channel")
        url = s.get("url")
        if not ch or not url:
            continue
        if ch not in channels_by_id:
            continue

        st = Stream(
            channel=ch,
            url=url,
            title=s.get("title"),
            quality=s.get("quality"),
            referrer=s.get("referrer"),
            user_agent=s.get("user_agent"),
        )
        streams_by_channel.setdefault(ch, []).append(st)

    return channels_by_id, streams_by_channel


def browser_playable(stream: Stream) -> bool:
    # Browsers cannot set Referer/User-Agent reliably via JS for HLS segment requests.
    # Also avoid obvious non-http(s) URLs.
    if not stream.url.startswith(("http://", "https://")):
        return False
    if stream.referrer or stream.user_agent:
        return False
    return True


@app.get("/")
def index():
    channels_by_id, streams_by_channel = load_data()

    q = (request.args.get("q") or "").strip().lower()
    country = (request.args.get("country") or DEFAULT_COUNTRY).strip()
    category = (request.args.get("category") or "").strip()

    countries = sorted({c.country for c in channels_by_id.values() if c.country})
    categories = sorted({cat for c in channels_by_id.values() for cat in c.categories})

    items = []
    for ch_id, ch in channels_by_id.items():
        streams = streams_by_channel.get(ch_id, [])
        streams = [s for s in streams if browser_playable(s)]
        if not streams:
            continue

        if country and ch.country != country:
            continue
        if category and category not in ch.categories:
            continue
        if q and (q not in ch.name.lower() and q not in ch.id.lower()):
            continue

        items.append({"channel": ch, "streams": streams})

    items.sort(key=lambda x: x["channel"].name.lower())

    return render_template(
        "index.html",
        items=items,
        q=q,
        country=country,
        category=category,
        countries=countries,
        categories=categories,
        results_count=len(items),
    )


@app.get("/channel/<channel_id>")
def channel_detail(channel_id: str):
    channels_by_id, streams_by_channel = load_data()
    ch = channels_by_id.get(channel_id)
    if not ch:
        abort(404)

    streams = [s for s in streams_by_channel.get(channel_id, []) if browser_playable(s)]
    return render_template("channel.html", ch=ch, streams=streams)


@app.get("/play/<channel_id>/<int:stream_index>")
def play(channel_id: str, stream_index: int):
    _, streams_by_channel = load_data()
    streams = [s for s in streams_by_channel.get(channel_id, []) if browser_playable(s)]
    if not streams or stream_index < 0 or stream_index >= len(streams):
        abort(404)

    stream = streams[stream_index]
    return render_template("player.html", channel_id=channel_id, stream=stream)


@app.post("/refresh")
def force_refresh():
    refresh_cache()
    return redirect(url_for("index"))


if __name__ == "__main__":
    # Render provides PORT env var
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
