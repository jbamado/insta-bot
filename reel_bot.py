#!/usr/bin/env python3
"""
reel_bot.py — Daily Good News Instagram Reel Generator
=======================================================
Fetches positive news → Claude scripts it → gTTS voice →
Pexels background video → FFmpeg assembly → Make.com → Instagram Reel
"""

import os, json, re, textwrap, requests, subprocess, tempfile
from pathlib import Path
from datetime import datetime, timezone

import feedparser
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont
import anthropic

# ── Config ────────────────────────────────────────────────────────────────────
PEXELS_API_KEY    = os.getenv("PEXELS_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MAKE_REEL_WEBHOOK = os.getenv("MAKE_REEL_WEBHOOK", "")

REEL_W, REEL_H = 1080, 1920   # 9:16 vertical

NEWS_FEEDS = [
    "https://www.goodnewsnetwork.org/feed/",
    "https://www.positive.news/feed/",
    "https://www.sunnyskyz.com/feed",
]

FONT_BOLD    = "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/roboto/Roboto-Regular.ttf"

# ── 1. Fetch News ─────────────────────────────────────────────────────────────

def fetch_news(max_items: int = 15) -> list[dict]:
    items = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ReelBot/1.0)"}
    for url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url, request_headers=headers)
            for entry in feed.entries[:6]:
                title   = entry.get("title", "").strip()
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:200].strip()
                if title:
                    items.append({"title": title, "summary": summary})
        except Exception as e:
            print(f"  Feed error {url}: {e}")
    print(f"  {len(items)} stories fetched")
    return items[:max_items]

# ── 2. Claude Script ──────────────────────────────────────────────────────────

def prepare_script(news_items: list[dict]) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    news_text = "\n".join(f"- {n['title']}" for n in news_items)

    prompt = f"""You create warm, uplifting 30-second Instagram Reels about good news from around the world.

From these stories, pick the MOST heartwarming one:
{news_text}

Write the reel content. Reply ONLY in valid JSON — no markdown, no explanation:
{{
  "title": "SHORT PUNCHY TITLE IN CAPS (max 6 words)",
  "narration": "Exactly 3 warm sentences read aloud. 55-65 words total. Emotional, positive, inspiring.",
  "video_query": "2-3 word Pexels video search (e.g. 'sunrise ocean', 'children laughing', 'community helping')",
  "caption": "2 short engaging lines for Instagram",
  "hashtags": "#goodnews #positivevibes #hope #inspiration #goodthings #spreadlove #uplift"
}}"""

    msg = client.messages.create(
        model="claude-opus-4-5", max_tokens=450,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    result = json.loads(raw)
    print(f"  Title: {result['title']}")
    print(f"  Query: {result['video_query']}")
    return result

# ── 3. Voice (gTTS) ───────────────────────────────────────────────────────────

def generate_voice(text: str, output_path: str):
    tts = gTTS(text=text, lang="en", slow=False)
    tts.save(output_path)
    print(f"  Voice saved ({Path(output_path).stat().st_size // 1024} KB)")

def get_audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True, check=True
    )
    return float(json.loads(r.stdout)["format"]["duration"])

# ── 4. Pexels Video ───────────────────────────────────────────────────────────

