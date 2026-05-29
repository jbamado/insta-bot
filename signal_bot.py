#!/usr/bin/env python3
"""
signal_bot.py — Crypto TA Scanner PRO com Claude
=================================================
v3: Gráfico anotado — Entry/SL/TP + padrões de vela desenhados no chart
"""

import os, io, json, logging, base64, re
from datetime import datetime, timezone

import yfinance as yf
import pandas as pd
import pandas_ta as ta
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.transforms import blended_transform_factory
import mplfinance as mpf
import anthropic
import requests

# ─── Config ───────────────────────────────────────────────────────────────────

PAIRS          = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
TIMEFRAME      = "4h"
LIMIT          = 200
MIN_CONFLUENCE = 2    # mínimo de condições em simultâneo
MIN_CONFIDENCE = 7    # confiança mínima Claude para enviar alerta

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)

# ─── 1. Dados ─────────────────────────────────────────────────────────────────

def _ticker(symbol: str) -> str:
    return symbol.split("/")[0] + "-USD"

def fetch_4h(symbol: str) -> pd.DataFrame:
    raw = yf.download(_ticker(symbol), period="60d", interval="1h", progress=False, auto_adjust=True)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.droplevel(1)
    raw.columns = [c.lower() for c in raw.columns]
    df = raw.resample("4h").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()
    return df.tail(LIMIT)

# Alias para retrocompatibilidade com test_chart.py
fetch_ohlcv = fetch_4h

