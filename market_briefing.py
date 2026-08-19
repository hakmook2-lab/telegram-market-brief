import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
DEFAULT_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

CONFIG_PATH = Path(__file__).with_name("config.json")
HEADERS = {"User-Agent": "Mozilla/5.0"}

SECTIONS = [
    ("indices", "📊 해외 주요지수 (전일 마감 기준)"),
    ("fx", "💱 환율 / 유가"),
    ("yields", "🏦 미국채 금리"),
]


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(config):
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def fetch_price_change(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    meta = resp.json()["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("previousClose") or meta["chartPreviousClose"]
    change = price - prev
    percent = change / prev * 100
    return price, change, percent


def format_line(section, name, symbol):
    try:
        price, change, percent = fetch_price_change(symbol)
    except Exception:
        return f"{name} (데이터 없음)"
    arrow = "▲" if change >= 0 else "▼"
    if section == "indices":
        return f"{name} {price:,.2f} {arrow}{abs(change):,.2f} ({percent:+.2f}%)"
    if section == "yields":
        return f"{name} {price:.3f}% {arrow}{abs(change) * 100:.1f}bp"
    return f"{name} {price:,.2f} {arrow}{abs(change):,.2f}"


def fetch_monthly_averages(symbol):
    """Mean of daily closes per month, keyed by (year, month).

    Averaged from daily bars rather than read off monthly bars, because a
    monthly bar carries the month's closing value, not its average.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": "1y", "interval": "1d"}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]

    buckets = {}
    for ts, close in zip(result["timestamp"], result["indicators"]["quote"][0]["close"]):
        if close is None:
            continue
        date = datetime.fromtimestamp(ts, timezone.utc)
        buckets.setdefault((date.year, date.month), []).append(close)
    return {key: sum(vals) / len(vals) for key, vals in buckets.items()}


def format_trend_value(kind, value):
    if kind == "pct":
        return f"{value:.3f}%"
    return f"{value:,.0f}" if value >= 10000 else f"{value:,.2f}"


def build_trend_block(name, symbol, kind):
    try:
        averages = fetch_monthly_averages(symbol)
    except Exception:
        averages = {}
    if not averages:
        return f"📈 {name} 월평균 추이\n(데이터 없음)"

    year = max(y for y, _ in averages)
    months = sorted(m for y, m in averages if y == year)

    lines = [f"📈 {name} 월평균 추이 ({year})"]
    for month in months:
        suffix = " (진행중)" if month == months[-1] else ""
        value = format_trend_value(kind, averages[(year, month)])
        lines.append(f"{year % 100}.{month}월: {value}{suffix}")
    return "\n".join(lines)


def build_message(config=None):
    config = config or load_config()
    blocks = []
    for section, heading in SECTIONS:
        entries = config.get(section, [])
        if not entries:
            continue
        lines = [heading]
        lines += [format_line(section, name, sym) for name, sym in entries]
        blocks.append("\n".join(lines))

    for name, symbol, kind in config.get("trends", []):
        blocks.append(build_trend_block(name, symbol, kind))
    return "\n\n".join(blocks)


def send_telegram(text, chat_id=None):
    chat_id = chat_id or DEFAULT_CHAT_ID
    if not chat_id:
        raise RuntimeError("No chat id: set TELEGRAM_CHAT_ID or pass chat_id")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
    print(f"Telegram response: {resp.status_code} / {resp.text}")
    resp.raise_for_status()
    if not resp.json().get("ok"):
        raise RuntimeError(f"Telegram send failed: {resp.text}")


if __name__ == "__main__":
    message = build_message()
    print(message)
    send_telegram(message)
