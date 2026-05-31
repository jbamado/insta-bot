#!/usr/bin/env python3
"""
reel_bot.py — Daily Good News Instagram Reel Generator
=======================================================
Fetches positive news → Claude scripts it → ElevenLabs/gTTS voice →
Pexels background video → Pixabay music → FFmpeg assembly → Make.com → Instagram Reel
"""

import os, json, re, textwrap, random, requests, subprocess, tempfile
from pathlib import Path
from datetime import datetime, timezone

import feedparser
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont
import anthropic

# ── Config ────────────────────────────────────────────────────────────────────
PEXELS_API_KEY      = os.getenv("PEXELS_API_KEY", "")
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
MAKE_REEL_WEBHOOK   = os.getenv("MAKE_REEL_WEBHOOK", "")
ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")   # optional — free at elevenlabs.io
PIXABAY_API_KEY     = os.getenv("PIXABAY_API_KEY", "")       # optional — free at pixabay.com/api

ELEVENLABS_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"  # Sarah — warm, natural English voice

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

    prompt = f"""You are a viral Instagram Reel creator for a Good News page with 50k+ followers.
Your reels are warm, emotional, and make people stop scrolling.

Pick the MOST heartwarming story from this list:
{news_text}

Write the reel content. Reply ONLY in valid JSON — no markdown, no explanation:
{{
  "title": "PUNCHY TITLE IN CAPS — max 5 words, creates curiosity or emotion",
  "narration": "3 sentences, 55-65 words total. Start with something that hooks immediately. Be warm, emotional, conversational — like talking to a friend. End with a hopeful message.",
  "video_query": "3-4 word Pexels search for CINEMATIC, EMOTIONAL footage (e.g. 'happy family reunion', 'elderly couple dancing', 'child first steps', 'volunteers building homes', 'sunrise mountain peaceful'). Pick something visually stunning that matches the story emotion.",
  "caption": "2 punchy lines that make people want to share. Use 1-2 emojis.",
  "hashtags": "#goodnews #positivevibes #hope #inspiration #goodthings #spreadlove #uplift #makeyourday #feelgood #bethechange"
}}"""

    msg = client.messages.create(
        model="claude-opus-4-5", max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    result = json.loads(raw)
    print(f"  Title: {result['title']}")
    print(f"  Query: {result['video_query']}")
    return result

# ── 3. Voice ──────────────────────────────────────────────────────────────────

def generate_voice(text: str, output_path: str):
    """Try ElevenLabs (natural voice) first, fall back to gTTS."""
    if ELEVENLABS_API_KEY:
        try:
            print("  Using ElevenLabs voice...")
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
                headers={
                    "xi-api-key": ELEVENLABS_API_KEY,
                    "Content-Type": "application/json"
                },
                json={
                    "text": text,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {"stability": 0.45, "similarity_boost": 0.80}
                },
                timeout=60
            )
            if r.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(r.content)
                print(f"  ElevenLabs voice saved ({Path(output_path).stat().st_size // 1024} KB)")
                return
            print(f"  ElevenLabs error: {r.status_code} — {r.text[:100]}")
        except Exception as e:
            print(f"  ElevenLabs failed: {e}")

    # Fallback: gTTS
    print("  Using gTTS fallback...")
    tts = gTTS(text=text, lang="en", slow=False)
    tts.save(output_path)
    print(f"  gTTS voice saved ({Path(output_path).stat().st_size // 1024} KB)")

def get_audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True, check=True
    )
    return float(json.loads(r.stdout)["format"]["duration"])

# ── 4. Background Music (Pixabay) ─────────────────────────────────────────────

