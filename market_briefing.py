import os
import sys
import requests

sys.stdout.reconfigure(encoding="utf-8")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {"User-Agent": "Mozilla/5.0"}

INDEX_SYMBOLS = [
    ("다우산업", "%5EDJI"),
    ("다우운송", "%5EDJT"),
    ("나스닥종합", "%5EIXIC"),
    ("나스닥100", "%5ENDX"),
    ("S&P500", "%5EGSPC"),
    ("필라델피아반도체", "%5ESOX"),
]

FX_SYMBOLS = [
    ("USD/KRW", "KRW=X"),
    ("USD/JPY", "JPY=X"),
    ("WTI", "CL=F"),
]

YIELD_SYMBOLS = [
    ("미국채10년", "%5ETNX"),
    ("미국채30년", "%5ETYX"),
]


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


def format_index_line(name, symbol):
    try:
        price, change, percent = fetch_price_change(symbol)
        arrow = "▲" if change >= 0 else "▼"
        return f"{name} {price:,.2f} {arrow}{abs(change):,.2f} ({percent:+.2f}%)"
    except Exception:
        return f"{name} (데이터 없음)"


def format_fx_line(name, symbol):
    try:
        price, change, _ = fetch_price_change(symbol)
        arrow = "▲" if change >= 0 else "▼"
        return f"{name} {price:,.2f} {arrow}{abs(change):,.2f}"
    except Exception:
        return f"{name} (데이터 없음)"


def format_yield_line(name, symbol):
    try:
        price, change, _ = fetch_price_change(symbol)
        arrow = "▲" if change >= 0 else "▼"
        bp = abs(change) * 100
        return f"{name} {price:.3f}% {arrow}{bp:.1f}bp"
    except Exception:
        return f"{name} (데이터 없음)"


def build_message():
    lines = ["📊 해외 주요지수 (전일 마감 기준)"]
    lines += [format_index_line(name, sym) for name, sym in INDEX_SYMBOLS]
    lines.append("")
    lines.append("💱 환율 / 유가")
    lines += [format_fx_line(name, sym) for name, sym in FX_SYMBOLS]
    lines.append("")
    lines.append("🏦 미국채 금리")
    lines += [format_yield_line(name, sym) for name, sym in YIELD_SYMBOLS]
    return "\n".join(lines)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    print(f"Telegram response: {resp.status_code} / {resp.text}")
    resp.raise_for_status()
    if not resp.json().get("ok"):
        raise RuntimeError(f"Telegram send failed: {resp.text}")


if __name__ == "__main__":
    message = build_message()
    print(message)
    send_telegram(message)
