#!/usr/bin/env python3
"""
post_bot.py  — @wealth style carousel
--------------------------------------
Slide 1 : Foto principal + headline dourada enorme
Slides 2+: Uma foto por item + nome dourado + detalhe branco
"""

import io, os, re, json, base64, logging, xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import anthropic, requests
from PIL import Image, ImageDraw, ImageFont

# ─── Config ───────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "XJWduaYYGKbMDEYFMfKa9EaGxcLN-_KkOlGPahK5x5A")
IMGBB_API_KEY       = "44c06dbe6a657fdfd3ac4b3b97164db6"
WEBHOOK_URL         = "https://hook.eu1.make.com/u3ci4y1uxw85edmxrd6li2qdjwu6mnst"
REEL_WEBHOOK_URL    = os.getenv("MAKE_REEL_WEBHOOK", "")
LOG_FILE            = "publications.json"

GOLD  = (255, 200,   0)
WHITE = (255, 255, 255)
BLACK = (  0,   0,   0)

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

# ─── Fonts ────────────────────────────────────────────────────────────────────

def _impact(size: int) -> ImageFont.FreeTypeFont:
    """Tenta carregar Impact (ideal) ou fallback bold."""
    for path in ["C:/Windows/Fonts/impact.ttf", "impact.ttf",
                 "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"]:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default()

def _regular(size: int) -> ImageFont.FreeTypeFont:
    for path in ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "Ubuntu.ttf"]:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default()

# ─── Text helpers ──────────────────────────────────────────────────────────────

def _tw(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]

def _th(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]

