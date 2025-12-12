from __future__ import annotations

import json
import os
import time
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, request, redirect, url_for, render_template_string, abort

app = Flask(__name__)

CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
STREAMS_URL = "https://iptv-org.github.io/api/streams.json"

CACHE_DIR = "cache"
CACHE_FILE = os.path.join(CACHE_DIR, "iptv_cache.json")
CACHE_TTL_SECONDS = 60 * 30  # 30 minutes


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
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

def load_data() -> Tuple[Dict[str, Channel], Dict[str, List[Stream]]]:
    """
    Returns:
      channels_by_id: {channel_id: Channel}
      streams_by_channel: {channel_id: [Stream, ...]}
    """
    _ensure_cache_dir()

    if _is_cache_fresh(CACHE_FILE, CACHE_TTL_SECONDS):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        channels_raw = fetch_json(CHANNELS_URL)
        streams_raw = fetch_json(STREAMS_URL)
        payload = {"channels": channels_raw, "streams": streams_raw}
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

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
        st = Stream(
            channel=ch,
            url=url,
            title=s.get("title"),
            quality=s.get("quality"),
            referrer=s.get("referrer"),
            user_agent=s.get("user_agent"),
        )
        streams_by_channel.setdefault(ch, []).append(st)

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
# Player launch
# -----------------------------
def launch_mpv(stream):
    VLC_PATH = r"C:\Program Files\VideoLAN\VLC\vlc.exe"
    subprocess.Popen([VLC_PATH, stream.url])