def fetch_daily(symbol: str) -> pd.DataFrame:
    raw = yf.download(_ticker(symbol), period="365d", interval="1d", progress=False, auto_adjust=True)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.droplevel(1)
    raw.columns = [c.lower() for c in raw.columns]
    return raw.dropna().tail(200)

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df.ta.ema(length=20,  append=True)
    df.ta.ema(length=50,  append=True)
    df.ta.ema(length=200, append=True)
    df.ta.rsi(length=14,  append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.bbands(length=20, std=2, append=True)
    df.ta.atr(length=14, append=True)
    return df

# ─── 2. Tendência Diária (MTF) ────────────────────────────────────────────────

def get_daily_trend(symbol: str) -> str:
    try:
        daily = fetch_daily(symbol)
        daily.ta.ema(length=50,  append=True)
        daily.ta.ema(length=200, append=True)
        last  = daily.iloc[-1]
        close = float(last["close"])
        e50   = float(last.get("EMA_50")  or 0)
        e200  = float(last.get("EMA_200") or 0)
        if close > e50 > e200:
            return "BULLISH"
        elif close < e50 < e200:
            return "BEARISH"
        return "NEUTRAL"
    except Exception as e:
        log.warning(f"Daily trend error: {e}")
        return "NEUTRAL"

# ─── 3. Suporte & Resistência ─────────────────────────────────────────────────

def find_sr_levels(df: pd.DataFrame, n: int = 4, lookback: int = 80) -> tuple[list, list]:
    sub = df.tail(lookback).reset_index(drop=True)
    supports, resistances = [], []
    for i in range(n, len(sub) - n):
        lo = float(sub.iloc[i]["low"])
        hi = float(sub.iloc[i]["high"])
        if all(lo <= float(sub.iloc[i-j]["low"])  for j in range(1, n+1)) and \
           all(lo <= float(sub.iloc[i+j]["low"])  for j in range(1, n+1)):
            supports.append(lo)
        if all(hi >= float(sub.iloc[i-j]["high"]) for j in range(1, n+1)) and \
           all(hi >= float(sub.iloc[i+j]["high"]) for j in range(1, n+1)):
            resistances.append(hi)

    def cluster(levels, thr=0.008):
        if not levels:
            return []
        levels = sorted(levels)
        out = [levels[0]]
        for lvl in levels[1:]:
            if (lvl - out[-1]) / out[-1] > thr:
                out.append(lvl)
            else:
                out[-1] = (out[-1] + lvl) / 2
        return out

    return cluster(supports), cluster(resistances)

def nearest_sr(price: float, supports: list, resistances: list) -> tuple[float, str, float]:
    all_lvls = [(s, "support") for s in supports] + [(r, "resistance") for r in resistances]
    if not all_lvls:
        return 0, "none", 999
    best = min(all_lvls, key=lambda x: abs(x[0] - price))
    return best[0], best[1], abs(best[0] - price) / price * 100

# ─── 4. Padrões de Velas ──────────────────────────────────────────────────────

def detect_patterns(df: pd.DataFrame) -> list[str]:
    if len(df) < 2:
        return []
    c, p = df.iloc[-1], df.iloc[-2]
    o, h, l, cl      = float(c["open"]), float(c["high"]), float(c["low"]),  float(c["close"])
    po, ph, pl, pcl   = float(p["open"]), float(p["high"]), float(p["low"]), float(p["close"])
    body = abs(cl - o)
    rng  = h - l
    lw   = min(o, cl) - l
    uw   = h - max(o, cl)
    found = []
    if rng == 0:
        return found
    if body / rng < 0.1:
        found.append("Doji")
    if body > 0 and lw >= 2 * body and uw <= 0.3 * body and cl > o:
        found.append("Hammer")
    if body > 0 and uw >= 2 * body and lw <= 0.3 * body:
        found.append("Shooting Star" if cl < o else "Inverted Hammer")
    if pcl < po and cl > o and o <= pcl and cl >= po:
        found.append("Bullish Engulfing")
    if pcl > po and cl < o and o >= pcl and cl <= po:
        found.append("Bearish Engulfing")
    if cl > o and body / rng > 0.85:
        found.append("Marubozu Bullish")
    return found

# ─── 5. Confluência (≥ MIN_CONFLUENCE condições) ──────────────────────────────

def check_confluence(df: pd.DataFrame, daily_trend: str,
                     supports: list, resistances: list) -> tuple[int, list, str]:
    c, p, p2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]

    rsi, p_rsi   = c.get("RSI_14"),        p.get("RSI_14")
    e20, e50     = c.get("EMA_20"),         c.get("EMA_50")
    pe20, pe50   = p.get("EMA_20"),         p.get("EMA_50")
    p2e20, p2e50 = p2.get("EMA_20"),        p2.get("EMA_50")
    macd, sig    = c.get("MACD_12_26_9"),   c.get("MACDs_12_26_9")
    p_macd, p_sig = p.get("MACD_12_26_9"), p.get("MACDs_12_26_9")
    bb_low       = c.get("BBL_20_2.0")
    bb_high      = c.get("BBU_20_2.0")
    close        = float(c["close"])

    vol_avg   = float(df["volume"].tail(20).mean())
    vol_cur   = float(c["volume"])
    vol_spike = vol_cur > vol_avg * 1.5

    patterns  = detect_patterns(df)
    sr_level, sr_type, sr_dist = nearest_sr(close, supports, resistances)

    bull, bear = [], []

    # ── EMA alignment / cross ─────────────────────────────────────────────────
    if all(v is not None for v in [e20, e50, pe20, pe50, p2e20, p2e50]):
        if p2e20 < p2e50 and pe20 >= pe50:
            bull.append("EMA20 x EMA50 bullish cross")
        elif e20 > e50:
            bull.append("EMA20 acima EMA50")
        if p2e20 > p2e50 and pe20 <= pe50:
            bear.append("EMA20 x EMA50 bearish cross")
        elif e20 < e50:
            bear.append("EMA20 abaixo EMA50")

    # ── RSI ───────────────────────────────────────────────────────────────────
    if rsi is not None and p_rsi is not None:
        if p_rsi >= 30 and rsi < 30:
            bull.append(f"RSI oversold ({rsi:.1f})")
        elif 30 <= rsi <= 45:
            bull.append(f"RSI zona favoravel ({rsi:.1f})")
        if p_rsi <= 70 and rsi > 70:
            bear.append(f"RSI overbought ({rsi:.1f})")
        elif 55 <= rsi <= 70:
            bear.append(f"RSI zona favoravel ({rsi:.1f})")

    # ── MACD ──────────────────────────────────────────────────────────────────
    if all(v is not None for v in [macd, sig, p_macd, p_sig]):
        if p_macd < p_sig and macd >= p_sig:
            bull.append("MACD cross bullish")
        elif macd > sig and macd > 0:
            bull.append("MACD bullish acima zero")
        if p_macd > p_sig and macd <= p_sig:
            bear.append("MACD cross bearish")
        elif macd < sig and macd < 0:
            bear.append("MACD bearish abaixo zero")

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    if bb_low  and close < bb_low  * 1.005:
        bull.append("Preco na BB inferior")
    if bb_high and close > bb_high * 0.995:
        bear.append("Preco na BB superior")

    # ── S&R proximity ─────────────────────────────────────────────────────────
    if sr_type == "support"    and sr_dist < 1.5:
        bull.append(f"Suporte proximo ({sr_level:.2f}, {sr_dist:.1f}%)")
    if sr_type == "resistance" and sr_dist < 1.5:
        bear.append(f"Resistencia proxima ({sr_level:.2f}, {sr_dist:.1f}%)")

    # ── Candlestick patterns ───────────────────────────────────────────────────
    bull_pats = [p for p in patterns if any(k in p for k in ["Hammer","Bullish Engulfing","Marubozu"])]
    bear_pats = [p for p in patterns if any(k in p for k in ["Shooting Star","Bearish Engulfing"])]
    if bull_pats: bull.append(f"Padrao: {bull_pats[0]}")
    if bear_pats: bear.append(f"Padrao: {bear_pats[0]}")

    # ── Volume spike ──────────────────────────────────────────────────────────
    if vol_spike:
        tag = f"Volume spike ({vol_cur/vol_avg:.1f}x medio)"
        bull.append(tag)
        bear.append(tag)

    # ── Daily trend filter ────────────────────────────────────────────────────
    bs, brs = len(bull), len(bear)
    if daily_trend == "BULLISH" and brs > bs and brs < 3:
        brs = max(0, brs - 1)
    elif daily_trend == "BEARISH" and bs > brs and bs < 3:
        bs = max(0, bs - 1)

    if bs >= brs and bs >= MIN_CONFLUENCE:
        return bs, bull, "LONG"
    elif brs > bs and brs >= MIN_CONFLUENCE:
        return brs, bear, "SHORT"
    return max(bs, brs), [], "NONE"

