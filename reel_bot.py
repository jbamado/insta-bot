#!/usr/bin/env python3
"""
reel_bot.py — Daily Good News Instagram Reel Generator
=======================================================
Fetches positive news → Claude scripts it → ElevenLabs/gTTS voice →
Pexels background video → Dynamic subtitles → FFmpeg → Make.com → Instagram
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
ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
PIXABAY_API_KEY     = os.getenv("PIXABAY_API_KEY", "")

ELEVENLABS_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"  # Sarah — warm, natural

REEL_W, REEL_H = 1080, 1920

# ── Brand colors (match carousel/post_bot style) ──────────────────────────────
GOLD  = (255, 200,   0)
WHITE = (255, 255, 255)
BLACK = (  0,   0,   0)

FONT_BEBAS   = "/usr/share/fonts/truetype/bebas/BebasNeue-Regular.ttf"
FONT_BOLD    = "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/roboto/Roboto-Regular.ttf"

NEWS_FEEDS = [
    "https://www.goodnewsnetwork.org/feed/",
    "https://www.positive.news/feed/",
    "https://www.sunnyskyz.com/feed",
    # Instagram accounts via RSSHub (public posts)
    "https://rsshub.app/instagram/user/thegoodnewsmovement",
    "https://rsshub.app/instagram/user/goodnews_movement",
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

def prepare_script(news_items: list[dict], available_videos: dict[str, str] = {}) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    news_text = "\n".join(f"- {n['title']}" for n in news_items)

    if available_videos:
        video_options = "\n".join(f"- {theme}" for theme in available_videos.keys())
        video_section = f"""
AVAILABLE VIDEO THEMES (pre-fetched from Pexels — these are confirmed available):
{video_options}

Choose the story that BEST MATCHES one of these themes visually.
Return the EXACT theme name in "video_theme"."""
    else:
        video_section = 'Return a cinematic 3-word Pexels query in "video_theme".'

    prompt = f"""You create viral Instagram Reels for @positivepulse.world — tech, AI, money and innovation breakthroughs.

Style: @wealth but GOOD NEWS. Bold, stops the scroll, makes people save the post.
{video_section}

Stories:
{news_text}

