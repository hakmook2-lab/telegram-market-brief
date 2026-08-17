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


def fetch_monthly_closes(symbol):
    """Month-end closes keyed by (year, month), oldest first.

    Yahoo appends the running month as an extra point alongside its month-start
    bucket, so later points overwrite earlier ones within the same month — which
    leaves the current month holding its latest value rather than a stale one.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": "2y", "interval": "1mo"}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]

    closes = {}
    for ts, close in zip(result["timestamp"], result["indicators"]["quote"][0]["close"]):
        if close is None:
            continue
        date = datetime.fromtimestamp(ts, timezone.utc)
        closes[(date.year, date.month)] = close
    return closes


def build_trend_block(config):
    entry = config.get("trend")
    if not entry:
        return None
    name, symbol = entry

    try:
        closes = fetch_monthly_closes(symbol)
    except Exception:
        return f"📈 {name} 월별 추이\n(데이터 없음)"
    if not closes:
        return f"📈 {name} 월별 추이\n(데이터 없음)"

    year = max(y for y, _ in closes)
    months = sorted(m for y, m in closes if y == year)

    lines = [f"📈 {name} 월별 추이 ({year})"]
    for month in months:
        suffix = " (현재)" if month == months[-1] else ""
        lines.append(f"{year % 100}.{month}월: {closes[(year, month)]:.3f}%{suffix}")
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

    trend = build_trend_block(config)
    if trend:
        blocks.append(trend)
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