# ─── 6. Backtest rápido ───────────────────────────────────────────────────────

def quick_backtest(df: pd.DataFrame, direction: str) -> tuple[int, int]:
    TP, SL = 0.015, 0.010
    wins = total = 0
    for i in range(20, len(df) - 6):
        row, prev = df.iloc[i], df.iloc[i-1]
        rsi   = row.get("RSI_14", 50)
        ema20 = row.get("EMA_20", 0);  ema50 = row.get("EMA_50", 0)
        pe20  = prev.get("EMA_20", 0); pe50  = prev.get("EMA_50", 0)
        macd  = row.get("MACD_12_26_9", 0);  sig = row.get("MACDs_12_26_9", 0)
        pm    = prev.get("MACD_12_26_9", 0); ps  = prev.get("MACDs_12_26_9", 0)
        bbl   = row.get("BBL_20_2.0", 0);    bbh = row.get("BBU_20_2.0", 0)
        close = float(row["close"])
        matched = (direction == "LONG"  and (rsi < 33 or (pe20 < pe50 and ema20 >= pe50) or
                   (pm < ps and macd >= ps) or (bbl and close < bbl*1.003))) or \
                  (direction == "SHORT" and (rsi > 67 or (pe20 > pe50 and ema20 <= pe50) or
                   (pm > ps and macd <= ps) or (bbh and close > bbh*0.997)))
        if not matched:
            continue
        total += 1
        for j in range(i+1, min(i+11, len(df))):
            hi = float(df.iloc[j]["high"]); lo = float(df.iloc[j]["low"])
            if direction == "LONG":
                if hi >= close*(1+TP): wins += 1; break
                if lo <= close*(1-SL): break
            else:
                if lo <= close*(1-TP): wins += 1; break
                if hi >= close*(1+SL): break
    return wins, total

