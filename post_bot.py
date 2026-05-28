#!/usr/bin/env python3
"""
post_bot.py  — Carousel edition (3 slides)
-------------------------------------------
Slide 1: Foto Unsplash + título  (hero)
Slide 2: Factos-chave            (facts card)
Slide 3: Impacto + CTA           (impact card)
"""

import io
import os
import json
import base64
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import anthropic
import requests
from PIL import Image, ImageDraw, ImageFont

# ─── Configuração ─────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "XJWduaYYGKbMDEYFMfKa9EaGxcLN-_KkOlGPahK5x5A")
IMGBB_API_KEY       = "44c06dbe6a657fdfd3ac4b3b97164db6"
WEBHOOK_URL         = "https://hook.eu1.make.com/u3ci4y1uxw85edmxrd6li2qdjwu6mnst"

LOG_FILE = "publications.json"

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("post_bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─── Palettes ─────────────────────────────────────────────────────────────────

BADGE_COLORS = {
    "Science":     (52,  152, 219),
    "Environment": (39,  174,  96),
    "Health":      (231,  76,  60),
    "Society":     (155,  89, 182),
    "Tech":        (243, 156,  18),
    "Animals":     ( 26, 188, 156),
    "default":     ( 52,  73,  94),
}

UNSPLASH_KEYWORDS = {
    "Science":     "science discovery research",
    "Environment": "nature forest earth",
    "Health":      "health wellness vitality",
    "Society":     "community people hope",
    "Tech":        "technology innovation future",
    "Animals":     "wildlife animals nature",
    "default":     "inspiration positive light",
}

GRADIENT_FALLBACK = {
    "Science":     ((8,  8,  45),   (20, 20, 110)),
    "Environment": ((5,  35, 12),   (12, 80,  32)),
    "Health":      ((35,  5, 35),   (90, 20,  90)),
    "Society":     ((8,  20, 45),   (25, 60, 110)),
    "Tech":        ((5,   5, 35),   (15, 15,  90)),
    "Animals":     ((35, 15,  5),   (90, 45,  12)),
    "default":     ((10, 10, 35),   (30, 30,  90)),
}

# ─── 1. Google News trending ──────────────────────────────────────────────────

def fetch_trending_news() -> list:
    url  = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    root  = ET.fromstring(resp.content)
    items = []
    for item in root.findall(".//item")[:20]:
        title = item.findtext("title", "").strip()
        if title:
            items.append(title)
    log.info(f"Google News: {len(items)} headlines")
    return items


# ─── 2. Claude gera conteúdo para 3 slides ────────────────────────────────────

def generate_post() -> dict:
    try:
        trending = fetch_trending_news()
    except Exception as e:
        log.warning(f"RSS falhou ({e})")
        trending = []

    news_section = ""
    if trending:
        news_section = (
            "Here are today's most viewed news headlines worldwide:\n\n"
            + "\n".join(f"{i+1}. {t}" for i, t in enumerate(trending))
            + "\n\nPick the ONE most positive, uplifting or inspiring story "
              "(or find a positive angle on one of them).\n\n"
        )

    prompt = (
        "You are a positive news editor for an Instagram account called Positive Pulse.\n\n"
        + news_section
        + "Create an Instagram post about a positive news story.\n\n"
        "IMPORTANT: Decide if this story deserves a CAROUSEL (3 slides) or a SINGLE photo post:\n"
        "- Use CAROUSEL when the story has multiple interesting facts, data, or angles to explore\n"
        "- Use SINGLE when it's a simple, feel-good story best told with one powerful image\n\n"
        "Return ONLY a valid JSON object with these exact fields:\n"
        "  post_type   : either 'carousel' or 'single'\n"
        "  headline    : punchy headline, max 8 words, ALL CAPS\n"
        "  subtitle    : one sentence expanding it, max 18 words\n"
        "  facts       : array of exactly 3 short facts (each max 12 words, start with an emoji) "
                        "— required even for single posts, leave as empty array [] if truly no facts\n"
        "  impact      : one powerful impact statement, max 15 words, ALL CAPS "
                        "(used in slide 3 of carousel, or ignored for single)\n"
        "  cta         : call-to-action, max 10 words (e.g. 'Follow for more good news every day!')\n"
        "  caption     : Instagram caption, 2-3 sentences, ends with call-to-action\n"
        "  hashtags    : array of 25 relevant hashtags (strings starting with #)\n"
        "  category    : one of [Science, Environment, Health, Society, Tech, Animals]\n"
        "  search_term : 2-3 english words to find a relevant photo\n\n"
        "No markdown, no extra text — only the JSON object."
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())
    log.info(f"Headline  : {data['headline']}")
    log.info(f"Category  : {data['category']}")
    log.info(f"Post type : {data.get('post_type', 'single')}")
    log.info(f"Facts     : {data.get('facts', [])}")
    return data


# ─── 3. Unsplash background ───────────────────────────────────────────────────

def _crop_center(img: Image.Image, size=(1080, 1080)) -> Image.Image:
    w, h  = img.size
    side  = min(w, h)
    left  = (w - side) // 2
    top   = (h - side) // 2
    return img.crop((left, top, left + side, top + side)).resize(size, Image.LANCZOS)


def fetch_background(post: dict) -> Image.Image | None:
    search   = post.get("search_term") or UNSPLASH_KEYWORDS.get(post.get("category", "default"), "nature")
    fallback = UNSPLASH_KEYWORDS.get(post.get("category", "default"), "nature").split()[0]

    if UNSPLASH_ACCESS_KEY:
        for query in [search, fallback]:
            try:
                resp = requests.get(
                    "https://api.unsplash.com/photos/random",
                    params={"query": query, "client_id": UNSPLASH_ACCESS_KEY},
                    timeout=15,
                )
                data = resp.json()
                if resp.ok and "urls" in data:
                    img_data = requests.get(data["urls"]["regular"], timeout=30).content
                    img = Image.open(io.BytesIO(img_data)).convert("RGB")
                    log.info(f"Unsplash OK (query: {query})")
                    return _crop_center(img)
            except Exception as e:
                log.warning(f"Unsplash '{query}': {type(e).__name__}")

    try:
        url  = f"https://source.unsplash.com/featured/1080x1080/?{search.split()[0]}"
        resp = requests.get(url, timeout=20, allow_redirects=True)
        if resp.ok and "image" in resp.headers.get("content-type", ""):
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            log.info("Unsplash source OK")
            return _crop_center(img)
    except Exception as e:
        log.warning(f"Unsplash source: {e}")

    log.warning("Sem foto — usando gradiente")
    return None


def _gradient_bg(category: str, size=(1080, 1080)) -> Image.Image:
    top_c, bot_c = GRADIENT_FALLBACK.get(category, GRADIENT_FALLBACK["default"])
    img  = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(size[1]):
        t = y / size[1]
        c = tuple(int(top_c[i] + (bot_c[i] - top_c[i]) * t) for i in range(3))
        draw.line([(0, y), (size[0], y)], fill=c)
    return img


# ─── 4. Fonts & helpers ───────────────────────────────────────────────────────

def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    bold_list = ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf",
                 "Ubuntu-Bold.ttf", "LiberationSans-Bold.ttf"]
    reg_list  = ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf",
                 "Ubuntu.ttf", "LiberationSans-Regular.ttf"]
    for name in (bold_list if bold else reg_list):
        try:
            return ImageFont.truetype(name, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default()


def _wrap(text: str, font, draw, max_w: int) -> list:
    words, lines, cur = text.split(), [], ""
    for word in words:
        test = f"{cur} {word}".strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _text_h(draw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


def _text_w(draw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


# ─── 5. SLIDE 1 — Hero (foto + título) ───────────────────────────────────────

def create_slide1(post: dict, bg: Image.Image | None) -> str:
    W, H     = 1080, 1080
    margin   = 60
    SPLIT    = 560
    category = post.get("category", "default")
    color    = BADGE_COLORS.get(category, BADGE_COLORS["default"])

    bg_img = bg or _gradient_bg(category)
    result = Image.new("RGB", (W, H), (12, 12, 18))

    # Foto com fade
    photo_area = bg_img.crop((0, 0, W, SPLIT)).convert("RGBA")
    fade = Image.new("RGBA", (W, SPLIT), (0, 0, 0, 0))
    fd   = ImageDraw.Draw(fade)
    for y in range(SPLIT - 150, SPLIT):
        t     = (y - (SPLIT - 150)) / 150
        alpha = int(t ** 0.6 * 255)
        fd.line([(0, y), (W, y)], fill=(12, 12, 18, alpha))
    photo_area.alpha_composite(fade)
    result.paste(photo_area.convert("RGB"), (0, 0))

    draw = ImageDraw.Draw(result)

    # Linha colorida
    draw.rectangle([(0, SPLIT - 4), (W, SPLIT + 4)], fill=color)

    # Badge categoria
    bf  = _font(28, bold=True)
    bb  = draw.textbbox((0, 0), category.upper(), font=bf)
    bw  = bb[2] - bb[0] + 30
    bh  = bb[3] - bb[1] + 22
    bx, by = margin, SPLIT - bh // 2 - 6

    result_rgba = result.convert("RGBA")
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld  = ImageDraw.Draw(lay)
    try:
        ld.rounded_rectangle([(bx, by), (bx + bw, by + bh)], radius=bh // 2, fill=(*color, 255))
    except AttributeError:
        ld.rectangle([(bx, by), (bx + bw, by + bh)], fill=(*color, 255))
    result_rgba.alpha_composite(lay)
    result = result_rgba.convert("RGB")
    draw   = ImageDraw.Draw(result)
    draw.text((bx + 15, by + 10), category.upper(), font=bf, fill=(255, 255, 255))

    # Brand topo direito
    ppf = _font(24, bold=True)
    ppw = _text_w(draw, "POSITIVE PULSE", ppf)
    draw.ellipse([(W - margin - ppw - 14, 46), (W - margin - ppw - 4, 56)], fill=color)
    draw.text((W - margin - ppw, 42), "POSITIVE PULSE", font=ppf, fill=(240, 240, 240))

    # Slide counter "1 / 3" topo esquerdo
    cf = _font(22, bold=False)
    draw.text((margin, 42), "1 / 3", font=cf, fill=(180, 180, 180))

    # Headline
    hf    = _font(80, bold=True)
    lines = _wrap(post["headline"].upper(), hf, draw, W - 2 * margin)
    y     = SPLIT + 38
    for line in lines[:3]:
        draw.text((margin, y), line, font=hf, fill=(255, 255, 255))
        y += _text_h(draw, line, hf) + 8

    # Accent line
    y += 14
    draw.rectangle([(margin, y), (margin + 60, y + 3)], fill=color)
    y += 22

    # Subtítulo
    sf        = _font(36, bold=False)
    sub_lines = _wrap(post.get("subtitle", ""), sf, draw, W - 2 * margin)
    for line in sub_lines[:2]:
        if y + _text_h(draw, line, sf) > H - 75:
            break
        draw.text((margin, y), line, font=sf, fill=(175, 178, 190))
        y += _text_h(draw, line, sf) + 6

    # Rodapé
    ff       = _font(24, bold=False)
    date_str = datetime.now().strftime("%B %d, %Y").upper()
    draw.text((margin, H - 52), date_str, font=ff, fill=(95, 95, 112))
    lf  = _font(24, bold=True)
    lw  = _text_w(draw, "POSITIVE PULSE", lf)
    draw.text((W - margin - lw, H - 52), "POSITIVE PULSE", font=lf, fill=color)

    path = "slide1.jpg"
    result.save(path, "JPEG", quality=95)
    log.info(f"Slide 1 criado -> {path}")
    return path


# ─── helpers ──────────────────────────────────────────────────────────────────

import re

def _strip_emoji(text: str) -> str:
    """Remove emojis que Pillow nao consegue renderizar."""
    return re.sub(r'[^\x00-\x7FÀ-ɏḀ-ỿ]+', '', text).strip()


def _dark_photo_bg(bg: Image.Image | None, category: str,
                   overlay_alpha: int = 210) -> Image.Image:
    """Foto com overlay muito escuro para slides 2 e 3."""
    W, H = 1080, 1080
    base = (bg or _gradient_bg(category)).copy()
    overlay = Image.new("RGBA", (W, H), (8, 8, 14, overlay_alpha))
    out = base.convert("RGBA")
    out.alpha_composite(overlay)
    return out.convert("RGB")


# ─── 6. SLIDE 2 — Key Facts (redesign) ───────────────────────────────────────

def create_slide2(post: dict, bg: Image.Image | None = None) -> str:
    W, H     = 1080, 1080
    margin   = 70
    category = post.get("category", "default")
    color    = BADGE_COLORS.get(category, BADGE_COLORS["default"])
    facts    = post.get("facts", ["No facts available"] * 3)

    img  = _dark_photo_bg(bg, category, overlay_alpha=215)
    draw = ImageDraw.Draw(img)

    # Barra colorida no topo
    draw.rectangle([(0, 0), (W, 6)], fill=color)

    # Slide counter
    cf = _font(22, bold=False)
    draw.text((margin, 32), "2 / 3", font=cf, fill=(160, 160, 175))

    # Brand topo direito
    ppf = _font(24, bold=True)
    ppw = _text_w(draw, "POSITIVE PULSE", ppf)
    draw.text((W - margin - ppw, 28), "POSITIVE PULSE", font=ppf, fill=color)

    # Secção "KEY FACTS" — centrada
    tf  = _font(34, bold=True)
    tw  = _text_w(draw, "KEY FACTS", tf)
    draw.text(((W - tw) // 2, 100), "KEY FACTS", font=tf, fill=color)
    lw2 = 80
    draw.rectangle([((W - lw2) // 2, 148), ((W + lw2) // 2, 151)], fill=color)

    # Headline
    hf    = _font(46, bold=True)
    lines = _wrap(post["headline"].upper(), hf, draw, W - 2 * margin)
    y     = 180
    for line in lines[:2]:
        draw.text((margin, y), line, font=hf, fill=(245, 245, 255))
        y += _text_h(draw, line, hf) + 8
    y += 36

    # 3 factos sem emoji — layout limpo com número colorido
    fact_font = _font(32, bold=False)
    num_font  = _font(36, bold=True)
    card_pad  = 22
    gap       = 24
    num_box   = 58

    for i, fact in enumerate(facts[:3]):
        clean = _strip_emoji(fact)
        if not clean:
            clean = fact  # fallback sem strip

        # Calcular altura do card
        fact_lines = _wrap(clean, fact_font, draw, W - 2 * margin - num_box - 28)
        card_h = max(num_box + 16, len(fact_lines) * (_text_h(draw, "A", fact_font) + 6) + 2 * card_pad)
        fx, fy = margin, y

        # Card background
        rgba = img.convert("RGBA")
        lay  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld   = ImageDraw.Draw(lay)
        try:
            ld.rounded_rectangle([(fx, fy), (W - margin, fy + card_h)],
                                  radius=14, fill=(*color, 22), outline=(*color, 90), width=2)
        except TypeError:
            ld.rectangle([(fx, fy), (W - margin, fy + card_h)], fill=(*color, 22))
        rgba.alpha_composite(lay)
        img  = rgba.convert("RGB")
        draw = ImageDraw.Draw(img)

        # Número em caixa colorida
        nx   = fx + card_pad
        ny   = fy + (card_h - num_box) // 2
        try:
            draw.rounded_rectangle([(nx, ny), (nx + num_box, ny + num_box)],
                                    radius=10, fill=color)
        except TypeError:
            draw.rectangle([(nx, ny), (nx + num_box, ny + num_box)], fill=color)
        num_str = str(i + 1)
        nw      = _text_w(draw, num_str, num_font)
        nh      = _text_h(draw, num_str, num_font)
        draw.text((nx + (num_box - nw) // 2, ny + (num_box - nh) // 2),
                  num_str, font=num_font, fill=(255, 255, 255))

        # Texto do facto
        tx = nx + num_box + 18
        ty = fy + card_pad
        for line in fact_lines[:3]:
            draw.text((tx, ty), line, font=fact_font, fill=(220, 220, 235))
            ty += _text_h(draw, line, fact_font) + 6

        y += card_h + gap

    # Rodapé
    ff  = _font(22, bold=False)
    ds  = datetime.now().strftime("%B %d, %Y").upper()
    draw.text((margin, H - 44), ds, font=ff, fill=(80, 80, 100))
    lf  = _font(22, bold=True)
    lw  = _text_w(draw, "POSITIVE PULSE", lf)
    draw.text((W - margin - lw, H - 44), "POSITIVE PULSE", font=lf, fill=color)
    draw.rectangle([(0, H - 6), (W, H)], fill=color)

    path = "slide2.jpg"
    img.save(path, "JPEG", quality=95)
    log.info(f"Slide 2 criado -> {path}")
    return path


# ─── 7. SLIDE 3 — Impact + CTA (redesign) ────────────────────────────────────

def create_slide3(post: dict, bg: Image.Image | None = None) -> str:
    W, H     = 1080, 1080
    margin   = 80
    category = post.get("category", "default")
    color    = BADGE_COLORS.get(category, BADGE_COLORS["default"])
    impact   = _strip_emoji(post.get("impact", post["headline"])) or post.get("impact", post["headline"])
    cta      = post.get("cta", "Follow for more good news every day!")

    img  = _dark_photo_bg(bg, category, overlay_alpha=200)
    draw = ImageDraw.Draw(img)

    # Barra colorida topo
    draw.rectangle([(0, 0), (W, 6)], fill=color)

    # Slide counter
    cf = _font(22, bold=False)
    draw.text((margin, 32), "3 / 3", font=cf, fill=(160, 160, 175))

    # Brand topo direito
    ppf = _font(24, bold=True)
    ppw = _text_w(draw, "POSITIVE PULSE", ppf)
    draw.text((W - margin - ppw, 28), "POSITIVE PULSE", font=ppf, fill=color)

    # Label "THE IMPACT" centrada
    tf  = _font(30, bold=True)
    lbl = "THE IMPACT"
    tw  = _text_w(draw, lbl, tf)
    draw.text(((W - tw) // 2, 108), lbl, font=tf, fill=color)
    draw.rectangle([((W - 60) // 2, 152), ((W + 60) // 2, 155)], fill=color)

    # Linha separadora horizontal
    draw.rectangle([(margin, 190), (W - margin, 192)], fill=(60, 60, 80))

    # Impact statement — grande, centrado, multi-linha
    if_font  = _font(72, bold=True)
    ilines   = _wrap(impact.upper(), if_font, draw, W - 2 * margin)
    line_h   = _text_h(draw, "A", if_font) + 16
    total_h  = line_h * min(len(ilines), 4)
    start_y  = 230 + (H - 230 - 300 - total_h) // 2   # centrar verticalmente

    for line in ilines[:4]:
        lw3 = _text_w(draw, line, if_font)
        # Sombra suave
        draw.text(((W - lw3) // 2 + 3, start_y + 3), line, font=if_font, fill=(0, 0, 0))
        draw.text(((W - lw3) // 2, start_y), line, font=if_font, fill=(255, 255, 255))
        start_y += line_h

    # Linha decorativa pós-impact
    sep_y = start_y + 30
    draw.rectangle([(margin + 80, sep_y), (W - margin - 80, sep_y + 3)], fill=color)

    # CTA
    ctaf = _font(34, bold=False)
    ctaw = _text_w(draw, cta, ctaf)
    if ctaw > W - 2 * margin:
        # quebrar em 2 linhas
        cta_lines = _wrap(cta, ctaf, draw, W - 2 * margin)
        cy = sep_y + 22
        for cl in cta_lines[:2]:
            clw = _text_w(draw, cl, ctaf)
            draw.text(((W - clw) // 2, cy), cl, font=ctaf, fill=(200, 205, 220))
            cy += _text_h(draw, cl, ctaf) + 6
        pill_y = cy + 30
    else:
        draw.text(((W - ctaw) // 2, sep_y + 22), cta, font=ctaf, fill=(200, 205, 220))
        pill_y = sep_y + 22 + _text_h(draw, cta, ctaf) + 40

    # Pílula POSITIVE PULSE
    pill_w, pill_h = 320, 68
    px = (W - pill_w) // 2
    py = min(pill_y, H - 160)

    rgba = img.convert("RGBA")
    lay  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld   = ImageDraw.Draw(lay)
    try:
        ld.rounded_rectangle([(px, py), (px + pill_w, py + pill_h)],
                              radius=34, fill=(*color, 230))
    except TypeError:
        ld.rectangle([(px, py), (px + pill_w, py + pill_h)], fill=(*color, 230))
    rgba.alpha_composite(lay)
    img  = rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    lf   = _font(28, bold=True)
    ltxt = "POSITIVE PULSE"
    ltw  = _text_w(draw, ltxt, lf)
    lth  = _text_h(draw, ltxt, lf)
    draw.text(((W - ltw) // 2, py + (pill_h - lth) // 2), ltxt, font=lf, fill=(255, 255, 255))

    # Rodapé
    ff  = _font(22, bold=False)
    ds  = datetime.now().strftime("%B %d, %Y").upper()
    draw.text((margin, H - 44), ds, font=ff, fill=(80, 80, 100))
    draw.rectangle([(0, H - 6), (W, H)], fill=color)

    path = "slide3.jpg"
    img.save(path, "JPEG", quality=95)
    log.info(f"Slide 3 criado -> {path}")
    return path


# ─── 8. Upload para imgbb ─────────────────────────────────────────────────────

def upload_image(path: str) -> str:
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": IMGBB_API_KEY, "image": b64},
        timeout=30,
    )
    resp.raise_for_status()
    url = resp.json()["data"]["url"]
    log.info(f"Upload OK -> {url}")
    return url


# ─── 9. Enviar carousel para o webhook Make.com ───────────────────────────────

def send_to_webhook(post: dict, image_urls: list):
    caption_full = f"{post['caption']}\n\n{' '.join(post['hashtags'])}"
    is_carousel = len(image_urls) > 1
    payload = {
        "image_urls": image_urls,
        "image_url":  image_urls[0],
        "caption":    caption_full,
        "headline":   post["headline"],
        "category":   post["category"],
        "timestamp":  datetime.now().isoformat(),
        "is_carousel": "true" if is_carousel else "false",   # string para Make.com
        "slide_count": len(image_urls),
    }
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    resp.raise_for_status()
    log.info(f"Webhook OK: {resp.status_code}")
    return payload


# ─── 10. Log local ────────────────────────────────────────────────────────────

def save_log(post: dict, image_urls: list):
    path    = Path(LOG_FILE)
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    history.append({
        "timestamp":  datetime.now().isoformat(),
        "headline":   post["headline"],
        "category":   post["category"],
        "image_urls": image_urls,
        "caption":    post["caption"],
        "hashtags":   post["hashtags"],
    })
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Log -> {LOG_FILE} ({len(history)} publicacoes)")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY nao definida.")
        raise SystemExit(1)

    log.info("=== post_bot a iniciar ===")

    log.info("Gerando conteudo com Claude...")
    post = generate_post()

    post_type = post.get("post_type", "single")
    log.info(f"Tipo de post: {post_type.upper()}")

    log.info("Buscando foto de fundo...")
    bg = fetch_background(post)

    if post_type == "carousel":
        log.info("Criando 3 slides (carousel)...")
        s1   = create_slide1(post, bg)
        s2   = create_slide2(post, bg)   # mesma foto de fundo
        s3   = create_slide3(post, bg)   # mesma foto de fundo
        slides = [s1, s2, s3]
    else:
        log.info("Criando 1 slide (post simples)...")
        s1   = create_slide1(post, bg)
        slides = [s1]

    log.info(f"Fazendo upload de {len(slides)} imagem(ns)...")
    urls = [upload_image(s) for s in slides]
    log.info(f"URLs: {urls}")

    log.info("Enviando para webhook Make.com...")
    try:
        send_to_webhook(post, urls)
    except Exception as e:
        log.warning(f"Webhook falhou: {e}")
        log.info(f"URLs disponiveis: {urls}")

    save_log(post, urls)
    log.info(f"=== Concluido! {len(slides)} slide(s) publicado(s) ===")


if __name__ == "__main__":
    main()