def get_background_music(tmp_dir: Path) -> str | None:
    """Download a royalty-free background track from Pixabay."""
    # Check if local file exists first
    local = Path("music/background.mp3")
    if local.exists():
        print(f"  Using local music file")
        return str(local)

    if not PIXABAY_API_KEY:
        print("  No PIXABAY_API_KEY — skipping music")
        return None

    queries = ["uplifting background", "positive happy", "inspirational soft", "warm acoustic"]
    random.shuffle(queries)

    for query in queries:
        try:
            r = requests.get(
                "https://pixabay.com/api/",
                params={
                    "key": PIXABAY_API_KEY,
                    "q": query,
                    "media_type": "music",
                    "per_page": 10,
                    "safesearch": "true"
                },
                timeout=15
            )
            if r.status_code != 200:
                continue
            hits = r.json().get("hits", [])
            if not hits:
                continue
            track = random.choice(hits[:5])
            audio_url = track.get("audio", {}).get("preview", "")
            if not audio_url:
                # Try direct download field
                audio_url = track.get("previewURL", track.get("url", ""))
            if not audio_url:
                continue

            music_path = str(tmp_dir / "music.mp3")
            download_file(audio_url, music_path)
            print(f"  Music: '{track.get('tags', query)}'")
            return music_path
        except Exception as e:
            print(f"  Music fetch error ({query}): {e}")

    return None

# ── 5. Pexels Video ───────────────────────────────────────────────────────────