def get_pexels_video(query: str) -> str | None:
    if not PEXELS_API_KEY:
        print("  No PEXELS_API_KEY")
        return None
    headers = {"Authorization": PEXELS_API_KEY}
    for orientation in ("portrait", "landscape"):
        params = {"query": query, "per_page": 15, "orientation": orientation}
        try:
            r = requests.get("https://api.pexels.com/videos/search",
                             headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                continue
            for video in r.json().get("videos", []):
                if video.get("duration", 0) < 10:
                    continue
                files = sorted(video.get("video_files", []),
                               key=lambda f: f.get("height", 0), reverse=True)
                for f in files:
                    if 480 <= f.get("height", 0) <= 1920:
                        return f["link"]
        except Exception as e:
            print(f"  Pexels error: {e}")
    return None

def download_file(url: str, path: str):
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    print(f"  Downloaded {Path(path).name} ({Path(path).stat().st_size // 1024} KB)")

# ── 5. Text Overlay (PIL) ─────────────────────────────────────────────────────

def make_overlay(script: dict) -> Image.Image:
    img  = Image.new("RGBA", (REEL_W, REEL_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark gradient overlay for readability
    for y in range(REEL_H):
        alpha = int(160 * abs(y / REEL_H - 0.5) * 2)
        alpha = min(alpha + 60, 180)
        draw.line([(0, y), (REEL_W, y)], fill=(0, 0, 0, alpha))

    # Load fonts
    try:
        f_title  = ImageFont.truetype(FONT_BOLD,    80)
        f_narr   = ImageFont.truetype(FONT_REGULAR, 48)
        f_brand  = ImageFont.truetype(FONT_BOLD,    50)
    except Exception:
        print("  Warning: Roboto fonts not found — using default")
        f_title = f_narr = f_brand = ImageFont.load_default()

    # ── Title (top) ──────────────────────────────────────────────────────────
    title_lines = textwrap.wrap(script["title"], width=16)
    y = 140
    for line in title_lines:
        bbox = draw.textbbox((0, 0), line, font=f_title)
        w    = bbox[2] - bbox[0]
        x    = (REEL_W - w) // 2
        # Shadow
        draw.text((x + 3, y + 3), line, font=f_title, fill=(0, 0, 0, 200))
        draw.text((x, y),         line, font=f_title, fill="white")
        y += 100

    # ── Narration (centre) ───────────────────────────────────────────────────
    narr_lines = textwrap.wrap(script["narration"], width=28)
    line_h     = 68
    total_h    = len(narr_lines) * line_h
    y          = (REEL_H - total_h) // 2 - 40

    for line in narr_lines:
        bbox = draw.textbbox((0, 0), line, font=f_narr)
        w    = bbox[2] - bbox[0]
        x    = (REEL_W - w) // 2
        draw.text((x + 2, y + 2), line, font=f_narr, fill=(0, 0, 0, 180))
        draw.text((x, y),         line, font=f_narr, fill="white")
        y += line_h

    # ── Branding (bottom) ────────────────────────────────────────────────────
    brand = "Good News Today ❤️"
    bbox  = draw.textbbox((0, 0), brand, font=f_brand)
    w     = bbox[2] - bbox[0]
    x     = (REEL_W - w) // 2
    draw.text((x + 2, REEL_H - 178), brand, font=f_brand, fill=(0, 0, 0, 180))
    draw.text((x,     REEL_H - 180), brand, font=f_brand, fill="#FFD700")

    return img

# ── 6. FFmpeg Assembly ────────────────────────────────────────────────────────

def assemble_reel(video_path: str, voice_path: str, overlay: Image.Image,
                  output_path: str, music_path: str | None = None):
    duration  = get_audio_duration(voice_path) + 2.0  # 2 sec tail
    overlay_p = output_path.replace(".mp4", "_ov.png")
    overlay.save(overlay_p)

    inputs = [
        "-stream_loop", "-1", "-i", video_path,   # 0: bg video (looped)
        "-i", overlay_p,                           # 1: text overlay PNG
        "-i", voice_path,                          # 2: voice
    ]

    # Video filter: scale/crop → overlay text
    vf = (
        f"[0:v]trim=duration={duration:.2f},setpts=PTS-STARTPTS,"
        f"scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=increase,"
        f"crop={REEL_W}:{REEL_H}[bg];"
        f"[bg][1:v]overlay=0:0[v]"
    )

    has_music = music_path and Path(music_path).exists()
    if has_music:
        inputs += ["-i", music_path]               # 3: music
        af = (
            f"[2:a]volume=1.0[voice];"
            f"[3:a]volume=0.12,atrim=duration={duration:.2f}[music];"
            f"[voice][music]amix=inputs=2:duration=first[a]"
        )
        fc   = vf + ";" + af
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        fc   = vf
        maps = ["-map", "[v]", "-map", "2:a"]

    cmd = (
        ["ffmpeg", "-y"] + inputs +
        ["-filter_complex", fc] + maps +
        ["-t", f"{duration:.2f}",
         "-c:v", "libx264", "-preset", "fast", "-crf", "22",
         "-c:a", "aac", "-b:a", "128k",
         "-r", "30", "-movflags", "+faststart",
         output_path]
    )

    print(f"  FFmpeg assembling ({duration:.1f}s)...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    Path(overlay_p).unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"  FFmpeg error:\n{result.stderr[-3000:]}")
        raise RuntimeError("FFmpeg assembly failed")

    size_mb = Path(output_path).stat().st_size / 1e6
    print(f"  Reel ready: {output_path} ({size_mb:.1f} MB)")

# ── 7. Upload + Make.com ──────────────────────────────────────────────────────

def upload_video(file_path: str) -> str:
    """Upload to file.io (free, expires 1 day). Returns public URL."""
    print("  Uploading to file.io...")
    with open(file_path, "rb") as f:
        r = requests.post(
            "https://file.io",
            files={"file": ("reel.mp4", f, "video/mp4")},
            data={"expires": "1d"},
            timeout=180
        )
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"Upload failed: {data}")
    url = data["link"]
    print(f"  URL: {url}")
    return url

def send_to_make(video_url: str, script: dict):
    if not MAKE_REEL_WEBHOOK:
        print("  MAKE_REEL_WEBHOOK not set — skipping")
        return
    caption = (
        f"{script['caption']}\n\n"
        f"{script['hashtags']}\n\n"
        f"🌟 Follow for daily good news!"
    )
    r = requests.post(
        MAKE_REEL_WEBHOOK,
        json={"video_url": video_url, "caption": caption},
        timeout=30
    )
    print(f"  Make.com: {r.status_code}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== Reel Bot — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===\n")

    # 1. News
    print("1. Fetching news...")
    news = fetch_news()
    if not news:
        print("ERROR: No news found"); return

    # 2. Script
    print("2. Claude scripting...")
    script = prepare_script(news)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # 3. Voice
        print("3. Generating voice (gTTS)...")
        voice_path = str(tmp / "voice.mp3")
        generate_voice(script["narration"], voice_path)

        # 4. Video
        print(f"4. Fetching Pexels video: '{script['video_query']}'...")
        video_url_pex = get_pexels_video(script["video_query"])
        if not video_url_pex:
            print("  Trying fallback: 'sunrise nature'")
            video_url_pex = get_pexels_video("sunrise nature")
        if not video_url_pex:
            print("ERROR: No Pexels video found"); return

        video_path = str(tmp / "bg.mp4")
        download_file(video_url_pex, video_path)

        # 5. Overlay
        print("5. Creating text overlay...")
        overlay = make_overlay(script)

        # 6. Assemble
        print("6. Assembling reel...")
        reel_path  = str(tmp / "reel.mp4")
        music_path = "music/background.mp3"
        assemble_reel(video_path, voice_path, overlay, reel_path, music_path)

        # 7. Upload + post
        print("7. Uploading & posting...")
        public_url = upload_video(reel_path)
        send_to_make(public_url, script)

    print("\n=== Reel Bot done! ===\n")

if __name__ == "__main__":
    main()
