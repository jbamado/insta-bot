#!/usr/bin/env python3
"""
post_bot.py
-----------
1. Usa Claude para gerar uma notícia positiva em inglês
2. Busca imagem real no Unsplash relacionada com a notícia
3. Cria imagem 1080x1080 estilo news viral do Instagram
4. Faz upload para imgbb
5. Envia image_url + caption para o webhook do Make.com
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

IMAGE_FILE = "post_image.jpg"
LOG_FILE   = "publications.json"

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

# ─── Palettes & keywords por categoria ───────────────────────────────────────

# Cor do badge (RGB)
BADGE_COLORS = {
    "Science":     (52,  152, 219),   # azul
    "Environment": (39,  174,  96),   # verde
    "Health":      (231,  76,  60),   # vermelho
    "Society":     (155,  89, 182),   # roxo
    "Tech":        (243, 156,  18),   # laranja
    "Animals":     ( 26, 188, 156),   # teal
    "default":     ( 52,  73,  94),   # escuro
}

# Palavras-chave para busca no Unsplash
UNSPLASH_KEYWORDS = {
    "Science":     "science discovery research",
    "Environment": "nature forest earth",
    "Health":      "health wellness vitality",
    "Society":     "community people hope",
    "Tech":        "technology innovation future",
    "Animals":     "wildlife animals nature",
    "default":     "inspiration positive light",
}

# Gradiente de fallback quando Unsplash falha
GRADIENT_FALLBACK = {
    "Science":     ((8, 8, 45),    (20, 20, 110)),
    "Environment": ((5, 35, 12),   (12, 80, 32)),
    "Health":      ((35, 5, 35),   (90, 20, 90)),
    "Society":     ((8, 20, 45),   (25, 60, 110)),
    "Tech":        ((5, 5, 35),    (15, 15, 90)),
    "Animals":     ((35, 15, 5),   (90, 45, 12)),
    "default":     ((10, 10, 35),  (30, 30, 90)),
}

# ─── 1. Buscar notícias trending do Google News ───────────────────────────────

def fetch_trending_news() -> list:
    """Devolve as top 20 notícias do Google News RSS."""
    url  = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    root  = ET.fromstring(resp.content)
    items = []
    for item in root.findall(".//item")[:20]:
        title = item.findtext("title", "").strip()
        if title:
            items.append(title)
    log.info(f"Google News: {len(items)} notícias encontradas")
    return items


# ─── 2. Gerar conteúdo com Claude ────────────────────────────────────────────

def generate_post() -> dict:
    # Buscar notícias reais; fallback silencioso se RSS falhar
    try:
        trending = fetch_trending_news()
    except Exception as e:
        log.warning(f"RSS falhou ({e}), a gerar notícia inventada")
        trending = []

    if trending:
        news_block = "\n".join(f"{i+1}. {t}" for i, t in enumerate(trending))
        prompt = (
            "You are a positive news editor for an Instagram account called Positive Pulse.\n\n"
            "Here are today's most viewed news headlines worldwide:\n\n"
            f"{news_block}\n\n"
            "Pick the ONE most positive, uplifting or inspiring story from this list "
            "(or find a positive angle on one of them). "
            "Then create a Positive Pulse Instagram post about it.\n\n"
            "Return ONLY a valid JSON object with these exact fields:\n"
            "  headline   : short punchy headline, max 8 words, ALL CAPS\n"
            "  subtitle   : one sentence expanding it, max 18 words\n"
            "  caption    : Instagram caption, 2-3 engaging sentences, ends with call-to-action\n"
            "  hashtags   : array of 25 relevant hashtags (strings starting with #)\n"
            "  category   : one of [Science, Environment, Health, Society, Tech, Animals]\n"
            "  search_term: 2-3 english words to find a relevant photo (e.g. 'ocean coral reef')\n\n"
            "No markdown, no extra text — only the JSON object."
        )
    else:
        prompt = (
            "You are a positive news editor for an Instagram account called Positive Pulse.\n\n"
            "Generate an uplifting, real-sounding positive news story.\n"
            "Return ONLY a valid JSON object with these exact fields:\n"
            "  headline   : short punchy headline, max 8 words, ALL CAPS\n"
            "  subtitle   : one sentence expanding it, max 18 words\n"
            "  caption    : Instagram caption, 2-3 engaging sentences, ends with call-to-action\n"
            "  hashtags   : array of 25 relevant hashtags (strings starting with #)\n"
            "  category   : one of [Science, Environment, Health, Society, Tech, Animals]\n"
            "  search_term: 2-3 english words to find a relevant photo (e.g. 'ocean coral reef')\n\n"
            "No markdown, no extra text — only the JSON object."
        )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())
    log.info(f"Headline    : {data['headline']}")
    log.info(f"Category    : {data['category']}")
    log.info(f"Search term : {data.get('search_term', '')}")
    return data


# ─── 3. Buscar imagem de fundo no Unsplash ────────────────────────────────────

def _crop_center(img: Image.Image, size=(1080, 1080)) -> Image.Image:
    """Cortar ao centro e redimensionar para quadrado."""
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top  = (h - side) // 2
    return img.crop((left, top, left + side, top + side)).resize(size, Image.LANCZOS)


def fetch_background(post: dict) -> Image.Image | None:
    """Tenta buscar foto relevante do Unsplash. Retorna None se falhar."""
    search = post.get("search_term") or UNSPLASH_KEYWORDS.get(post.get("category", "default"), "nature")

    # ── Método 1: Unsplash API oficial (com chave) ──
    if UNSPLASH_ACCESS_KEY:
        # Tenta query específica; se falhar (404), usa só a categoria
        category_fallback = UNSPLASH_KEYWORDS.get(post.get("category", "default"), "nature").split()[0]
        for query in [search, category_fallback]:
            try:
                resp = requests.get(
                    "https://api.unsplash.com/photos/random",
                    params={"query": query, "client_id": UNSPLASH_ACCESS_KEY},
                    timeout=15,
                )
                data = resp.json()
                if resp.ok and "urls" in data:
                    photo_url = data["urls"]["regular"]
                    img_data  = requests.get(photo_url, timeout=30).content
                    img = Image.open(io.BytesIO(img_data)).convert("RGB")
                    log.info(f"Foto Unsplash OK (query: {query})")
                    return _crop_center(img)
                else:
                    log.warning(f"Unsplash query '{query}' -> {resp.status_code}")
            except Exception as e:
                log.warning(f"Unsplash query '{query}': {type(e).__name__}")

    # ── Método 2: Unsplash source (sem chave) ──
    try:
        keyword = search.split()[0]
        url  = f"https://source.unsplash.com/featured/1080x1080/?{keyword}"
        resp = requests.get(url, timeout=20, allow_redirects=True)
        if resp.ok and "image" in resp.headers.get("content-type", ""):
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            log.info("Foto via Unsplash source")
            return _crop_center(img)
    except Exception as e:
        log.warning(f"Unsplash source: {e}")

    log.warning("Sem foto disponível — a usar gradiente")
    return None


def _gradient_bg(category: str) -> Image.Image:
    top_c, bot_c = GRADIENT_FALLBACK.get(category, GRADIENT_FALLBACK["default"])
    img  = Image.new("RGB", (1080, 1080))
    draw = ImageDraw.Draw(img)
    for y in range(1080):
        t = y / 1080
        c = tuple(int(top_c[i] + (bot_c[i] - top_c[i]) * t) for i in range(3))
        draw.line([(0, y), (1080, y)], fill=c)
    return img


# ─── 3. Criar imagem — Magazine Split ────────────────────────────────────────

def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    bold_fonts = ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf",
                  "Ubuntu-Bold.ttf", "LiberationSans-Bold.ttf"]
    reg_fonts  = ["arial.ttf",   "Arial.ttf",      "DejaVuSans.ttf",
                  "Ubuntu.ttf",  "LiberationSans-Regular.ttf"]
    for name in (bold_fonts if bold else reg_fonts):
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


def create_image(post: dict) -> str:
    W, H     = 1080, 1080
    margin   = 60
    SPLIT    = 560
    category = post.get("category", "default")
    color    = BADGE_COLORS.get(category, BADGE_COLORS["default"])

    # ── 1. Fundo + painel escuro base ────────────────────────────────────────
    bg     = fetch_background(post) or _gradient_bg(category)
    result = Image.new("RGB", (W, H), (12, 12, 18))

    # ── 2. Foto no topo com fade suave para o painel ──────────────────────────
    photo_area = bg.crop((0, 0, W, SPLIT)).convert("RGBA")
    fade = Image.new("RGBA", (W, SPLIT), (0, 0, 0, 0))
    fd   = ImageDraw.Draw(fade)
    for y in range(SPLIT - 150, SPLIT):
        t     = (y - (SPLIT - 150)) / 150
        alpha = int(t ** 0.6 * 255)
        fd.line([(0, y), (W, y)], fill=(12, 12, 18, alpha))
    photo_area.alpha_composite(fade)
    result.paste(photo_area.convert("RGB"), (0, 0))

    draw = ImageDraw.Draw(result)

    # ── 3. Linha colorida de separação ────────────────────────────────────────
    draw.rectangle([(0, SPLIT - 4), (W, SPLIT + 4)], fill=color)

    # ── 4. Badge categoria a sobrepor a linha ─────────────────────────────────
    bf  = _font(28, bold=True)
    bb  = draw.textbbox((0, 0), category.upper(), font=bf)
    bw  = bb[2] - bb[0] + 30
    bh  = bb[3] - bb[1] + 22
    bx  = margin
    by  = SPLIT - bh // 2 - 6

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

    # ── 5. Brand no topo direito da foto ──────────────────────────────────────
    ppf   = _font(24, bold=True)
    ppw   = draw.textbbox((0, 0), "POSITIVE PULSE", font=ppf)[2]
    dot_x = W - margin - ppw - 14
    draw.ellipse([(dot_x, 46), (dot_x + 10, 56)], fill=color)
    draw.text((W - margin - ppw, 42), "POSITIVE PULSE", font=ppf, fill=(240, 240, 240))

    # ── 6. Headline no painel ─────────────────────────────────────────────────
    hf    = _font(80, bold=True)
    lines = _wrap(post["headline"].upper(), hf, draw, W - 2 * margin)
    y     = SPLIT + 38
    for line in lines[:3]:
        draw.text((margin, y), line, font=hf, fill=(255, 255, 255))
        y += _text_h(draw, line, hf) + 8

    # ── 7. Linha accent ───────────────────────────────────────────────────────
    y += 14
    draw.rectangle([(margin, y), (margin + 60, y + 3)], fill=color)
    y += 22

    # ── 8. Subtítulo ──────────────────────────────────────────────────────────
    sf        = _font(36, bold=False)
    sub_lines = _wrap(post.get("subtitle", ""), sf, draw, W - 2 * margin)
    for line in sub_lines[:2]:
        if y + _text_h(draw, line, sf) > H - 75:
            break
        draw.text((margin, y), line, font=sf, fill=(175, 178, 190))
        y += _text_h(draw, line, sf) + 6

    # ── 9. Rodapé ─────────────────────────────────────────────────────────────
    ff       = _font(24, bold=False)
    date_str = datetime.now().strftime("%B %d, %Y").upper()
    draw.text((margin, H - 52), date_str, font=ff, fill=(95, 95, 112))
    lf  = _font(24, bold=True)
    lw  = draw.textbbox((0, 0), "POSITIVE PULSE", font=lf)[2]
    draw.text((W - margin - lw, H - 52), "POSITIVE PULSE", font=lf, fill=color)

    result.save(IMAGE_FILE, "JPEG", quality=95)
    log.info(f"Imagem criada → {IMAGE_FILE}")
    return IMAGE_FILE


# ─── 4. Upload para imgbb ─────────────────────────────────────────────────────

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
    log.info(f"Upload OK → {url}")
    return url


# ─── 5. Enviar para o webhook Make.com ───────────────────────────────────────

def send_to_webhook(post: dict, image_url: str):
    caption_full = f"{post['caption']}\n\n{' '.join(post['hashtags'])}"
    payload = {
        "image_url": image_url,
        "caption":   caption_full,
        "headline":  post["headline"],
        "category":  post["category"],
        "timestamp": datetime.now().isoformat(),
    }
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    resp.raise_for_status()
    log.info(f"Webhook OK: {resp.status_code}")
    return payload


# ─── 6. Log local ─────────────────────────────────────────────────────────────

def save_log(post: dict, image_url: str):
    path    = Path(LOG_FILE)
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    history.append({
        "timestamp": datetime.now().isoformat(),
        "headline":  post["headline"],
        "category":  post["category"],
        "image_url": image_url,
        "caption":   post["caption"],
        "hashtags":  post["hashtags"],
    })
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Log → {LOG_FILE} ({len(history)} publicações)")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY não definida.")
        raise SystemExit(1)

    log.info("═══ post_bot a iniciar ═══")

    log.info("Gerando conteúdo com Claude...")
    post = generate_post()

    log.info("Criando imagem...")
    image_path = create_image(post)

    log.info("Fazendo upload para imgbb...")
    image_url = upload_image(image_path)

    log.info("Enviando para webhook Make.com...")
    try:
        send_to_webhook(post, image_url)
    except Exception as e:
        log.warning(f"Webhook falhou (Make.com inativo?): {e}")
        log.info(f"Imagem disponível em: {image_url}")

    save_log(post, image_url)
    log.info("═══ Concluído! ═══")


if __name__ == "__main__":
    main()