# -----------------------------
# UI
# -----------------------------
TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Local IPTV Browser</title>
  <style>
    body { font-family: system-ui, Arial; margin: 24px; }
    .top { display:flex; gap:12px; align-items:center; margin-bottom: 16px; flex-wrap: wrap; }
    input, select { padding:10px 12px; border-radius:12px; border:1px solid #ccc; }
    .btn { padding:10px 12px; border:1px solid #222; border-radius:12px; background:#fff; cursor:pointer; }
    .btn:hover { opacity:0.85; }
    .row { display:flex; gap:12px; align-items:center; padding:12px; border:1px solid #ddd; border-radius:14px; margin-bottom:10px; }
    .grow { flex: 1; min-width: 280px; }
    .name { font-weight:700; }
    .meta { color:#555; font-size: 12px; }
    .pill { display:inline-block; padding:2px 8px; border:1px solid #ddd; border-radius:999px; margin-right:6px; font-size:12px; color:#444; }
    .links { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
    .small { font-size: 12px; color: #666; }
    .danger { border-color: #a00; color: #a00; }
  </style>
</head>
<body>
  <h2>Local IPTV Browser</h2>
  <div class="small">
    Data source: iptv-org/api (cached locally). Player: mpv.
  </div>

  <div class="top">
    <form method="get" class="top" style="flex:1; min-width: 320px;">
      <input name="q" placeholder="Search channel name…" value="{{ q }}" style="flex:1; min-width: 240px;">
      <select name="country">
        <option value="">All countries</option>
        {% for c in countries %}
          <option value="{{ c }}" {% if country==c %}selected{% endif %}>{{ c }}</option>
        {% endfor %}
      </select>
      <select name="category">
        <option value="">All categories</option>
        {% for cat in categories %}
          <option value="{{ cat }}" {% if category==cat %}selected{% endif %}>{{ cat }}</option>
        {% endfor %}
      </select>
      <button class="btn" type="submit">Apply</button>
    </form>

    <form method="post" action="{{ url_for('force_refresh') }}">
      <button class="btn danger" type="submit">Refresh cache</button>
    </form>
  </div>

  <div class="small">
    Showing {{ results_count }} channels (only channels with streams). Click a stream to play.
  </div>
  <hr>

  {% for item in items %}
    <div class="row">
      <div class="grow">
        <div class="name">{{ item["channel"].name }}</div>
        <div class="meta">
          <span class="pill">Country: {{ item["channel"].country or "—" }}</span>
          {% for cat in item["channel"].categories[:4] %}
            <span class="pill">{{ cat }}</span>
          {% endfor %}
        </div>
        <div class="meta">Channel ID: {{ item["channel"].id }}</div>
      </div>

      <div class="links">
        {% for s in item["streams"][:3] %}
          <form method="post" action="{{ url_for('play') }}">
            <input type="hidden" name="channel_id" value="{{ item['channel'].id }}">
            <input type="hidden" name="stream_index" value="{{ loop.index0 }}">
            <button class="btn" type="submit">
              Play{% if s.quality %} ({{ s.quality }}){% endif %}{% if s.title %} - {{ s.title }}{% endif %}
            </button>
          </form>
        {% endfor %}

        {% if item["streams"]|length > 3 %}
          <a class="btn" href="{{ url_for('channel_detail', channel_id=item['channel'].id) }}">More…</a>
        {% endif %}
      </div>
    </div>
  {% endfor %}

  {% if items|length == 0 %}
    <p>No results.</p>
  {% endif %}
</body>
</html>
"""

DETAIL_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{{ ch.name }} - Streams</title>
  <style>
    body { font-family: system-ui, Arial; margin: 24px; }
    .btn { padding:10px 12px; border:1px solid #222; border-radius:12px; background:#fff; cursor:pointer; }
    .btn:hover { opacity:0.85; }
    .row { display:flex; gap:12px; align-items:center; padding:12px; border:1px solid #ddd; border-radius:14px; margin-bottom:10px; }
    .grow { flex: 1; }
    .name { font-weight: 700; }
    .meta { color:#555; font-size: 12px; word-break: break-all; }
  </style>
</head>
<body>
  <a class="btn" href="{{ url_for('index') }}">Back</a>
  <h2>{{ ch.name }}</h2>
  <div class="meta">Channel ID: {{ ch.id }} | Country: {{ ch.country or "—" }} | Categories: {{ ", ".join(ch.categories) if ch.categories else "—" }}</div>
  <hr>

  {% for s in streams %}
    <div class="row">
      <div class="grow">
        <div class="name">
          {% if s.title %}{{ s.title }}{% else %}Stream {{ loop.index }}{% endif %}
          {% if s.quality %} ({{ s.quality }}){% endif %}
        </div>
        <div class="meta">{{ s.url }}</div>
        {% if s.referrer %}<div class="meta">Referrer: {{ s.referrer }}</div>{% endif %}
        {% if s.user_agent %}<div class="meta">User-Agent: {{ s.user_agent }}</div>{% endif %}
      </div>
      <form method="post" action="{{ url_for('play') }}">
        <input type="hidden" name="channel_id" value="{{ ch.id }}">
        <input type="hidden" name="stream_index" value="{{ loop.index0 }}">
        <button class="btn" type="submit">Play</button>
      </form>
    </div>
  {% endfor %}

  {% if streams|length == 0 %}
    <p>No streams found for this channel.</p>
  {% endif %}
</body>
</html>
"""


# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def index():
    channels_by_id, streams_by_channel = load_data()

    # Query params
    q = (request.args.get("q") or "").strip().lower()
    country = (request.args.get("country") or "MX").strip()  # default MX
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

    return render_template_string(
        TEMPLATE,
        items=items,
        q=q,
        country=country,
        category=category,
        countries=all_countries,
        categories=all_categories,
        results_count=len(items),
    )

@app.get("/channel/<channel_id>")
def channel_detail(channel_id: str):
    channels_by_id, streams_by_channel = load_data()
    ch = channels_by_id.get(channel_id)
    if not ch:
        abort(404)

    streams = streams_by_channel.get(channel_id, [])
    return render_template_string(DETAIL_TEMPLATE, ch=ch, streams=streams)

@app.post("/play")
def play():
    channel_id = (request.form.get("channel_id") or "").strip()
    try:
        stream_index = int(request.form.get("stream_index") or "0")
    except ValueError:
        stream_index = 0

    channels_by_id, streams_by_channel = load_data()
    if channel_id not in streams_by_channel:
        abort(404)

    streams = streams_by_channel[channel_id]
    if stream_index < 0 or stream_index >= len(streams):
        abort(400)

    launch_mpv(streams[stream_index])
    return redirect(request.referrer or url_for("index"))

@app.post("/refresh")
def force_refresh():
    refresh_cache()
    return redirect(url_for("index"))

if __name__ == "__main__":
    # Visit http://127.0.0.1:5000
    app.run(host="127.0.0.1", port=5000, debug=True)
