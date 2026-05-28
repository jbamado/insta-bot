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


# ─── 6. SLIDE 2 — Key Facts ───────────────────────────────────────────────────

def create_slide2(post: dict) -> str:
    W, H     = 1080, 1080
    margin   = 70
    category = post.get("category", "default")
    color    = BADGE_COLORS.get(category, BADGE_COLORS["default"])
    facts    = post.get("facts", ["No facts available"] * 3)

    # Fundo muito escuro com gradiente subtil
    img = Image.new("RGB", (W, H), (10, 10, 16))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        r = int(10 + 8 * t)
        g = int(10 + 8 * t)
        b = int(16 + 14 * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    draw = ImageDraw.Draw(img)

    # Barra colorida no topo
    draw.rectangle([(0, 0), (W, 8)], fill=color)

    # Slide counter "2 / 3"
    cf = _font(22, bold=False)
    draw.text((margin, 35), "2 / 3", font=cf, fill=(180, 180, 180))

    # Brand topo direito
    ppf = _font(24, bold=True)
    ppw = _text_w(draw, "POSITIVE PULSE", ppf)
    draw.text((W - margin - ppw, 31), "POSITIVE PULSE", font=ppf, fill=color)

    # Título da secção
    tf    = _font(38, bold=True)
    title = "KEY FACTS"
    tw    = _text_w(draw, title, tf)
    draw.text(((W - tw) // 2, 110), title, font=tf, fill=color)

    # Linha decorativa sob o título
    lw2 = 120
    draw.rectangle([((W - lw2) // 2, 165), ((W + lw2) // 2, 168)], fill=color)

    # Headline pequena
    hf    = _font(44, bold=True)
    lines = _wrap(post["headline"].upper(), hf, draw, W - 2 * margin)
    y     = 205
    for line in lines[:2]:
        draw.text((margin, y), line, font=hf, fill=(230, 230, 240))
        y += _text_h(draw, line, hf) + 6
    y += 40

    # 3 Facts com card background cada um
    fact_font = _font(34, bold=False)
    card_h    = 130
    gap       = 28

    for i, fact in enumerate(facts[:3]):
        fx = margin
        fy = y + i * (card_h + gap)

        # Card semi-transparente
        img_rgba = img.convert("RGBA")
        card_lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cd       = ImageDraw.Draw(card_lay)
        try:
            cd.rounded_rectangle(
                [(fx, fy), (W - margin, fy + card_h)],
                radius=16,
                fill=(*color, 28),
                outline=(*color, 80),
                width=2,
            )
        except TypeError:
            cd.rectangle([(fx, fy), (W - margin, fy + card_h)], fill=(*color, 28))
        img_rgba.alpha_composite(card_lay)
        img  = img_rgba.convert("RGB")
        draw = ImageDraw.Draw(img)

        # Número do facto
        nf = _font(36, bold=True)
        draw.text((fx + 20, fy + 22), str(i + 1), font=nf, fill=color)

        # Texto do facto
        fact_lines = _wrap(fact, fact_font, draw, W - 2 * margin - 70)
        ty = fy + 18
        for line in fact_lines[:2]:
            draw.text((fx + 65, ty), line, font=fact_font, fill=(215, 215, 225))
            ty += _text_h(draw, line, fact_font) + 4

    # Rodapé
    ff  = _font(22, bold=False)
    ds  = datetime.now().strftime("%B %d, %Y").upper()
    draw.text((margin, H - 45), ds, font=ff, fill=(70, 70, 85))
    draw.rectangle([(0, H - 8), (W, H)], fill=color)

    path = "slide2.jpg"
    img.save(path, "JPEG", quality=95)
    log.info(f"Slide 2 criado -> {path}")
    return path


# ─── 7. SLIDE 3 — Impact + CTA ────────────────────────────────────────────────

def create_slide3(post: dict) -> str:
    W, H     = 1080, 1080
    margin   = 80
    category = post.get("category", "default")
    color    = BADGE_COLORS.get(category, BADGE_COLORS["default"])
    impact   = post.get("impact", post["headline"])
    cta      = post.get("cta", "Follow for more good news every day!")

    top_c, bot_c = GRADIENT_FALLBACK.get(category, GRADIENT_FALLBACK["default"])
    # Tornar o gradiente um pouco mais escuro para contraste
    top_c = tuple(max(0, c - 5) for c in top_c)
    bot_c = tuple(min(255, c + 20) for c in bot_c)

    img  = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        c = tuple(int(top_c[i] + (bot_c[i] - top_c[i]) * t) for i in range(3))
        draw.line([(0, y), (W, y)], fill=c)

    draw = ImageDraw.Draw(img)

    # Barra colorida topo
    draw.rectangle([(0, 0), (W, 8)], fill=color)

    # Slide counter "3 / 3"
    cf = _font(22, bold=False)
    draw.text((margin, 35), "3 / 3", font=cf, fill=(180, 180, 180))

    # Brand topo direito
    ppf = _font(24, bold=True)
    ppw = _text_w(draw, "POSITIVE PULSE", ppf)
    draw.text((W - margin - ppw, 31), "POSITIVE PULSE", font=ppf, fill=color)

    # Aspas decorativas grandes
    qf = _font(180, bold=True)
    draw.text((margin - 15, 80), "“", font=qf, fill=(*color, 60) if False else color)

    # Impact statement (grande, centrado)
    if_size = 68
    ifont   = _font(if_size, bold=True)
    ilines  = _wrap(impact.upper(), ifont, draw, W - 2 * margin)
    # Total height do bloco de texto
    line_h  = _text_h(draw, "A", ifont) + 14
    total_h = line_h * len(ilines[:4])
    start_y = (H - total_h) // 2 - 60

    for line in ilines[:4]:
        lw3 = _text_w(draw, line, ifont)
        draw.text(((W - lw3) // 2, start_y), line, font=ifont, fill=(255, 255, 255))
        start_y += line_h

    # Linha decorativa
    y_line = start_y + 30
    draw.rectangle([((W - 100) // 2, y_line), ((W + 100) // 2, y_line + 4)], fill=color)

    # CTA
    ctaf  = _font(32, bold=False)
    ctaw  = _text_w(draw, cta, ctaf)
    draw.text(((W - ctaw) // 2, y_line + 28), cta, font=ctaf, fill=(200, 200, 215))

    # Quadrado com logo no centro em baixo
    box_w, box_h = 340, 80
    bx = (W - box_w) // 2
    by = H - 145

    img_rgba = img.convert("RGBA")
    box_lay  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd       = ImageDraw.Draw(box_lay)
    try:
        bd.rounded_rectangle([(bx, by), (bx + box_w, by + box_h)],
                              radius=40, fill=(*color, 220))
    except TypeError:
        bd.rectangle([(bx, by), (bx + box_w, by + box_h)], fill=(*color, 220))
    img_rgba.alpha_composite(box_lay)
    img  = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    lf  = _font(30, bold=True)
    ltxt = "POSITIVE PULSE"
    ltw  = _text_w(draw, ltxt, lf)
    draw.text(((W - ltw) // 2, by + 24), ltxt, font=lf, fill=(255, 255, 255))

    # Rodapé
    ff  = _font(22, bold=False)
    ds  = datetime.now().strftime("%B %d, %Y").upper()
    draw.text((margin, H - 38), ds, font=ff, fill=(100, 100, 115))
    draw.rectangle([(0, H - 8), (W, H)], fill=color)

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
    payload = {
        "image_urls": image_urls,          # lista com 3 URLs
        "image_url":  image_urls[0],       # compatibilidade com cenário antigo
        "caption":    caption_full,
        "headline":   post["headline"],
        "category":   post["category"],
        "timestamp":  datetime.now().isoformat(),
        "is_carousel": True,
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
        s2   = create_slide2(post)
        s3   = create_slide3(post)
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
