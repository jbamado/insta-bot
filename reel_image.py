#!/usr/bin/env python3
"""
reel_image.py — Gerador de imagens 9:16 para Instagram Reels
=============================================================
Design: imagem de fundo full bleed + gradiente escuro + título bold + brand bar

Usage:
    python reel_image.py --image fundo.jpg --title "OCEAN SAVES THE PLANET" --subtitle "A descoberta que vai mudar tudo" --output reel.jpg
    python reel_image.py --test   (gera imagem de teste com gradiente)
"""

import argparse
import io
import os
import sys
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

# ── Dimensões 9:16 ────────────────────────────────────────────────────────────
W, H = 1080, 1920

# ── Cores ─────────────────────────────────────────────────────────────────────
GOLD  = (255, 200,   0)
WHITE = (255, 255, 255)
BLACK = (  0,   0,   0)

# ── Fontes (tenta múltiplos caminhos — Windows + Linux/Actions) ───────────────
def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates_bold = [
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/bebas/BebasNeue-Regular.ttf",
        "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
        "arialbd.ttf",
    ]
    candidates_regular = [
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/roboto/Roboto-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
        "arial.ttf",
    ]
    for path in (candidates_bold if bold else candidates_regular):
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return draw.textbbox((0, 0), text, font=font)[2]

def _th(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]

def _wrap(text: str, font, draw: ImageDraw.ImageDraw, max_w: int) -> list[str]:
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

def _shadow_text(draw: ImageDraw.ImageDraw, x: int, y: int, text: str,
                 font, fill=WHITE, shadow: int = 5):
    """Texto com sombra preta para máximo contraste."""
    for dx in range(-shadow, shadow + 1, 2):
        for dy in range(-shadow, shadow + 1, 2):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=BLACK)
    draw.text((x, y), text, font=font, fill=fill)

def _centered_shadow(draw: ImageDraw.ImageDraw, y: int, text: str,
                     font, fill=WHITE, shadow: int = 5) -> int:
    """Texto centrado com sombra. Retorna altura do texto."""
    tw = _tw(draw, text, font)
    _shadow_text(draw, (W - tw) // 2, y, text, font, fill, shadow)
    return _th(draw, text, font)


# ── 1. Crop centrado para 1080×1920 ──────────────────────────────────────────

def fit_background(img: Image.Image) -> Image.Image:
    """Redimensiona e recorta para 1080×1920 mantendo proporção (crop centrado)."""
    img = img.convert("RGB")
    src_w, src_h = img.size
    target_ratio = W / H
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        # mais largo que o target — escala pela altura
        new_h = H
        new_w = int(src_w * H / src_h)
    else:
        # mais alto — escala pela largura
        new_w = W
        new_h = int(src_h * W / src_w)

    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - W) // 2
    top  = (new_h - H) // 2
    return img.crop((left, top, left + W, top + H))


# ── 2. Gradiente escuro na metade inferior ────────────────────────────────────

def apply_gradient(img: Image.Image, start_pct: float = 0.42,
                   max_alpha: int = 210) -> Image.Image:
    """
    Gradiente suave preto de baixo para cima.
    start_pct: onde começa a escurecer (0.0 = topo, 1.0 = fundo)
    max_alpha: opacidade máxima no fundo (0-255). 210 ≈ 82%
    """
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    start_y = int(H * start_pct)

    for y in range(start_y, H):
        t     = (y - start_y) / (H - start_y)          # 0→1
        alpha = int(max_alpha * (t ** 0.6))             # curva suave
        draw.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    base = img.convert("RGBA")
    base.alpha_composite(overlay)
    return base.convert("RGB")


# ── 3. Brand bar ● POSITIVE PULSE ● ──────────────────────────────────────────