# ─── 7. Gráfico ───────────────────────────────────────────────────────────────

def generate_chart_fig(df: pd.DataFrame, symbol: str, supports: list,
                       resistances: list, daily_trend: str):
    """Gera o gráfico e devolve (fig, axes, plot_df) — sem guardar em disco."""
    plot  = df.tail(80).copy()
    mpf_df = plot[["open","high","low","close","volume"]].copy()
    mpf_df.columns = ["Open","High","Low","Close","Volume"]

    apds = []
    def safe_ap(col, **kw):
        if col in plot.columns and plot[col].notna().sum() > 5:
            apds.append(mpf.make_addplot(plot[col], **kw))

    safe_ap("EMA_20",     color="#00bfff", width=1.2, panel=0)
    safe_ap("EMA_50",     color="orange",  width=1.2, panel=0)
    safe_ap("EMA_200",    color="#ff4444", width=1.5, panel=0)
    safe_ap("BBU_20_2.0", color="#666666", width=0.8, linestyle="--", panel=0)
    safe_ap("BBL_20_2.0", color="#666666", width=0.8, linestyle="--", panel=0)

    if "RSI_14" in plot.columns:
        safe_ap("RSI_14", panel=2, color="purple", width=1.2, ylabel="RSI")
        apds.append(mpf.make_addplot(pd.Series(70, index=plot.index), panel=2,
                                     color="red",   linestyle="--", width=0.7))
        apds.append(mpf.make_addplot(pd.Series(30, index=plot.index), panel=2,
                                     color="green", linestyle="--", width=0.7))

    if "MACD_12_26_9" in plot.columns:
        safe_ap("MACD_12_26_9",  panel=3, color="#00bfff", width=1.0, ylabel="MACD")
        safe_ap("MACDs_12_26_9", panel=3, color="red",     width=1.0)
        if "MACDh_12_26_9" in plot.columns:
            hist = plot["MACDh_12_26_9"].fillna(0)
            apds.append(mpf.make_addplot(hist, type="bar", panel=3,
                        color=["#26a69a" if v >= 0 else "#ef5350" for v in hist], width=0.8))

    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds", gridstyle="--", gridcolor="#2a2a2a",
        facecolor="#0d0d0d", edgecolor="#333333",
        rc={"axes.labelcolor":"#cccccc","xtick.color":"#aaaaaa","ytick.color":"#aaaaaa"},
    )

    icon  = {"BULLISH":"[UP]","BEARISH":"[DN]","NEUTRAL":"[--]"}.get(daily_trend,"")
    title = (f"\n{symbol} 4H  —  "
             f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC  |  "
             f"Daily: {icon} {daily_trend}")

    lo_rng = float(plot["low"].min())  * 0.97
    hi_rng = float(plot["high"].max()) * 1.03
    sr_prices, sr_colors = [], []
    for s in supports:
        if lo_rng <= s <= hi_rng:
            sr_prices.append(s); sr_colors.append("#00ff88")
    for r in resistances:
        if lo_rng <= r <= hi_rng:
            sr_prices.append(r); sr_colors.append("#ff4444")

    kwargs = dict(
        type="candle", style=style, volume=True,
        panel_ratios=(4,1,1.5,1.5), figsize=(14,10), title=title,
        returnfig=True,
        warn_too_much_data=500,
    )
    if apds:
        kwargs["addplot"] = apds
    if sr_prices:
        kwargs["hlines"] = dict(hlines=sr_prices, colors=sr_colors,
                                linestyle="-.", linewidths=1.2, alpha=0.75)

    fig, axes = mpf.plot(mpf_df, **kwargs)
    return fig, axes, plot