Reply ONLY in valid JSON — no markdown:
{{
  "title": "MAX 4 WORDS ALL CAPS. Examples: 'AI CHANGED EVERYTHING', 'NOBODY SAW THIS', 'FUTURE IS HERE'",
  "narration": "3 punchy sentences. MAX 45 words. Shocking fact first. Power words. Real-world impact at end.",
  "hook": "3-4 WORDS ALL CAPS. Examples: 'WAIT FOR THIS', 'GAME CHANGER', 'THIS IS HUGE'",
  "video_theme": "exact theme name from list above",
  "caption": "Line 1: Bold statement + 1 emoji people want to save. Line 2: Question inviting comments.",
  "hashtags": "#technology #innovation #AI #future #tech #investing #breakthrough #science #positivepulse #goodvibes"
}}"""

    msg = client.messages.create(
        model="claude-opus-4-5", max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    result = json.loads(raw)
    print(f"  Title: {result['title']}")
    print(f"  Video theme: {result.get('video_theme', 'N/A')}")
    return result

# ── 3. Voice ──────────────────────────────────────────────────────────────────

def generate_voice(text: str, output_path: str):
    if ELEVENLABS_API_KEY:
        try:
            print("  Using ElevenLabs voice...")
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
                headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
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

# ── 4. Dynamic Subtitles (ASS format) ────────────────────────────────────────

def generate_subtitles(narration: str, voice_duration: float, output_path: str,
                       hook: str = ""):
    """ASS subtitles: 3-word chunks, fade-in, yellow/white alternating, optional hook."""
    words = narration.split()
    chunks = [' '.join(words[i:i+3]) for i in range(0, len(words), 3)]

    t_start = 0.3
    t_end   = voice_duration - 0.8
    chunk_dur = (t_end - t_start) / max(len(chunks), 1)

    def fmt(sec: float) -> str:
        sec = max(sec, 0)
        h, r = divmod(sec, 3600)
        m, s = divmod(r, 60)
        return f"{int(h):01d}:{int(m):02d}:{s:05.2f}"

    ass = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {REEL_W}\n"
        f"PlayResY: {REEL_H}\n"
        "WrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
        "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        # Gold bold — main subtitle (ASS AABBGGRR: gold=&H0000C8FF)
        "Style: White,Roboto Bold,96,&H0000C8FF,&H000000FF,"
        "&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,8,2,"
        "2,60,60,430,1\n"
        # White bold — alternating subtitle
        "Style: Yellow,Roboto Bold,96,&H00FFFFFF,&H000000FF,"
        "&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,8,2,"
        "2,60,60,430,1\n"
        # Hook — top-aligned, gold large
        "Style: Hook,Roboto Bold,84,&H0000C8FF,&H000000FF,"
        "&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,7,2,"
        "8,80,80,510,1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    # Hook text shown below title card (top-aligned) for first 2 seconds
    if hook:
        clean = hook.upper().strip()
        ass += f"Dialogue: 0,{fmt(0.0)},{fmt(2.0)},Hook,,0,0,0,,{{\\fad(250,350)}}{clean}\n"

    # Narration chunks — ALL CAPS, fade-in, alternating gold/white
    for i, chunk in enumerate(chunks):
        s = t_start + i * chunk_dur
        e = s + chunk_dur + 0.06
        style = "Yellow" if i % 3 == 1 else "White"
        ass += f"Dialogue: 0,{fmt(s)},{fmt(e)},{style},,0,0,0,,{{\\fad(110,0)}}{chunk.upper()}\n"

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass)
    print(f"  Subtitles: {len(chunks)} chunks {'+ hook' if hook else ''}")

# ── 5. Pexels Video ───────────────────────────────────────────────────────────

# ── Cinematic video themes — always look stunning ─────────────────────────────
VIDEO_THEMES = [
    ("neon city night",        "futuristic urban energy"),
    ("space galaxy stars",     "cosmos and universe"),
    ("ocean waves blue",       "nature and environment"),
    ("solar energy field",     "renewable energy and green tech"),
    ("drone aerial city",      "bird's eye city view"),
    ("electric car speed",     "future of transport"),
    ("server room blue",       "data and technology"),
    ("rocket launch fire",     "space exploration"),
    ("skyscraper aerial view", "modern architecture"),
    ("futuristic robot arm",   "AI and automation"),
    ("stock market charts",    "finance and investing"),
    ("wind turbines sunset",   "clean energy"),
]

def _search_pexels_video(query: str, headers: dict) -> str | None:
    """Search Pexels for one query, return video URL or None."""
    for orientation in ("portrait", "landscape"):
        params = {"query": query, "per_page": 12, "orientation": orientation, "size": "large"}
        try:
            r = requests.get("https://api.pexels.com/videos/search",
                             headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                continue
            videos = [v for v in r.json().get("videos", []) if v.get("duration", 0) >= 12]
            if not videos:
                continue
            random.shuffle(videos[:5])
            for video in videos:
                files = sorted(video.get("video_files", []),
                               key=lambda f: f.get("height", 0), reverse=True)
                for f in files:
                    if 720 <= f.get("height", 0) <= 1920:
                        return f["link"]
        except Exception:
            pass
    return None

def prefetch_videos() -> dict[str, str]:
    """Pre-fetch video URLs for a random selection of cinematic themes.
    Returns {theme_name: video_url}."""
    if not PEXELS_API_KEY:
        return {}
    headers  = {"Authorization": PEXELS_API_KEY}
    themes   = random.sample(VIDEO_THEMES, min(8, len(VIDEO_THEMES)))
    available = {}
    for query, description in themes:
        url = _search_pexels_video(query, headers)
        if url:
            available[query] = url
            print(f"  ✓ '{query}'")
        if len(available) >= 6:   # 6 options is enough
            break
    print(f"  {len(available)} video themes pre-fetched")
    return available

def get_pexels_video(query: str) -> str | None:
    """Fallback: search Pexels directly for a query."""
    if not PEXELS_API_KEY:
        return None
    headers = {"Authorization": PEXELS_API_KEY}
    for q in [query, query.split()[0]]:
        url = _search_pexels_video(q, headers)
        if url:
            return url
    return None

def download_file(url: str, path: str):
    r = requests.get(url, stream=True, timeout=120)
    r.raise_for_status()
    with open(path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    print(f"  Downloaded {Path(path).name} ({Path(path).stat().st_size // 1024} KB)")

# ── 6. Background Music ───────────────────────────────────────────────────────

def has_audio_stream(path: str) -> bool:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-of", "json", path],
            capture_output=True, text=True, check=True
        )
        return bool(json.loads(r.stdout).get("streams"))
    except Exception:
        return False

def get_background_music() -> str | None:
    music_dir = Path("music")
    if music_dir.exists():
        for mp3 in sorted(music_dir.glob("*.mp3")):
            if has_audio_stream(str(mp3)):
                print(f"  Music: {mp3.name}")
                return str(mp3)
    print("  No music — add MP3 to music/ folder to enable")
    return None

# ── 7. Text Overlay (PIL) — @wealth style: dark + gold ───────────────────────

def _shadow_text(draw, x, y, text, font, fill=None, shadow=5):
    """Texto com sombra preta — igual ao post_bot."""
    if fill is None:
        fill = GOLD
    for dx in range(-shadow, shadow + 1, 2):
        for dy in range(-shadow, shadow + 1, 2):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=BLACK)
    draw.text((x, y), text, font=font, fill=fill)

def _centered_text(draw, y, text, font, fill=None, shadow=5):
    if fill is None:
        fill = GOLD
    bb = draw.textbbox((0, 0), text, font=font)
    tw = bb[2] - bb[0]
    _shadow_text(draw, (REEL_W - tw) // 2, y, text, font, fill, shadow)
    return bb[3] - bb[1]

def _draw_brand_bar(draw, y):
    """● POSITIVE PULSE ● com linhas douradas — igual ao post_bot."""
    try:
        bf = ImageFont.truetype(FONT_REGULAR, 32)
    except Exception:
        bf = ImageFont.load_default()
    txt  = "POSITIVE PULSE"
    dot  = "●"
    full = f"{dot}  {txt}  {dot}"
    bb   = draw.textbbox((0, 0), full, font=bf)
    fw   = bb[2] - bb[0]
    cx   = REEL_W // 2
    margin = 60

    line_end = cx - fw // 2 - 18
    if line_end > margin:
        draw.rectangle([(margin, y + 14), (line_end, y + 17)], fill=GOLD)
    draw.text((cx - fw // 2, y), full, font=bf, fill=GOLD)
    line2_start = cx + fw // 2 + 18
    if line2_start < REEL_W - margin:
        draw.rectangle([(line2_start, y + 14), (REEL_W - margin, y + 17)], fill=GOLD)

def make_overlay(script: dict) -> Image.Image:
    """@wealth style overlay: dark gradient, gold Impact title, POSITIVE PULSE brand."""
    img  = Image.new("RGBA", (REEL_W, REEL_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Heavy dark gradient top + bottom, clear in the middle (video breathes)
    for y in range(REEL_H):
        frac = y / REEL_H
        if frac < 0.45:
            alpha = int(230 * (1 - frac / 0.45) ** 0.6)
        elif frac < 0.60:
            alpha = int(30 + 40 * ((frac - 0.45) / 0.15))
        else:
            alpha = int(70 + 200 * ((frac - 0.60) / 0.40) ** 0.7)
        draw.line([(0, y), (REEL_W, y)], fill=(0, 0, 0, min(int(alpha), 235)))

    # Load Bebas Neue (or fallback to Roboto Bold)
    try:
        f_title = ImageFont.truetype(FONT_BEBAS, 120)
        f_sub   = ImageFont.truetype(FONT_BEBAS,  52)
    except Exception:
        try:
            f_title = ImageFont.truetype(FONT_BOLD, 108)
            f_sub   = ImageFont.truetype(FONT_BOLD,  48)
        except Exception:
            f_title = f_sub = ImageFont.load_default()

    try:
        f_regular = ImageFont.truetype(FONT_REGULAR, 36)
    except Exception:
        f_regular = ImageFont.load_default()

    # ── Title — top, gold, bold, ALL CAPS ─────────────────────────────────────
    title = script["title"].upper()
    title_lines = textwrap.wrap(title, width=14)
    ty = 90
    for line in title_lines:
        h = _centered_text(draw, ty, line, f_title, fill=GOLD, shadow=6)
        ty += h + 12

    # ── Branding bar at bottom ─────────────────────────────────────────────────
    _draw_brand_bar(draw, REEL_H - 110)

    return img

# ── 8. FFmpeg Assembly ────────────────────────────────────────────────────────

def assemble_reel(video_path: str, voice_path: str, overlay: Image.Image,
                  output_path: str, subtitle_path: str | None = None,
                  music_path: str | None = None):
    duration  = get_audio_duration(voice_path) + 2.5
    overlay_p = output_path.replace(".mp4", "_ov.png")
    overlay.save(overlay_p)

    inputs = [
        "-stream_loop", "-1", "-i", video_path,   # 0: bg video
        "-i", overlay_p,                           # 1: title/branding overlay
        "-i", voice_path,                          # 2: voice
    ]

    # Build video filter chain
    # Dark cinematic grade matching @wealth style: desaturate slightly, darken
    grade = (
        "eq=saturation=0.85:contrast=1.10:brightness=-0.05:gamma=0.88,"
        "colorbalance=rs=-0.02:gs=-0.02:bs=-0.03"
    )

    has_subs = subtitle_path and Path(subtitle_path).exists()
    if has_subs:
        vf = (
            f"[0:v]trim=duration={duration:.2f},setpts=PTS-STARTPTS,"
            f"scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=increase,"
            f"crop={REEL_W}:{REEL_H},{grade}[bg];"
            f"[bg][1:v]overlay=0:0[bg_ov];"
            f"[bg_ov]ass='{subtitle_path}'[v]"
        )
    else:
        vf = (
            f"[0:v]trim=duration={duration:.2f},setpts=PTS-STARTPTS,"
            f"scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=increase,"
            f"crop={REEL_W}:{REEL_H},{grade}[bg];"
            f"[bg][1:v]overlay=0:0[v]"
        )

    has_music = music_path and Path(music_path).exists() and has_audio_stream(music_path)
    if has_music:
        inputs += ["-i", music_path]
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

    print(f"  FFmpeg assembling ({duration:.1f}s, subtitles={'yes' if has_subs else 'no'})...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    Path(overlay_p).unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"  FFmpeg error:\n{result.stderr[-3000:]}")
        raise RuntimeError("FFmpeg assembly failed")

    size_mb = Path(output_path).stat().st_size / 1e6
    print(f"  Reel ready: {output_path} ({size_mb:.1f} MB)")

# ── 9. Upload + Make.com ──────────────────────────────────────────────────────

def upload_video(file_path: str) -> str:
    # 1. litterbox.catbox.moe
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

    # 2. catbox.moe
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

    # 3. transfer.sh
    try:
        print("  Uploading to transfer.sh...")
        with open(file_path, "rb") as f:
            r = requests.put("https://transfer.sh/reel.mp4", data=f, timeout=300)
        url = r.text.strip()
        if url.startswith("https://"):
            print(f"  URL: {url}")
            return url
    except Exception as e:
        print(f"  transfer.sh failed: {e}")

    # 4. 0x0.st
    try:
        print("  Uploading to 0x0.st...")
        with open(file_path, "rb") as f:
            r = requests.post("https://0x0.st",
                              files={"file": ("reel.mp4", f, "video/mp4")}, timeout=300)
        if r.status_code == 200 and r.text.strip().startswith("https://"):
            print(f"  URL: {r.text.strip()}")
            return r.text.strip()
    except Exception as e:
        print(f"  0x0.st failed: {e}")

    raise RuntimeError("All upload services failed")

def send_to_make(video_url: str, script: dict):
    if not MAKE_REEL_WEBHOOK:
        print("  MAKE_REEL_WEBHOOK not set — skipping")
        return
    caption = (
        f"{script['caption']}\n\n"
        f"{script['hashtags']}\n\n"
        f"Follow @positivepulseworld for daily good news!"
    )
    r = requests.post(MAKE_REEL_WEBHOOK,
                      json={"video_url": video_url, "caption": caption}, timeout=30)
    print(f"  Make.com: {r.status_code}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== Reel Bot — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===\n")
    voice_engine = "ElevenLabs" if ELEVENLABS_API_KEY else "gTTS"
    print(f"  Voice: {voice_engine}\n")

    # 1. News
    print("1. Fetching news...")
    news = fetch_news()
    if not news:
        print("ERROR: No news found"); return

    # 2. Pre-fetch videos (Option B — video first, story second)
    print("2. Pre-fetching cinematic videos...")
    available_videos = prefetch_videos()

    # 3. Claude picks best story+video combo
    print("3. Claude scripting (matching story to video)...")
    script = prepare_script(news, available_videos)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # 4. Voice
        print(f"4. Generating voice ({voice_engine})...")
        voice_path = str(tmp / "voice.mp3")
        generate_voice(script["narration"], voice_path)
        voice_dur = get_audio_duration(voice_path)

        # 5. Subtitles
        print("5. Generating subtitles...")
        sub_path = str(tmp / "subs.ass")
        hook_text = script.get("hook", "")
        generate_subtitles(script["narration"], voice_dur, sub_path, hook_text)
        if hook_text:
            print(f"  Hook: '{hook_text}'")

        # 6. Get pre-fetched video
        chosen_theme = script.get("video_theme", "")
        video_url_pex = available_videos.get(chosen_theme)
        if video_url_pex:
            print(f"6. Using pre-fetched video: '{chosen_theme}'")
        else:
            print(f"6. Theme '{chosen_theme}' not in cache — searching Pexels...")
            video_url_pex = get_pexels_video(chosen_theme or "futuristic technology")
        if not video_url_pex:
            print("ERROR: No Pexels video found"); return

        video_path = str(tmp / "bg.mp4")
        download_file(video_url_pex, video_path)

        # 7. Music
        print("7. Getting background music...")
        music_path = get_background_music()

        # 8. Overlay
        print("8. Creating overlay...")
        overlay = make_overlay(script)

        # 9. Assemble
        print("9. Assembling reel...")
        reel_path = str(tmp / "reel.mp4")
        assemble_reel(video_path, voice_path, overlay, reel_path, sub_path, music_path)

        # 10. Upload + post
        print("10. Uploading & posting...")
        public_url = upload_video(reel_path)
        send_to_make(public_url, script)

    print("\n=== Reel Bot done! ===\n")

if __name__ == "__main__":
    main()