def _wrap(text, font, draw, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def _shadow_text(draw, x, y, text, font, fill=GOLD, shadow=4):
    """Texto com sombra preta para contraste máximo."""
    for dx in range(-shadow, shadow + 1):
        for dy in range(-shadow, shadow + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=BLACK)
    draw.text((x, y), text, font=font, fill=fill)

def _centered_shadow(draw, y, text, font, fill=GOLD, shadow=4, W=1080):
    w = _tw(draw, text, font)
    _shadow_text(draw, (W - w) // 2, y, text, font, fill, shadow)
    return _th(draw, text, font)

# ─── Bottom gradient overlay ───────────────────────────────────────────────────

def _apply_bottom_gradient(img: Image.Image, start_pct=0.42) -> Image.Image:
    """Gradiente preto de baixo para cima — deixa a foto visível no topo."""
    W, H = img.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    start_y = int(H * start_pct)
    for y in range(start_y, H):
        t = (y - start_y) / (H - start_y)
        alpha = int(min(255, t ** 0.55 * 290))
        od.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    out = img.convert("RGBA")
    out.alpha_composite(overlay)
    return out.convert("RGB")

# ─── Brand bar ────────────────────────────────────────────────────────────────

def _draw_brand(draw, y, W=1080, margin=60):
    """● POSITIVE PULSE ● com linhas douradas — estilo @wealth."""
    bf   = _regular(26)
    txt  = "POSITIVE PULSE"
    tw   = _tw(draw, txt, bf)
    cx   = W // 2

    # Dot + text + dot
    dot  = "●"
    dw   = _tw(draw, dot, bf)
    full = f"{dot}  {txt}  {dot}"
    fw   = _tw(draw, full, bf)

    # Linhas de cada lado
    line_end   = cx - fw // 2 - 18
    line_start = margin
    if line_end > line_start:
        draw.rectangle([(line_start, y + 10), (line_end, y + 12)], fill=GOLD)
    draw.text((cx - fw // 2, y), full, font=bf, fill=GOLD)
    line2_start = cx + fw // 2 + 18
    if line2_start < W - margin:
        draw.rectangle([(line2_start, y + 10), (W - margin, y + 12)], fill=GOLD)

# ─── Unsplash ─────────────────────────────────────────────────────────────────

def _crop_square(img: Image.Image, size=1080) -> Image.Image:
    w, h = img.size
    s    = min(w, h)
    l    = (w - s) // 2
    t    = (h - s) // 2
    return img.crop((l, t, l + s, t + s)).resize((size, size), Image.LANCZOS)

def fetch_photo(search_term: str) -> Image.Image | None:
    if UNSPLASH_ACCESS_KEY:
        for query in [search_term, search_term.split()[0]]:
            try:
                r = requests.get(
                    "https://api.unsplash.com/photos/random",
                    params={"query": query, "client_id": UNSPLASH_ACCESS_KEY},
                    timeout=15,
                )
                d = r.json()
                if r.ok and "urls" in d:
                    img_data = requests.get(d["urls"]["regular"], timeout=30).content
                    log.info(f"Unsplash OK: {query}")
                    return _crop_square(Image.open(io.BytesIO(img_data)).convert("RGB"))
            except Exception as e:
                log.warning(f"Unsplash '{query}': {e}")
    # fallback gradiente
    img  = Image.new("RGB", (1080, 1080), (15, 15, 25))
    draw = ImageDraw.Draw(img)
    for y in range(1080):
        t = y / 1080
        draw.line([(0, y), (1080, y)], fill=(int(10 + 20*t), int(10 + 15*t), int(25 + 35*t)))
    return img

# ─── 1. Google News ───────────────────────────────────────────────────────────

def fetch_trending() -> list:
    try:
        r = requests.get("https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
                         timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(r.content)
        items = [i.findtext("title", "").strip() for i in root.findall(".//item")[:20]]
        log.info(f"Google News: {len(items)} headlines")
        return [i for i in items if i]
    except Exception as e:
        log.warning(f"RSS falhou: {e}")
        return []

# ─── 2. Claude — gera lista de itens ──────────────────────────────────────────

def generate_post() -> dict:
    trending = fetch_trending()
    news_block = ""
    if trending:
        news_block = (
            "Today's trending headlines:\n"
            + "\n".join(f"{i+1}. {t}" for i, t in enumerate(trending))
            + "\n\n"
        )

    prompt = (
        "You create viral Instagram carousel posts for Positive Pulse — a positive news account.\n\n"
        + news_block
        + "Create a LIST-based carousel post in the style of @wealth on Instagram.\n"
        "Format: a ranked or themed list (e.g. 'TOP 5 SCIENTISTS CHANGING THE WORLD', "
        "'THE MOST INSPIRING COUNTRIES RIGHT NOW', 'DISCOVERIES THAT WILL CHANGE YOUR LIFE').\n\n"
        "Rules:\n"
        "- Topic MUST be positive, uplifting, inspiring or fascinating\n"
        "- List MUST have exactly 5 items\n"
        "- Each item needs its own Unsplash photo search term\n"
        "- Headlines and names must be SHORT and PUNCHY (max 5 words each)\n"
        "- Names should be ALL CAPS\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "topic": "THE MOST INSPIRING SCIENTISTS ALIVE TODAY",\n'
        '  "search_term": "science laboratory discovery",\n'
        '  "items": [\n'
        '    {"name": "JANE GOODALL", "detail": "50 YEARS SAVING WILDLIFE", "search": "wildlife chimpanzee jungle africa"},\n'
        '    {"name": "ELON MUSK", "detail": "MAKING HUMANS MULTIPLANETARY", "search": "rocket space launch"},\n'
        '    {"name": "KATALIN KARIKO", "detail": "MRNA SAVES MILLIONS OF LIVES", "search": "medical laboratory vaccine"},\n'
        '    {"name": "DEMIS HASSABIS", "detail": "AI SOLVES 50-YEAR OLD PROBLEM", "search": "artificial intelligence research"},\n'
        '    {"name": "WANGARI MAATHAI", "detail": "PLANTED 50 MILLION TREES", "search": "trees forest reforestation africa"}\n'
        "  ],\n"
        '  "caption": "Instagram caption 2-3 sentences with call to action",\n'
        '  "hashtags": ["#positivenews", "#inspiring", ...] (25 hashtags)\n'
        "}\n\n"
        "No markdown, no extra text — only the JSON."
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())
    log.info(f"Topic  : {data['topic']}")
    log.info(f"Items  : {[i['name'] for i in data['items']]}")
    return data

# ─── 3. SLIDE 1 — Hero (foto + headline) ──────────────────────────────────────

def create_slide1(post: dict, bg: Image.Image) -> str:
    W = H = 1080
    margin = 55

    img  = _apply_bottom_gradient(bg, start_pct=0.38)
    draw = ImageDraw.Draw(img)

    # ── Headline dourada em Impact ──────────────────────────────────────────
    topic = post["topic"].upper()
    # Tamanho adaptativo
    for size in [105, 90, 78, 66, 56]:
        f     = _impact(size)
        lines = _wrap(topic, f, draw, W - 2 * margin)
        if len(lines) <= 3:
            break

    line_h = _th(draw, "A", f) + 10
    total  = len(lines) * line_h
    y      = H - total - 95   # posição do texto (acima do brand)

    for line in lines:
        _centered_shadow(draw, y, line, f, fill=GOLD, shadow=5, W=W)
        y += line_h

    # ── Brand ──────────────────────────────────────────────────────────────
    _draw_brand(draw, H - 68, W=W, margin=margin)

    # ── Slide counter ──────────────────────────────────────────────────────
    cf = _regular(24)
    draw.text((margin, 36), f"1 / {len(post['items']) + 1}", font=cf, fill=(200, 200, 200))

    path = "slide1.jpg"
    img.save(path, "JPEG", quality=95)
    log.info("Slide 1 criado")
    return path

# ─── 4. SLIDE ITEM — foto + nome dourado + detalhe ────────────────────────────

def create_item_slide(item: dict, idx: int, total: int, bg: Image.Image) -> str:
    W = H = 1080
    margin = 55

    img  = _apply_bottom_gradient(bg, start_pct=0.45)
    draw = ImageDraw.Draw(img)

    # ── Nome (grande, dourado) ──────────────────────────────────────────────
    name = item["name"].upper()
    for size in [115, 98, 84, 70, 58]:
        nf    = _impact(size)
        lines = _wrap(name, nf, draw, W - 2 * margin)
        if len(lines) <= 2:
            break

    # Tamanho adaptativo para o detalhe — nunca corta nas margens
    detail_text = item["detail"].upper()
    for size in [62, 52, 44, 36, 30]:
        detail_f = _impact(size)
        if _tw(draw, detail_text, detail_f) <= W - 2 * margin:
            break

    detail_h  = _th(draw, "A", detail_f) + 8
    name_h    = _th(draw, "A", nf) + 10
    total_h   = len(lines) * name_h + detail_h + 16

    y = H - total_h - 72   # espaço para brand

    for line in lines:
        _centered_shadow(draw, y, line, nf, fill=GOLD, shadow=5, W=W)
        y += name_h

    # ── Detalhe (branco, menor) ─────────────────────────────────────────────
    y += 8
    _centered_shadow(draw, y, detail_text, detail_f,
                     fill=WHITE, shadow=4, W=W)

    # ── Brand ──────────────────────────────────────────────────────────────
    _draw_brand(draw, H - 58, W=W, margin=margin)

    # ── Slide counter ──────────────────────────────────────────────────────
    cf = _regular(24)
    draw.text((margin, 36), f"{idx + 2} / {total + 1}", font=cf, fill=(200, 200, 200))

    path = f"slide{idx + 2}.jpg"
    img.save(path, "JPEG", quality=95)
    log.info(f"Slide {idx + 2} criado: {item['name']}")
    return path

# ─── 5. Upload imgbb ──────────────────────────────────────────────────────────

def upload_image(path: str) -> str:
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    r = requests.post("https://api.imgbb.com/1/upload",
                      data={"key": IMGBB_API_KEY, "image": b64}, timeout=30)
    r.raise_for_status()
    url = r.json()["data"]["url"]
    log.info(f"Upload OK -> {url}")
    return url

# ─── 6. Webhook Make.com ──────────────────────────────────────────────────────

def send_to_webhook(post: dict, image_urls: list):
    caption = f"{post['caption']}\n\n{' '.join(post['hashtags'])}"
    is_carousel = len(image_urls) > 1
    payload = {
        "image_urls":  image_urls,
        "image_url":   image_urls[0],
        "caption":     caption,
        "headline":    post["topic"],
        "category":    "Positive",
        "timestamp":   datetime.now().isoformat(),
        "is_carousel": "true" if is_carousel else "false",
        "slide_count": len(image_urls),
    }
    r = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    r.raise_for_status()
    log.info(f"Webhook OK: {r.status_code}")

# ─── 7. Log ───────────────────────────────────────────────────────────────────

def save_log(post: dict, urls: list):
    path    = Path(LOG_FILE)
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    history.append({
        "timestamp": datetime.now().isoformat(),
        "topic":     post["topic"],
        "items":     [i["name"] for i in post["items"]],
        "urls":      urls,
    })
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Log -> {len(history)} publicacoes")

# ─── 8. Gerar imagem Reel (9:16) com a foto do hero ──────────────────────────

def create_reel_image(post: dict, bg: Image.Image) -> str:
    """Gera imagem 1080x1920 para Reel reutilizando a foto do slide 1."""
    RW, RH = 1080, 1920
    margin = 70

    # Crop centrado para 9:16
    src_w, src_h = bg.size
    if src_w / src_h > RW / RH:
        new_h, new_w = RH, int(src_w * RH / src_h)
    else:
        new_w, new_h = RW, int(src_h * RW / src_w)
    img = bg.resize((new_w, new_h), Image.LANCZOS)
    left, top = (new_w - RW) // 2, (new_h - RH) // 2
    img = img.crop((left, top, left + RW, top + RH))

    # Gradiente escuro na metade inferior
    overlay = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    start_y = int(RH * 0.42)
    for y in range(start_y, RH):
        t = (y - start_y) / (RH - start_y)
        od.line([(0, y), (RW, y)], fill=(0, 0, 0, int(210 * (t ** 0.6))))
    img = img.convert("RGBA")
    img.alpha_composite(overlay)
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)

    # Título dourado (topic) — tamanho adaptativo
    topic = post["topic"].upper()
    for size in [115, 98, 84, 70, 58]:
        f_title = _impact(size)
        lines = _wrap(topic, f_title, draw, RW - 2 * margin)
        if len(lines) <= 3:
            break

    line_h = draw.textbbox((0, 0), "A", font=f_title)[3] + 14
    brand_y = RH - 105

    # Subtítulo = primeiro item da lista
    f_sub = _impact(52)
    first_item = post["items"][0]["name"].upper() if post.get("items") else ""
    sub_lines = _wrap(first_item, f_sub, draw, RW - 2 * margin) if first_item else []
    sub_h = len(sub_lines) * (draw.textbbox((0, 0), "A", font=f_sub)[3] + 10) + 28 if sub_lines else 0

    total_h = len(lines) * line_h + sub_h
    y = brand_y - total_h - 60

    for line in lines:
        tw = draw.textbbox((0, 0), line, font=f_title)[2]
        _centered_shadow(draw, y, line, f_title, fill=GOLD, shadow=6, W=RW)
        y += line_h

    if sub_lines:
        y += 16
        for line in sub_lines:
            _centered_shadow(draw, y, line, f_sub, fill=WHITE, shadow=4, W=RW)
            y += draw.textbbox((0, 0), "A", font=f_sub)[3] + 10

    # Brand bar
    bf = _regular(30)
    full = "● POSITIVE PULSE ●"
    tw = draw.textbbox((0, 0), full, font=bf)[2]
    draw.rectangle([(margin, brand_y - 18), (RW - margin, brand_y - 16)], fill=GOLD)
    _centered_shadow(draw, brand_y, full, bf, fill=GOLD, shadow=3, W=RW)

    path = "reel_image.jpg"
    img.save(path, "JPEG", quality=95)
    log.info(f"Reel image saved -> {path}")
    return path


def send_reel_to_webhook(post: dict, image_url: str):
    if not REEL_WEBHOOK_URL:
        log.info("MAKE_REEL_WEBHOOK nao definido — reel nao enviado")
        return
    caption = f"{post['caption']}\n\n{' '.join(post['hashtags'])}"
    r = requests.post(REEL_WEBHOOK_URL, json={"video_url": image_url, "caption": caption}, timeout=30)
    r.raise_for_status()
    log.info(f"Reel webhook OK: {r.status_code}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY nao definida.")
        raise SystemExit(1)

    log.info("=== post_bot @wealth style ===")

    log.info("Gerando conteudo com Claude...")
    post = generate_post()

    items = post["items"]
    slides_paths = []

    # Slide 1 — foto do tema geral
    log.info("Buscando foto para slide 1...")
    bg1 = fetch_photo(post.get("search_term", "inspiring news world"))
    slides_paths.append(create_slide1(post, bg1))

    # Slides 2-6 — uma foto por item
    bg_first_item = None
    for i, item in enumerate(items):
        log.info(f"Buscando foto para item {i+1}: {item['name']}...")
        bg = fetch_photo(item["search"])
        if i == 0:
            bg_first_item = bg  # guarda foto do 1º item para o Reel
        slides_paths.append(create_item_slide(item, i, len(items), bg))

    # Gerar imagem Reel com a foto + conteúdo do 1º item (slide 2)
    log.info("Gerando imagem Reel 9:16...")
    reel_post = {
        "topic": items[0]["name"],
        "items": [{"name": items[0]["detail"]}],
        "caption": post["caption"],
        "hashtags": post["hashtags"],
    }
    reel_path = create_reel_image(reel_post, bg_first_item)

    log.info(f"Fazendo upload de {len(slides_paths)} slides + reel...")
    urls = [upload_image(p) for p in slides_paths]
    reel_url = upload_image(reel_path)

    log.info("Enviando carousel para Make.com...")
    try:
        send_to_webhook(post, urls)
    except Exception as e:
        log.warning(f"Carousel webhook falhou: {e}")
        log.info(f"URLs: {urls}")

    log.info("Enviando reel para Make.com...")
    try:
        send_reel_to_webhook(post, reel_url)
    except Exception as e:
        log.warning(f"Reel webhook falhou: {e}")

    save_log(post, urls)
    log.info(f"=== Concluido! {len(slides_paths)} slides + 1 reel ===")

if __name__ == "__main__":
    main()