def annotate_chart(fig, axes, plot_df: pd.DataFrame,
                   patterns: list = None,
                   entry: float = None, sl: float = None,
                   tp1: float = None, tp2: float = None,
                   direction: str = "LONG"):
    """
    Desenha no gráfico:
    - Marcadores de padrão de vela (triângulo + nome) na última vela
    - Linhas horizontais tracejadas para Entry / SL / TP1 / TP2
    - Zona de risco sombreada entre Entry e SL
    """
    ax    = axes[0]                                             # painel de preço
    trans = blended_transform_factory(ax.transAxes, ax.transData)
    last  = plot_df.iloc[-1]

    # ── Marcadores de padrões de vela ─────────────────────────────────────────
    if patterns:
        bull_pats = [p for p in patterns
                     if any(k in p for k in ["Hammer","Bullish Engulfing","Marubozu"])]
        bear_pats = [p for p in patterns
                     if any(k in p for k in ["Shooting Star","Bearish Engulfing"])]

        try:
            last_x = mdates.date2num(plot_df.index[-1].to_pydatetime())
        except Exception:
            last_x = len(plot_df) - 1

        if bull_pats:
            y = float(last["low"]) * 0.9965
            ax.scatter([last_x], [y], marker="^", color="lime",
                       s=280, zorder=10, clip_on=False)
            ax.text(0.97, y, f" {bull_pats[0]}", transform=trans,
                    ha="right", va="top", color="lime",
                    fontsize=8.5, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="#001800", alpha=0.85))

        if bear_pats:
            y = float(last["high"]) * 1.0035
            ax.scatter([last_x], [y], marker="v", color="#ff4444",
                       s=280, zorder=10, clip_on=False)
            ax.text(0.97, y, f" {bear_pats[0]}", transform=trans,
                    ha="right", va="bottom", color="#ff4444",
                    fontsize=8.5, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="#180000", alpha=0.85))

    # ── Linhas de Entry / SL / TP ─────────────────────────────────────────────
    def _fmt(p: float) -> str:
        return f"{p:,.2f}" if p < 1_000 else f"{p:,.0f}"

    trade_lvls = []
    if entry is not None:
        trade_lvls.append((entry, "white",   2.0, f"  ENTRY  {_fmt(entry)}"))
    if sl is not None:
        trade_lvls.append((sl,    "#ff3333", 2.0, f"  SL    {_fmt(sl)}"))
    if tp1 is not None:
        trade_lvls.append((tp1,   "#00dd44", 1.8, f"  TP1   {_fmt(tp1)}"))
    if tp2 is not None:
        trade_lvls.append((tp2,   "#00aa33", 1.5, f"  TP2   {_fmt(tp2)}"))

    for price, color, lw, label in trade_lvls:
        ax.axhline(y=price, color=color, linestyle="--", linewidth=lw,
                   alpha=0.9, zorder=5)
        ax.text(0.01, price, label, transform=trans,
                ha="left", va="bottom",
                color=color, fontsize=8.5, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#111111", alpha=0.8))

    # ── Zona de risco (sombra amarela entre Entry e SL) ───────────────────────
    if entry is not None and sl is not None:
        lo = min(entry, sl)
        hi = max(entry, sl)
        ax.axhspan(lo, hi, alpha=0.07, color="yellow", zorder=1)


def fig_to_bytes(fig) -> bytes:
    """Serializa a figura para PNG em memória sem fechar a figura."""
    buf = io.BytesIO()
    fig.savefig(buf, dpi=150, bbox_inches="tight", facecolor="#0d0d0d")
    buf.seek(0)
    return buf.read()


def save_chart(fig, symbol: str) -> str:
    """Guarda a figura em disco e fecha-a. Devolve o path."""
    path = f"chart_{symbol.replace('/','_')}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0d0d0d")
    plt.close(fig)
    log.info(f"Grafico guardado: {path}")
    return path


# Alias retrocompatibilidade (test_chart.py usa generate_chart)
def generate_chart(df, symbol, supports=None, resistances=None, daily_trend="NEUTRAL"):
    fig, axes, plot_df = generate_chart_fig(
        df, symbol, supports or [], resistances or [], daily_trend)
    return save_chart(fig, symbol)


