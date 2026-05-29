"""
test_annotated.py — Envia gráfico BTC 4H totalmente anotado para Telegram.
Usa dados reais com Entry/SL/TP baseados no ATR para ver o aspecto final.
"""
import os
os.chdir(r"C:\Users\joaoa\insta-claude")

os.environ.setdefault("TELEGRAM_TOKEN",   "8808175410:AAF0g8aOgP29tdql527myoViHRsLW6p4IYs")
os.environ.setdefault("TELEGRAM_CHAT_ID", "6788304518")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy")

from signal_bot import (
    fetch_4h, add_indicators, get_daily_trend,
    find_sr_levels, detect_patterns,
    generate_chart_for_telegram,
)
import requests

symbol = "BTC/USDT"
print(f"A buscar dados de {symbol}...")
df = fetch_4h(symbol)
print(f"  {len(df)} velas 4H  ({df.index[0].date()} ate {df.index[-1].date()})")

df = add_indicators(df)
daily_trend = get_daily_trend(symbol)
print(f"Daily trend: {daily_trend}")

supports, resistances = find_sr_levels(df)
print(f"S/R: {len(supports)} suportes, {len(resistances)} resistencias")

patterns = detect_patterns(df)
print(f"Padroes reais: {patterns}")

close = float(df.iloc[-1]["close"])
atr   = float(df.iloc[-1].get("ATRr_14", close * 0.015))

# Forçar padrão bullish para o teste visual
if not patterns:
    patterns = ["Hammer"]

entry = round(close, 0)
sl    = round(close - atr * 1.5, 0)
tp1   = round(close + atr * 2.0, 0)
tp2   = round(close + atr * 3.5, 0)
rr    = round((tp1 - entry) / max(entry - sl, 1), 2)

print(f"\nPreco: {close:,.0f}  |  ATR: {atr:,.0f}")
print(f"Entry: {entry:,.0f}  |  SL: {sl:,.0f}  |  TP1: {tp1:,.0f}  |  TP2: {tp2:,.0f}")
print(f"Padrao(s) a desenhar: {patterns}")

print("\nA gerar grafico anotado...")
chart = generate_chart_for_telegram(
    df, symbol, supports, resistances, daily_trend,
    patterns, entry, sl, tp1, tp2
)
print(f"Grafico: {chart}")

# Monta caption Telegram
icon = {"BULLISH":"📈","BEARISH":"📉","NEUTRAL":"➡️"}.get(daily_trend,"")
pat_str = patterns[0] if patterns else "—"

caption = (
    f"🟢 *{symbol} — LONG*  ⭐⭐⭐⭐\n"
    f"⏱ `4H`  •  Grafico de Teste\n"
    f"{icon} *Daily:* `{daily_trend}`\n\n"
    f"📌 *Setup:* EMA + RSI Bounce\n"
    f"*Confluencia (3 sinais):*\n"
    f"   ✅ EMA20 acima EMA50\n"
    f"   ✅ RSI zona favoravel\n"
    f"   ✅ Padrao: {pat_str}\n\n"
    f"💰 *Entrada:*  `{entry:,.0f}`\n"
    f"🛑 *Stop Loss:* `{sl:,.0f}`\n"
    f"🎯 *TP1:*  `{tp1:,.0f}`\n"
    f"🎯 *TP2:*  `{tp2:,.0f}`\n"
    f"📊 *R:R:*  `1:{rr}`\n"
    f"⭐ *Confianca:* `8/10`\n\n"
    f"📝 _Grafico de teste — linhas desenhadas directamente no chart_\n\n"
    f"⚠️ _Nao e conselho financeiro. DYOR._"
)

print("A enviar para Telegram...")
token   = os.environ["TELEGRAM_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]
url = f"https://api.telegram.org/bot{token}/sendPhoto"

with open(chart, "rb") as f:
    r = requests.post(
        url,
        data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"},
        files={"photo": f},
        timeout=30,
    )

ok = r.json().get("ok")
print(f"Resposta: {r.status_code} — ok={ok}")
if not ok:
    print(f"Erro: {r.json()}")
else:
    print("Verifica o Telegram!")
