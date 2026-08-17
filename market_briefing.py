import json
import os
import sys
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