def get_pexels_video(query: str) -> str | None:
    if not PEXELS_API_KEY:
        print("  No PEXELS_API_KEY")
        return None
    headers = {"Authorization": PEXELS_API_KEY}

    # Try portrait HD first, then landscape
    for orientation in ("portrait", "landscape"):
        params = {"query": query, "per_page": 20, "orientation": orientation, "size": "large"}
        try:
            r = requests.get("https://api.pexels.com/videos/search",
                             headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                continue
            videos = r.json().get("videos", [])
            # Prefer longer, higher-quality videos
            videos = [v for v in videos if v.get("duration", 0) >= 12]
            random.shuffle(videos[:8])  # randomise top results for variety
            for video in videos:
                files = sorted(video.get("video_files", []),
                               key=lambda f: f.get("height", 0), reverse=True)
                for f in files:
                    h = f.get("height", 0)
                    if 720 <= h <= 1920:    # HD minimum
                        return f["link"]
        except Exception as e:
            print(f"  Pexels error: {e}")
    return None

def download_file(url: str, path: str):
    r = requests.get(url, stream=True, timeout=120)
    r.raise_for_status()
    with open(path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    print(f"  Downloaded {Path(path).name} ({Path(path).stat().st_size // 1024} KB)")

# ── 6. Text Overlay (PIL) — Cinematic Design ──────────────────────────────────

def make_overlay(script: dict) -> Image.Image:
    img  = Image.new("RGBA", (REEL_W, REEL_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Cinematic gradient ────────────────────────────────────────────────────
    # Dark at very top and heavy at bottom — lets the video breathe in the middle
    for y in range(REEL_H):
        frac = y / REEL_H
        if frac < 0.20:          # top 20% — strong for header
            alpha = int(180 * (1 - frac / 0.20))
        elif frac < 0.50:        # middle — minimal, show the video
            alpha = int(40 + 30 * ((frac - 0.20) / 0.30))
        else:                    # bottom 50% — strong for narration text
            alpha = int(70 + 190 * ((frac - 0.50) / 0.50))
        alpha = min(alpha, 210)
        draw.line([(0, y), (REEL_W, y)], fill=(0, 0, 0, alpha))

    # ── Load fonts ────────────────────────────────────────────────────────────
    try:
        f_tag   = ImageFont.truetype(FONT_BOLD,    42)
        f_title = ImageFont.truetype(FONT_BOLD,    92)
        f_narr  = ImageFont.truetype(FONT_REGULAR, 52)
        f_brand = ImageFont.truetype(FONT_BOLD,    48)
        f_cta   = ImageFont.truetype(FONT_REGULAR, 38)
    except Exception:
        print("  Warning: Roboto fonts not found — using default")
        f_tag = f_title = f_narr = f_brand = f_cta = ImageFont.load_default()

    PAD = 55  # horizontal padding for cards

    # ── Top tag ───────────────────────────────────────────────────────────────
    tag = "✨ GOOD NEWS ✨"
    bbox = draw.textbbox((0, 0), tag, font=f_tag)
    tw = bbox[2] - bbox[0]
    draw.text(((REEL_W - tw) // 2, 90), tag, font=f_tag, fill="#FFD700")

    # ── Title card (orange/warm) ──────────────────────────────────────────────
    title_lines = textwrap.wrap(script["title"], width=14)
    line_h_t    = 108
    title_blk_h = len(title_lines) * line_h_t + 40

    card_t = 165
    card_b = card_t + title_blk_h
    draw.rounded_rectangle(
        [PAD, card_t, REEL_W - PAD, card_b],
        radius=28,
        fill=(220, 100, 20, 200)   # deep orange
    )
    # Thin gold border
    draw.rounded_rectangle(
        [PAD, card_t, REEL_W - PAD, card_b],
        radius=28,
        outline=(255, 210, 0, 200),
        width=3
    )

    ty = card_t + 20
    for line in title_lines:
        bbox = draw.textbbox((0, 0), line, font=f_title)
        tw = bbox[2] - bbox[0]
        tx = (REEL_W - tw) // 2
        draw.text((tx + 3, ty + 3), line, font=f_title, fill=(0, 0, 0, 150))   # shadow
        draw.text((tx, ty),         line, font=f_title, fill="white")
        ty += line_h_t

    # ── Narration card (dark, bottom area) ───────────────────────────────────
    narr_lines  = textwrap.wrap(script["narration"], width=26)
    line_h_n    = 74
    narr_blk_h  = len(narr_lines) * line_h_n + 44

    ncard_b = REEL_H - 250
    ncard_t = ncard_b - narr_blk_h
    draw.rounded_rectangle(
        [PAD, ncard_t, REEL_W - PAD, ncard_b],
        radius=28,
        fill=(10, 10, 20, 195)
    )
    draw.rounded_rectangle(
        [PAD, ncard_t, REEL_W - PAD, ncard_b],
        radius=28,
        outline=(255, 255, 255, 60),
        width=2
    )

    ny = ncard_t + 22
    for line in narr_lines:
        bbox = draw.textbbox((0, 0), line, font=f_narr)
        tw = bbox[2] - bbox[0]
        tx = (REEL_W - tw) // 2
        draw.text((tx + 2, ny + 2), line, font=f_narr, fill=(0, 0, 0, 130))    # shadow
        draw.text((tx, ny),         line, font=f_narr, fill=(255, 255, 255, 240))
        ny += line_h_n

    # ── Branding ──────────────────────────────────────────────────────────────
    brand = "🌍 Good News Today"
    bbox  = draw.textbbox((0, 0), brand, font=f_brand)
    tw    = bbox[2] - bbox[0]
    bx    = (REEL_W - tw) // 2
    draw.text((bx + 2, REEL_H - 198), brand, font=f_brand, fill=(0, 0, 0, 160))
    draw.text((bx,     REEL_H - 200), brand, font=f_brand, fill="#FFD700")

    cta  = "Follow for daily good news! 💛"
    bbox = draw.textbbox((0, 0), cta, font=f_cta)
    tw   = bbox[2] - bbox[0]
    draw.text(((REEL_W - tw) // 2, REEL_H - 135), cta, font=f_cta,
              fill=(255, 255, 255, 210))

    return img

# ── 7. FFmpeg Assembly ────────────────────────────────────────────────────────

def assemble_reel(video_path: str, voice_path: str, overlay: Image.Image,
                  output_path: str, music_path: str | None = None):
    duration  = get_audio_duration(voice_path) + 2.5   # 2.5 sec tail
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
            f"[3:a]volume=0.10,atrim=duration={duration:.2f},"
            f"afade=t=out:st={duration - 1.5:.2f}:d=1.5[music];"
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
         "-c:v", "libx264", "-preset", "fast", "-crf", "20",
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

# ── 8. Upload + Make.com ──────────────────────────────────────────────────────

def upload_video(file_path: str) -> str:
    """Upload video — tenta vários serviços gratuitos até um funcionar."""

    # 1. litterbox.catbox.moe — temporário 72h, sem conta necessária
    try:
        print("  Uploading to litterbox.catbox.moe...")
        with open(file_path, "rb") as f:
            r = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "72h"},
                files={"fileToUpload": ("reel.mp4", f, "video/mp4")},
                timeout=300
            )
        url = r.text.strip()
        if url.startswith("https://"):
            print(f"  URL: {url}")
            return url
        print(f"  litterbox: {r.status_code} — {r.text[:120]}")
    except Exception as e:
        print(f"  litterbox failed: {e}")

    # 2. catbox.moe — permanente, upload anónimo
    try:
        print("  Uploading to catbox.moe...")
        with open(file_path, "rb") as f:
            r = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload", "userhash": ""},
                files={"fileToUpload": ("reel.mp4", f, "video/mp4")},
                timeout=300
            )
        url = r.text.strip()
        if url.startswith("https://"):
            print(f"  URL: {url}")
            return url
        print(f"  catbox.moe: {r.text[:120]}")
    except Exception as e:
        print(f"  catbox.moe failed: {e}")

    # 3. transfer.sh — 14 dias, sem conta
    try:
        print("  Uploading to transfer.sh...")
        with open(file_path, "rb") as f:
            r = requests.put(
                "https://transfer.sh/reel.mp4",
                data=f,
                timeout=300
            )
        url = r.text.strip()
        if url.startswith("https://"):
            print(f"  URL: {url}")
            return url
        print(f"  transfer.sh: {r.status_code} — {r.text[:120]}")
    except Exception as e:
        print(f"  transfer.sh failed: {e}")

    # 4. 0x0.st — último recurso
    try:
        print("  Uploading to 0x0.st...")
        with open(file_path, "rb") as f:
            r = requests.post(
                "https://0x0.st",
                files={"file": ("reel.mp4", f, "video/mp4")},
                timeout=300
            )
        if r.status_code == 200 and r.text.strip().startswith("https://"):
            url = r.text.strip()
            print(f"  URL: {url}")
            return url
        print(f"  0x0.st: {r.status_code} — {r.text[:120]}")
    except Exception as e:
        print(f"  0x0.st failed: {e}")

    raise RuntimeError("Todos os serviços de upload falharam")

def send_to_make(video_url: str, script: dict):
    if not MAKE_REEL_WEBHOOK:
        print("  MAKE_REEL_WEBHOOK not set — skipping")
        return
    caption = (
        f"{script['caption']}\n\n"
        f"{script['hashtags']}\n\n"
        f"🌟 Follow @positivepulseworld for daily good news!"
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
    voice_engine = "ElevenLabs" if ELEVENLABS_API_KEY else "gTTS"
    music_source = "Pixabay" if PIXABAY_API_KEY else ("local" if Path("music/background.mp3").exists() else "none")
    print(f"  Voice: {voice_engine} | Music: {music_source}\n")

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
        print(f"3. Generating voice ({voice_engine})...")
        voice_path = str(tmp / "voice.mp3")
        generate_voice(script["narration"], voice_path)

        # 4. Video
        print(f"4. Fetching Pexels video: '{script['video_query']}'...")
        video_url_pex = get_pexels_video(script["video_query"])
        if not video_url_pex:
            print("  Trying fallback: 'sunrise nature mountains'")
            video_url_pex = get_pexels_video("sunrise nature mountains")
        if not video_url_pex:
            print("ERROR: No Pexels video found"); return

        video_path = str(tmp / "bg.mp4")
        download_file(video_url_pex, video_path)

        # 5. Music
        print("5. Getting background music...")
        music_path = get_background_music(tmp)

        # 6. Overlay
        print("6. Creating text overlay...")
        overlay = make_overlay(script)

        # 7. Assemble
        print("7. Assembling reel...")
        reel_path = str(tmp / "reel.mp4")
        assemble_reel(video_path, voice_path, overlay, reel_path, music_path)

        # 8. Upload + post
        print("8. Uploading & posting...")
        public_url = upload_video(reel_path)
        send_to_make(public_url, script)

    print("\n=== Reel Bot done! ===\n")

if __name__ == "__main__":
    main()
