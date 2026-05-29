#!/usr/bin/env python3
"""
signal_bot.py — Crypto TA Scanner com Claude
=============================================
Escaneia BTC, ETH, SOL, BNB no timeframe 4H
Só envia alerta quando há um setup FRESCO de alta qualidade
Envia gráfico + análise para Telegram
"""

import os, io, json, logging, base64
from datetime import datetime, timezone

import ccxt
import pandas as pd
import pandas_ta as ta
import matplotlib
matplotlib.use("Agg")
import mplfinance as mpf
import matplotlib.pyplot as plt
import anthropic
import requests

# ─── Config ───────────────────────────────────────────────────────────────────

PAIRS     = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
TIMEFRAME = "4h"
LIMIT     = 200   # velas a buscar

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

# ─── 1. Dados ─────────────────────────────────────────────────────────────────

def fetch_ohlcv(symbol: str) -> pd.DataFrame:
    exchange = ccxt.bybit({"enableRateLimit": True})
    raw = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=LIMIT)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    return df

# ─── 2. Indicadores ───────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df.ta.ema(length=20,  append=True)
    df.ta.ema(length=50,  append=True)
    df.ta.ema(length=200, append=True)
    df.ta.rsi(length=14,  append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.bbands(length=20, std=2, append=True)
    df.ta.atr(length=14, append=True)
    return df

# ─── 3. Pré-filtro (sem chamar Claude) ────────────────────────────────────────

def is_fresh_setup(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Verifica se um sinal ACABOU de acontecer (última ou penúltima vela).
    Retorna (True, motivo) ou (False, "").
    """
    c  = df.iloc[-1]   # vela atual
    p  = df.iloc[-2]   # vela anterior
    p2 = df.iloc[-3]   # 2 velas atrás

    rsi   = c.get("RSI_14")
    p_rsi = p.get("RSI_14")

    e20, e50     = c.get("EMA_20"), c.get("EMA_50")
    pe20, pe50   = p.get("EMA_20"), p.get("EMA_50")
    p2e20, p2e50 = p2.get("EMA_20"), p2.get("EMA_50")

    macd, sig     = c.get("MACD_12_26_9"), c.get("MACDs_12_26_9")
    p_macd, p_sig = p.get("MACD_12_26_9"), p.get("MACDs_12_26_9")

    bb_low  = c.get("BBL_20_2.0")
    bb_high = c.get("BBU_20_2.0")
    close   = c["close"]

    reasons = []

    # RSI acabou de entrar em zona extrema
    if rsi is not None and p_rsi is not None:
        if p_rsi >= 32 and rsi < 32:
            reasons.append(f"RSI oversold ({rsi:.1f})")
        if p_rsi <= 68 and rsi > 68:
            reasons.append(f"RSI overbought ({rsi:.1f})")

    # EMA cross nas últimas 2 velas
    if all(v is not None for v in [e20, e50, pe20, pe50, p2e20, p2e50]):
        if p2e20 < p2e50 and pe20 >= pe50:   # cross bullish na vela anterior
            reasons.append("EMA20 cruzou acima EMA50 (bullish)")
        if p2e20 > p2e50 and pe20 <= pe50:   # cross bearish na vela anterior
            reasons.append("EMA20 cruzou abaixo EMA50 (bearish)")

    # MACD cross fresco
    if all(v is not None for v in [macd, sig, p_macd, p_sig]):
        if p_macd < p_sig and macd > sig:
            reasons.append("MACD cruzou bullish")
        if p_macd > p_sig and macd < sig:
            reasons.append("MACD cruzou bearish")

    # Preço tocou Bollinger Band
    if bb_low and close < bb_low * 1.002:
        reasons.append(f"Preço tocou BB inferior ({close:.2f})")
    if bb_high and close > bb_high * 0.998:
        reasons.append(f"Preço tocou BB superior ({close:.2f})")

    if reasons:
        return True, " | ".join(reasons)
    return False, ""

# ─── 4. Gráfico ───────────────────────────────────────────────────────────────

def generate_chart(df: pd.DataFrame, symbol: str) -> str:
    plot = df.tail(80).copy()

    # mplfinance precisa de colunas capitalizadas
    mpf_df = plot[["open", "high", "low", "close", "volume"]].copy()
    mpf_df.columns = ["Open", "High", "Low", "Close", "Volume"]

    apds = []

    def safe_ap(col, **kw):
        if col in plot.columns and plot[col].notna().sum() > 5:
            apds.append(mpf.make_addplot(plot[col], **kw))

    safe_ap("EMA_20",  color="#00bfff", width=1.2, panel=0)
    safe_ap("EMA_50",  color="orange",  width=1.2, panel=0)
    safe_ap("EMA_200", color="#ff4444", width=1.5, panel=0)
    safe_ap("BBU_20_2.0", color="#888888", width=0.8, linestyle="--", panel=0)
    safe_ap("BBL_20_2.0", color="#888888", width=0.8, linestyle="--", panel=0)

    # RSI panel
    if "RSI_14" in plot.columns:
        safe_ap("RSI_14", panel=2, color="purple", width=1.2, ylabel="RSI")
        ob = pd.Series(70, index=plot.index)
        os_ = pd.Series(30, index=plot.index)
        apds.append(mpf.make_addplot(ob,  panel=2, color="red",   linestyle="--", width=0.7))
        apds.append(mpf.make_addplot(os_, panel=2, color="green", linestyle="--", width=0.7))

    # MACD panel
    if "MACD_12_26_9" in plot.columns and "MACDs_12_26_9" in plot.columns:
        safe_ap("MACD_12_26_9",  panel=3, color="#00bfff", width=1.0, ylabel="MACD")
        safe_ap("MACDs_12_26_9", panel=3, color="red",     width=1.0)
        if "MACDh_12_26_9" in plot.columns:
            hist = plot["MACDh_12_26_9"].fillna(0)
            colors = ["#26a69a" if v >= 0 else "#ef5350" for v in hist]
            apds.append(mpf.make_addplot(hist, type="bar", panel=3, color=colors, width=0.8))

    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        gridstyle="--",
        gridcolor="#2a2a2a",
        facecolor="#0d0d0d",
        edgecolor="#333333",
        rc={"axes.labelcolor": "#cccccc", "xtick.color": "#aaaaaa", "ytick.color": "#aaaaaa"},
    )

    path = f"chart_{symbol.replace('/', '_')}.png"
    kwargs = dict(
        type="candle",
        style=style,
        volume=True,
        panel_ratios=(4, 1, 1.5, 1.5),
        figsize=(14, 10),
        title=f"\n{symbol}  {TIMEFRAME.upper()}  —  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
        savefig=dict(fname=path, dpi=150, bbox_inches="tight"),
        warn_too_much_data=500,
    )
    if apds:
        kwargs["addplot"] = apds

    mpf.plot(mpf_df, **kwargs)
    plt.close("all")
    log.info(f"Gráfico gerado: {path}")
    return path

# ─── 5. Análise Claude ────────────────────────────────────────────────────────

def analyze(df: pd.DataFrame, symbol: str, chart_path: str, trigger: str) -> dict:
    c = df.iloc[-1]

    def f(col): return round(float(c[col]), 6) if col in c and pd.notna(c[col]) else None

    data = {
        "symbol": symbol, "timeframe": TIMEFRAME,
        "close":    f("close"),
        "rsi":      f("RSI_14"),
        "ema20":    f("EMA_20"),   "ema50":  f("EMA_50"),  "ema200": f("EMA_200"),
        "macd":     f("MACD_12_26_9"), "macd_signal": f("MACDs_12_26_9"),
        "bb_upper": f("BBU_20_2.0"),   "bb_lower":    f("BBL_20_2.0"),
        "atr":      f("ATRr_14"),
        "trigger":  trigger,
    }

    with open(chart_path, "rb") as fh:
        img_b64 = base64.standard_b64encode(fh.read()).decode()

    prompt = f"""És um trader técnico experiente. Analisa este gráfico de {symbol} {TIMEFRAME}.

Indicadores atuais:
{json.dumps(data, indent=2)}

Trigger detetado: {trigger}

Analisa o gráfico com atenção. Avalia:
- Direção da tendência (alinhamento EMAs)
- Momentum (RSI, MACD)
- Estrutura de preço (suporte/resistência, padrões de velas)
- Confirmação de volume
- Qualidade geral do setup

Responde APENAS em JSON válido:
{{
  "has_signal": true ou false,
  "direction": "LONG" ou "SHORT" ou "NONE",
  "confidence": número de 1 a 10,
  "entry_zone": "nível de entrada ou range",
  "stop_loss": "nível de stop",
  "take_profit_1": "primeiro alvo",
  "take_profit_2": "segundo alvo",
  "risk_reward": "ex: 1:2.5",
  "setup_name": "nome do setup, ex: EMA Cross + RSI Confirmation",
  "analysis": "2-3 frases a explicar o setup de forma clara e objetiva"
}}

Só coloca has_signal=true se confidence >= 7.
Sê rigoroso — apenas setups de alta qualidade."""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=600,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text",  "text": prompt},
            ],
        }],
    )

    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

# ─── 6. Telegram ──────────────────────────────────────────────────────────────

def send_telegram(symbol: str, analysis: dict, chart_path: str):
    d = analysis["direction"]
    emoji = "🟢" if d == "LONG" else "🔴"
    conf  = analysis.get("confidence", 0)
    stars = "⭐" * min(conf, 5)

    caption = (
        f"{emoji} *{symbol} — {d}*  {stars}\n"
        f"⏱ `{TIMEFRAME.upper()}`  •  {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n\n"
        f"📌 *Setup:* {analysis.get('setup_name','')}\n\n"
        f"💰 *Entrada:*  `{analysis.get('entry_zone','')}`\n"
        f"🛑 *Stop Loss:* `{analysis.get('stop_loss','')}`\n"
        f"🎯 *TP1:*  `{analysis.get('take_profit_1','')}`\n"
        f"🎯 *TP2:*  `{analysis.get('take_profit_2','')}`\n"
        f"📊 *R:R:*  `{analysis.get('risk_reward','')}`\n"
        f"⭐ *Confiança:* `{conf}/10`\n\n"
        f"📝 _{analysis.get('analysis','')}_\n\n"
        f"⚠️ _Não é conselho financeiro. DYOR._"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(chart_path, "rb") as fh:
        r = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "Markdown"},
            files={"photo": fh},
            timeout=30,
        )
    log.info(f"Telegram: {r.status_code}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY não definida")
        return

    log.info(f"=== Crypto Scanner — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

    for symbol in PAIRS:
        try:
            log.info(f"A escanear {symbol}...")
            df = fetch_ohlcv(symbol)
            df = add_indicators(df)

            fresh, trigger = is_fresh_setup(df)
            if not fresh:
                log.info(f"  Sem setup fresco")
                continue

            log.info(f"  Setup detetado: {trigger}")
            chart = generate_chart(df, symbol)

            log.info(f"  A analisar com Claude...")
            result = analyze(df, symbol, chart, trigger)

            conf = result.get("confidence", 0)
            has_signal = result.get("has_signal", False)
            log.info(f"  Resultado: {result.get('direction')} | Confiança: {conf}/10 | Sinal: {has_signal}")

            if has_signal and conf >= 7:
                log.info(f"  *** SINAL ENVIADO para Telegram ***")
                send_telegram(symbol, result, chart)
            else:
                log.info(f"  Setup descartado (qualidade insuficiente)")

        except Exception as e:
            log.error(f"  Erro com {symbol}: {e}", exc_info=True)

    log.info("=== Scan concluído ===")


if __name__ == "__main__":
    main()