def draw_brand(draw: ImageDraw.ImageDraw, y: int, margin: int = 60):
    """Linha fina dourada + ● POSITIVE PULSE ● centrado."""
    bf   = _load_font(30, bold=False)
    full = "● POSITIVE PULSE ●"
    tw   = _tw(draw, full, bf)
    cx   = W // 2

    # Linha decorativa fina acima do texto
    line_y = y - 18
    draw.rectangle([(margin, line_y), (W - margin, line_y + 2)], fill=GOLD)

    # Texto brand centrado
    _shadow_text(draw, cx - tw // 2, y, full, bf, fill=GOLD, shadow=3)


# ── 4. Função principal ───────────────────────────────────────────────────────

def create_reel_image(
    image_path: str,
    title: str,
    output_path: str,
    subtitle: str = "",
    gradient_start: float = 0.40,
) -> str:
    """
    Gera imagem 1080×1920 para Instagram Reel.

    Args:
        image_path:      caminho para a imagem de fundo (local ou URL https://)
        title:           título principal (será convertido para MAIÚSCULAS)
        output_path:     onde guardar o JPEG resultado
        subtitle:        texto secundário opcional
        gradient_start:  onde começa o gradiente (0.0–1.0)

    Returns:
        output_path
    """
    # ── Carregar imagem ───────────────────────────────────────────────────────
    if image_path.startswith("http"):
        resp = requests.get(image_path, timeout=30)
        resp.raise_for_status()
        bg = Image.open(io.BytesIO(resp.content))
    else:
        bg = Image.open(image_path)

    # ── Fit + gradiente ───────────────────────────────────────────────────────
    bg   = fit_background(bg)
    bg   = apply_gradient(bg, start_pct=gradient_start)
    draw = ImageDraw.Draw(bg)

    margin = 70

    # ── Título — terço inferior, branco bold, MAIÚSCULAS ─────────────────────
    title_text = title.upper()
    for size in [115, 98, 84, 70, 58]:
        f_title = _load_font(size, bold=True)
        lines   = _wrap(title_text, f_title, draw, W - 2 * margin)
        if len(lines) <= 3:
            break

    line_h = _th(draw, "A", f_title) + 14

    # Calcular posição Y: acima do brand bar
    brand_y    = H - 105
    subtitle_h = 0

    if subtitle:
        f_sub      = _load_font(46, bold=False)
        sub_lines  = _wrap(subtitle.upper(), f_sub, draw, W - 2 * margin)
        subtitle_h = len(sub_lines) * (_th(draw, "A", f_sub) + 10) + 28

    total_text_h = len(lines) * line_h + subtitle_h
    title_y      = brand_y - total_text_h - 60

    # Desenhar título — dourado como nos carousels
    y = title_y
    for line in lines:
        h = _centered_shadow(draw, y, line, f_title, fill=GOLD, shadow=6)
        y += h + 14

    # ── Subtítulo — branco bold ───────────────────────────────────────────────
    if subtitle:
        y += 16
        f_sub = _load_font(52, bold=True)
        for line in _wrap(subtitle.upper(), f_sub, draw, W - 2 * margin):
            h = _centered_shadow(draw, y, line, f_sub, fill=WHITE, shadow=4)
            y += h + 10

    # ── Brand bar ─────────────────────────────────────────────────────────────
    draw_brand(draw, brand_y, margin=margin)

    # ── Guardar ───────────────────────────────────────────────────────────────
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    bg.save(output_path, "JPEG", quality=95)
    size_kb = Path(output_path).stat().st_size // 1024
    print(f"OK Saved: {output_path}  ({size_kb} KB)")
    return output_path


# ── 5. Imagem de teste ────────────────────────────────────────────────────────

UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "XJWduaYYGKbMDEYFMfKa9EaGxcLN-_KkOlGPahK5x5A")

def fetch_unsplash(query: str) -> Image.Image | None:
    """Busca foto do Unsplash e retorna Image RGB, ou None se falhar."""
    if not UNSPLASH_ACCESS_KEY:
        return None
    for q in [query, query.split()[0]]:
        try:
            r = requests.get(
                "https://api.unsplash.com/photos/random",
                params={"query": q, "orientation": "portrait", "client_id": UNSPLASH_ACCESS_KEY},
                timeout=15,
            )
            d = r.json()
            if r.ok and "urls" in d:
                img_data = requests.get(d["urls"]["regular"], timeout=30).content
                print(f"Unsplash OK: {q}")
                return Image.open(io.BytesIO(img_data)).convert("RGB")
        except Exception as e:
            print(f"Unsplash '{q}': {e}")
    return None

def _make_test_background() -> Image.Image:
    """Gradiente escuro como fundo de fallback."""
    img  = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    top  = (8, 8, 25)
    bot  = (5, 5, 15)
    for y in range(H):
        t = y / H
        c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (W, y)], fill=c)
    return img


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Gera imagem 9:16 para Instagram Reel")
    parser.add_argument("--image",    help="Caminho ou URL da imagem de fundo")
    parser.add_argument("--title",    default="OCEAN SAVES THE PLANET", help="Título principal")
    parser.add_argument("--subtitle", default="A descoberta que vai mudar tudo", help="Subtítulo (opcional)")
    parser.add_argument("--output",   default="reel_output.jpg", help="Ficheiro de saída")
    parser.add_argument("--test",     action="store_true", help="Gera com fundo de teste (sem imagem)")
    args = parser.parse_args()

    if args.test or not args.image:
        print("Gerando imagem de teste com Unsplash...")
        tmp_bg = "test_bg_tmp.jpg"
        bg = fetch_unsplash("artificial intelligence technology futuristic")
        if bg is None:
            print("Unsplash falhou, usando gradiente...")
            bg = _make_test_background()
        bg.save(tmp_bg, "JPEG", quality=95)
        create_reel_image(
            image_path=tmp_bg,
            title=args.title,
            subtitle=args.subtitle,
            output_path=args.output,
        )
        Path(tmp_bg).unlink(missing_ok=True)
    else:
        create_reel_image(
            image_path=args.image,
            title=args.title,
            subtitle=args.subtitle,
            output_path=args.output,
        )


if __name__ == "__main__":
    main()