def parse_price(text) -> float | None:
    """Extrai o primeiro número válido de uma string de preço."""
    if not text:
        return None
    nums = re.findall(r'[\d]+(?:[.,][\d]+)*', str(text))
    for n in nums:
        try:
            val = float(n.replace(",", ""))
            if val > 0:
                return val
        except ValueError:
            pass
    return None

# ─── 8. Análise Claude ────────────────────────────────────────────────────────

def analyze(df: pd.DataFrame, symbol: str, img_b64: str,
            conditions: list, direction: str, daily_trend: str,
            supports: list, resistances: list, winrate_info: str) -> dict:
    c = df.iloc[-1]
    def f(col): return round(float(c[col]), 6) if col in c and pd.notna(c[col]) else None

    data = {
        "symbol": symbol, "timeframe": "4H", "daily_trend": daily_trend,
        "close": f("close"), "rsi": f("RSI_14"),
        "ema20": f("EMA_20"), "ema50": f("EMA_50"), "ema200": f("EMA_200"),
        "macd": f("MACD_12_26_9"), "macd_signal": f("MACDs_12_26_9"),
        "bb_upper": f("BBU_20_2.0"), "bb_lower": f("BBL_20_2.0"),
        "atr": f("ATRr_14"),
        "conditions_detected": conditions,
        "preferred_direction": direction,
        "key_supports":    [round(s, 2) for s in supports[-4:]],
        "key_resistances": [round(r, 2) for r in resistances[:4]],
        "backtest": winrate_info,
    }

    prompt = f"""Es um trader tecnico expert. Analisa este grafico {symbol} 4H.

Dados:
{json.dumps(data, indent=2)}

O sistema detetou {len(conditions)} condicoes: {', '.join(conditions)}
Tendencia diaria: {daily_trend} | Direcao sugerida: {direction}

Analisa o grafico e confirma ou rejeita. Considera:
1. Alinhamento com tendencia diaria
2. Qualidade dos niveis S/R (linhas no grafico)
3. Espaco ate proxima resistencia/suporte
4. Momentum atual (RSI, MACD)
5. Padroes de velas visiveis

IMPORTANTE: entry_zone, stop_loss, take_profit_1, take_profit_2 devem ser NUMEROS EXACTOS
(ex: "95200" ou "94800", nao ranges como "95000-95500").

Responde APENAS em JSON valido:
{{
  "has_signal": true/false,
  "direction": "LONG"/"SHORT"/"NONE",
  "confidence": 1-10,
  "entry_zone": "preco exacto",
  "stop_loss": "nivel de preco",
  "take_profit_1": "alvo 1 preco",
  "take_profit_2": "alvo 2 preco",
  "risk_reward": "ex: 1:2.5",
  "setup_name": "nome do setup",
  "analysis": "2-3 frases claras e objetivas"
}}
So has_signal=true se confidence >= 7. Se rigoroso."""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-opus-4-5", max_tokens=600,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
            {"type": "text",  "text": prompt},
        ]}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
    return json.loads(raw.strip())

# ─── 9. Telegram ──────────────────────────────────────────────────────────────

