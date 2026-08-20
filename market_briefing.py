import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
DEFAULT_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

CONFIG_PATH = Path(__file__).with_name("config.json")
HEADERS = {"User-Agent": "Mozilla/5.0"}
SESSION = requests.Session()

# 야후가 1년치를 달라는데 몇 달치만 주는 일이 있다(아래 fetch_monthly_series 참고).
# 완결된 달인데 영업일이 이보다 적으면 그 달 평균은 믿을 수 없다고 본다.
# 미국 증시 영업일은 한 달에 보통 19~23일이라 15일이면 충분히 여유 있는 하한이다.
MIN_TRADING_DAYS = 15
# 1년치 요청이 정상이면 13개 달에 걸친다(양끝 부분달 포함). 11개월 미만이면 잘린 것.
MIN_MONTHS_COVERED = 11

SECTIONS = [
    ("indices", "📊 해외 주요지수 (전일 마감 기준)"),
    ("fx", "💱 환율 / 원자재"),
    ("yields", "🏦 미국채 금리"),
]


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(config):
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def fetch_chart(symbol, params=None, attempts=4, accept=None):
    """야후 차트 API 호출. 실패하거나 응답이 부실하면 물러났다 다시 친다.

    이 브리핑은 GitHub Actions(ubuntu 러너)에서 돌아가는데, 야후는 클라우드 IP를
    자주 스로틀한다. 그때 429/5xx만 주는 게 아니라 **200으로 짧은 데이터를 주는**
    경우가 있어서, 호출부가 넘겨준 accept()로 내용까지 확인하고 재시도한다.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    delay, last = 2, "이유 미상"
    for attempt in range(attempts):
        try:
            resp = SESSION.get(url, params=params, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                payload = resp.json()["chart"]["result"][0]
                if accept is None or accept(payload):
                    return payload
                last = "응답이 불완전함(야후 스로틀 추정)"
            else:
                last = f"HTTP {resp.status_code}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        if attempt < attempts - 1:
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(last)


def fetch_price_change(symbol):
    meta = fetch_chart(symbol)["meta"]
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


def _bucket_by_month(payload):
    """일봉을 (연,월) 바구니에 담는다."""
    buckets = {}
    closes = payload["indicators"]["quote"][0]["close"]
    for ts, close in zip(payload["timestamp"], closes):
        if close is None:
            continue
        date = datetime.fromtimestamp(ts, timezone.utc)
        buckets.setdefault((date.year, date.month), []).append(close)
    return buckets


def fetch_monthly_series(symbol):
    """월평균을 {(연,월): (평균, 표본일수)} 로 돌려준다.

    월봉이 아니라 일봉을 평균낸다 — 월봉이 담는 건 그 달의 '종가'지 '평균'이 아니다.

    ⚠️ 표본일수를 같이 돌려주는 이유 (2026-08-20 사고)
       야후가 1년치 요청에 두 달치만 200 OK로 돌려준 적이 있다. 예전 코드는 그걸
       그대로 믿어서 텔레그램에 이렇게 나갔다:

           📈 미국채10년 월평균 추이 (2026)
           26.7월: 4.652%          ← 실제 7월 평균은 4.592%
           26.8월: 4.673% (진행중)

       1~6월이 통째로 사라진 것도 문제지만, 더 나쁜 건 **7월 값이 틀렸다는 것**이다.
       잘려 들어온 7월 며칠치만으로 평균을 내고서는 정상 월평균인 척 표시했다.
       빠진 건 눈에 띄지만 틀린 숫자는 눈에 안 띈다. 그래서 표본일수를 들고 다니며
       모자란 달은 값을 내보내지 않는다.
    """
    params = {"range": "1y", "interval": "1d"}

    def looks_complete(payload):
        return len(_bucket_by_month(payload)) >= MIN_MONTHS_COVERED

    try:
        payload = fetch_chart(symbol, params, accept=looks_complete)
    except RuntimeError:
        # 여러 번 쳐도 계속 짧게 준다. 그래도 통째로 버리지는 않는다 —
        # 받은 만큼은 쓰고, 어디가 모자란지는 build_trend_block이 밝힌다.
        payload = fetch_chart(symbol, params, attempts=1)
    return {key: (sum(vals) / len(vals), len(vals))
            for key, vals in _bucket_by_month(payload).items()}


def format_trend_value(kind, value):
    if kind == "pct":
        return f"{value:.3f}%"
    return f"{value:,.0f}" if value >= 10000 else f"{value:,.2f}"


def build_trend_block(name, symbol, kind):
    try:
        series = fetch_monthly_series(symbol)
    except Exception as exc:
        return f"📈 {name} 월평균 추이\n(데이터 없음 — {exc})"

    year = max(y for y, _ in series)
    months = sorted(m for y, m in series if y == year)
    current = months[-1]

    lines = [f"📈 {name} 월평균 추이 ({year})"]
    for month in months:
        mean, days = series[(year, month)]
        if month != current and days < MIN_TRADING_DAYS:
            # 이미 끝난 달인데 영업일이 모자라다 = 야후가 잘라 준 것.
            # 틀린 평균을 정상인 척 내보내느니 모자라다고 밝힌다.
            lines.append(f"{year % 100}.{month}월: (자료부족 {days}일)")
            continue
        suffix = " (진행중)" if month == current else ""
        lines.append(f"{year % 100}.{month}월: {format_trend_value(kind, mean)}{suffix}")

    # 아예 빠진 달도 조용히 넘어가지 않는다. 07:48 사고 때 1~6월이 이렇게 사라졌다.
    missing = [m for m in range(1, current) if (year, m) not in series]
    if missing:
        gap = ", ".join(f"{m}월" for m in missing)
        lines.append(f"⚠️ {gap} 자료 누락(야후 응답 불완전)")
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
