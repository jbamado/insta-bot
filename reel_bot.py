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

    prompt = f"""You create viral Instagram Reels for a good news page. Study what @goodnews_movement (4M followers) does:
- Stories about REAL PEOPLE doing unexpected acts of kindness
- Titles that are EMOTIONAL not descriptive ("HE GAVE EVERYTHING" not "MAN DONATES MONEY")
- Content people DM to friends because it made them smile or tear up

ONLY pick stories about: acts of kindness, children doing amazing things, animals rescued,
communities helping, underdogs winning, strangers helping strangers.
AVOID: inventions, science facts, corporate news, statistics without a human face.

Stories:
{news_text}

Reply ONLY in valid JSON — no markdown, no explanation:
{{
  "title": "3-4 WORDS MAX IN CAPS — emotional, stops the scroll. Focus on the FEELING not the fact. Examples: 'HE GAVE EVERYTHING', 'NOBODY EXPECTED THIS', 'SHE CHANGED 100 LIVES'",
  "narration": "3 sentences MAX. Under 40 words. Start mid-action, no slow build. Short punchy sentences. End with hope. Sound like texting a friend about something unbelievable.",
  "video_query": "3-4 word Pexels search matching the EMOTION (e.g. 'elderly couple embrace', 'child laughing outside', 'volunteers helping people', 'woman happy tears', 'community celebration'). Match the feeling of the story.",
  "caption": "Line 1: Share-worthy hook with 1 emoji. Line 2: Question that invites comments.",
  "hashtags": "#goodnews #kindness #humanity #hope #inspiration #spreadlove #feelgood #makeyourday #bethechange #positivevibes"
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
    print(f"  Query: {result['video_query']}")
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

def generate_subtitles(narration: str, voice_duration: float, output_path: str):
    """Generate ASS subtitle file — 3-4 word chunks timed evenly across narration."""
    words = narration.split()
    chunks = [' '.join(words[i:i+3]) for i in range(0, len(words), 3)]  # 3 words = dynamic

    t_start = 0.4
    t_end   = voice_duration - 0.8
    chunk_dur = (t_end - t_start) / max(len(chunks), 1)

    def fmt(sec: float) -> str:
        sec = max(sec, 0)
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h:01d}:{m:02d}:{s:05.2f}"

    # ASS style — large white bold text, black outline, bottom-center
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
        # White text, thick black outline, bottom-centre (alignment=2), MarginV=380
        # Font 88px, white, thick black outline (7), bold, bottom-centre, MarginV=400
        "Style: Default,Roboto Bold,88,&H00FFFFFF,&H000000FF,"
        "&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,7,2,"
        "2,60,60,400,1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    for i, chunk in enumerate(chunks):
        s = t_start + i * chunk_dur
        e = s + chunk_dur + 0.05   # tiny overlap so there's no flash of empty
        ass += f"Dialogue: 0,{fmt(s)},{fmt(e)},Default,,0,0,0,,{chunk}\n"

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass)
    print(f"  Subtitles: {len(chunks)} chunks over {voice_duration:.1f}s")

# ── 5. Pexels Video ───────────────────────────────────────────────────────────

def get_pexels_video(query: str) -> str | None:
    if not PEXELS_API_KEY:
        print("  No PEXELS_API_KEY")
        return None
    headers = {"Authorization": PEXELS_API_KEY}
    for orientation in ("portrait", "landscape"):
        params = {"query": query, "per_page": 20, "orientation": orientation, "size": "large"}
        try:
            r = requests.get("https://api.pexels.com/videos/search",
                             headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                continue
            videos = [v for v in r.json().get("videos", []) if v.get("duration", 0) >= 12]
            random.shuffle(videos[:8])
            for video in videos:
                files = sorted(video.get("video_files", []),
                               key=lambda f: f.get("height", 0), reverse=True)
                for f in files:
                    if 720 <= f.get("height", 0) <= 1920:
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

# ── 7. Text Overlay (PIL) — Minimal: hook + branding only ─────────────────────

def make_overlay(script: dict) -> Image.Image:
    """Minimal overlay: punchy title at top + branding at bottom.
    The narration is handled by dynamic ASS subtitles, not static text."""
    img  = Image.new("RGBA", (REEL_W, REEL_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Gradient: dark at top (title area) and bottom (branding), clear in middle
    for y in range(REEL_H):
        frac = y / REEL_H
        if frac < 0.38:
            alpha = int(210 * (1 - frac / 0.38) ** 0.7)
        elif frac < 0.62:
            alpha = 0   # completely clear — let the video breathe
        else:
            alpha = int(220 * ((frac - 0.62) / 0.38) ** 0.7)
        draw.line([(0, y), (REEL_W, y)], fill=(0, 0, 0, min(alpha, 215)))

    try:
        f_tag   = ImageFont.truetype(FONT_BOLD,    44)
        f_title = ImageFont.truetype(FONT_BOLD,    98)
        f_brand = ImageFont.truetype(FONT_BOLD,    50)
        f_cta   = ImageFont.truetype(FONT_REGULAR, 38)
    except Exception:
        print("  Warning: fonts not found — using default")
        f_tag = f_title = f_brand = f_cta = ImageFont.load_default()

    PAD = 55

    # ── Top tag ───────────────────────────────────────────────────────────────
    tag = "* GOOD NEWS *"
    bbox = draw.textbbox((0, 0), tag, font=f_tag)
    tw = bbox[2] - bbox[0]
    draw.text(((REEL_W - tw) // 2, 80), tag, font=f_tag, fill="#FFD700")

    # ── Title card (orange) ──────────────────────────────────────────────────
    title_lines = textwrap.wrap(script["title"], width=13)
    line_h_t    = 112
    title_blk_h = len(title_lines) * line_h_t + 44
    card_t = 155
    card_b = card_t + title_blk_h

    draw.rounded_rectangle([PAD, card_t, REEL_W - PAD, card_b],
                           radius=30, fill=(215, 90, 15, 220))
    draw.rounded_rectangle([PAD, card_t, REEL_W - PAD, card_b],
                           radius=30, outline=(255, 210, 0, 210), width=4)

    ty = card_t + 22
    for line in title_lines:
        bbox = draw.textbbox((0, 0), line, font=f_title)
        tw = bbox[2] - bbox[0]
        tx = (REEL_W - tw) // 2
        draw.text((tx + 3, ty + 3), line, font=f_title, fill=(0, 0, 0, 160))
        draw.text((tx, ty),         line, font=f_title, fill="white")
        ty += line_h_t

    # ── Branding ──────────────────────────────────────────────────────────────
    brand = "Good News Today"
    bbox  = draw.textbbox((0, 0), brand, font=f_brand)
    tw    = bbox[2] - bbox[0]
    bx    = (REEL_W - tw) // 2
    draw.text((bx + 2, REEL_H - 205), brand, font=f_brand, fill=(0, 0, 0, 160))
    draw.text((bx,     REEL_H - 207), brand, font=f_brand, fill="#FFD700")

    cta  = "Follow for daily good news!"
    bbox = draw.textbbox((0, 0), cta, font=f_cta)
    tw   = bbox[2] - bbox[0]
    draw.text(((REEL_W - tw) // 2, REEL_H - 140),
              cta, font=f_cta, fill=(255, 255, 255, 210))

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
    has_subs = subtitle_path and Path(subtitle_path).exists()
    if has_subs:
        vf = (
            f"[0:v]trim=duration={duration:.2f},setpts=PTS-STARTPTS,"
            f"scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=increase,"
            f"crop={REEL_W}:{REEL_H}[bg];"
            f"[bg][1:v]overlay=0:0[bg_ov];"
            f"[bg_ov]ass='{subtitle_path}'[v]"
        )
    else:
        vf = (
            f"[0:v]trim=duration={duration:.2f},setpts=PTS-STARTPTS,"
            f"scale={REEL_W}:{REEL_H}:force_original_aspect_ratio=increase,"
            f"crop={REEL_W}:{REEL_H}[bg];"
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

    # 2. Script
    print("2. Claude scripting...")
    script = prepare_script(news)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # 3. Voice
        print(f"3. Generating voice ({voice_engine})...")
        voice_path = str(tmp / "voice.mp3")
        generate_voice(script["narration"], voice_path)
        voice_dur = get_audio_duration(voice_path)

        # 4. Subtitles
        print("4. Generating subtitles...")
        sub_path = str(tmp / "subs.ass")
        generate_subtitles(script["narration"], voice_dur, sub_path)

        # 5. Video
        print(f"5. Fetching Pexels video: '{script['video_query']}'...")
        video_url_pex = get_pexels_video(script["video_query"])
        if not video_url_pex:
            print("  Trying fallback: 'emotional family moment'")
            video_url_pex = get_pexels_video("emotional family moment")
        if not video_url_pex:
            print("ERROR: No Pexels video found"); return

        video_path = str(tmp / "bg.mp4")
        download_file(video_url_pex, video_path)

        # 6. Music
        print("6. Getting background music...")
        music_path = get_background_music()

        # 7. Overlay (minimal — just title + branding)
        print("7. Creating overlay...")
        overlay = make_overlay(script)

        # 8. Assemble
        print("8. Assembling reel...")
        reel_path = str(tmp / "reel.mp4")
        assemble_reel(video_path, voice_path, overlay, reel_path, sub_path, music_path)

        # 9. Upload + post
        print("9. Uploading & posting...")
        public_url = upload_video(reel_path)
        send_to_make(public_url, script)

    print("\n=== Reel Bot done! ===\n")

if __name__ == "__main__":
    main()