def send_telegram(symbol: str, analysis: dict, chart_path: str,
                  conditions: list, daily_trend: str, wins: int, total: int):
    d     = analysis["direction"]
    emoji = "🟢" if d == "LONG" else "🔴"
    conf  = analysis.get("confidence", 0)
    stars = "⭐" * min(conf, 5)
    icon  = {"BULLISH":"📈","BEARISH":"📉","NEUTRAL":"➡️"}.get(daily_trend,"")
    wr    = f"📈 *Win Rate:* `{round(wins/total*100)}%` _{wins}/{total}_\n" if total >= 5 else ""
    conds = "\n".join(f"   ✅ {c}" for c in conditions[:5])

    caption = (
        f"{emoji} *{symbol} — {d}*  {stars}\n"
        f"⏱ `4H`  •  {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"
        f"{icon} *Daily:* `{daily_trend}`\n\n"
        f"📌 *Setup:* {analysis.get('setup_name','')}\n"
        f"*Confluencia ({len(conditions)} sinais):*\n{conds}\n\n"
        f"💰 *Entrada:*  `{analysis.get('entry_zone','')}`\n"
        f"🛑 *Stop Loss:* `{analysis.get('stop_loss','')}`\n"
        f"🎯 *TP1:*  `{analysis.get('take_profit_1','')}`\n"
        f"🎯 *TP2:*  `{analysis.get('take_profit_2','')}`\n"
        f"📊 *R:R:*  `{analysis.get('risk_reward','')}`\n"
        f"⭐ *Confianca:* `{conf}/10`\n"
        + wr +
        f"\n📝 _{analysis.get('analysis','')}_\n\n"
        f"⚠️ _Nao e conselho financeiro. DYOR._"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(chart_path, "rb") as fh:
        r = requests.post(url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "Markdown"},
            files={"photo": fh}, timeout=30)
    log.info(f"Telegram: {r.status_code}")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY nao definida"); return

    log.info(f"=== Crypto Scanner PRO v3 — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

    for symbol in PAIRS:
        try:
            log.info(f"A escanear {symbol}...")

            df = fetch_4h(symbol)
            df = add_indicators(df)

            daily_trend = get_daily_trend(symbol)
            log.info(f"  Daily: {daily_trend}")

            supports, resistances = find_sr_levels(df)
            log.info(f"  S/R: {len(supports)} suportes, {len(resistances)} resistencias")

            patterns = detect_patterns(df)
            if patterns:
                log.info(f"  Padroes: {patterns}")

            score, conditions, direction = check_confluence(df, daily_trend, supports, resistances)
            log.info(f"  Confluencia: {score} condicoes -> {direction}")

            if not conditions:
                log.info(f"  Insuficiente ({score}/{MIN_CONFLUENCE})"); continue

            log.info(f"  Condicoes: {conditions}")

            wins, total = quick_backtest(df, direction)
            wr_info = f"{wins}/{total} ({round(wins/total*100) if total else 0}%)" if total else "sem dados"
            log.info(f"  Backtest: {wr_info}")

            # Gera gráfico em memória (figura mantida aberta)
            fig, axes, plot_df = generate_chart_fig(df, symbol, supports, resistances, daily_trend)
            img_bytes = fig_to_bytes(fig)   # para Claude — não fecha a figura
            img_b64   = base64.standard_b64encode(img_bytes).decode()

            log.info(f"  A analisar com Claude...")
            result = analyze(df, symbol, img_b64, conditions, direction,
                             daily_trend, supports, resistances, wr_info)

            conf  = result.get("confidence", 0)
            has_s = result.get("has_signal", False)
            final = result.get("direction", direction)
            log.info(f"  Claude: {final} | {conf}/10 | Sinal: {has_s}")

            if has_s and conf >= MIN_CONFIDENCE:
                # Extrair níveis de preço do texto do Claude
                entry = parse_price(result.get("entry_zone"))
                sl    = parse_price(result.get("stop_loss"))
                tp1   = parse_price(result.get("take_profit_1"))
                tp2   = parse_price(result.get("take_profit_2"))
                log.info(f"  Niveis: Entry={entry} SL={sl} TP1={tp1} TP2={tp2}")

                # Anotar o gráfico com padrões + Entry/SL/TP
                annotate_chart(fig, axes, plot_df,
                               patterns=patterns,
                               entry=entry, sl=sl, tp1=tp1, tp2=tp2,
                               direction=final)

                # Guardar gráfico anotado em disco
                chart = save_chart(fig, symbol)

                if final != direction:
                    wins, total = quick_backtest(df, final)

                log.info(f"  *** SINAL -> Telegram ***")
                send_telegram(symbol, result, chart, conditions, daily_trend, wins, total)

            else:
                plt.close(fig)
                log.info(f"  Descartado ({conf}/10 < {MIN_CONFIDENCE})")

        except Exception as e:
            log.error(f"  Erro {symbol}: {e}", exc_info=True)

    log.info("=== Scan concluido ===")


if __name__ == "__main__":
    main()
