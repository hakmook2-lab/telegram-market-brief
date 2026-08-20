# -*- coding: utf-8 -*-
"""자체테스트_브리핑.py

market_briefing.py를 네트워크 없이 검증한다.
야후를 가짜로 세워놓고, 정상 응답·잘린 응답·오류 응답에서 각각 어떻게 나가는지 본다.

실행:  python 자체테스트_브리핑.py
"""
import os
import sys
import calendar
import datetime as dt

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

os.environ.setdefault("TELEGRAM_TOKEN", "테스트용_더미")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_briefing as mb  # noqa: E402

mb.time.sleep = lambda *_a, **_k: None      # 재시도 백오프로 테스트가 늘어지지 않게

results = []


def check(name, cond, detail=""):
    st = "PASS" if cond else "FAIL"
    results.append((st, name, detail))
    print(f"[{st}] {name} {detail}")


# ─────────────────────────────────────────────────────────────
# 가짜 야후
# ─────────────────────────────────────────────────────────────
def make_payload(months, value_of, days_per_month=None):
    """months = [(연,월), ...] 각 달의 영업일(월~금)을 채운 일봉 payload를 만든다."""
    ts, closes = [], []
    for (year, month) in months:
        made = 0
        limit = (days_per_month or {}).get((year, month))
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            date = dt.date(year, month, day)
            if date.weekday() >= 5:
                continue
            if limit is not None and made >= limit:
                break
            ts.append(int(dt.datetime(year, month, day, tzinfo=dt.timezone.utc).timestamp()))
            closes.append(value_of(year, month))
            made += 1
    return {"timestamp": ts,
            "indicators": {"quote": [{"close": closes}]},
            "meta": {"regularMarketPrice": closes[-1] if closes else 0,
                     "previousClose": closes[0] if closes else 0}}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = "fake"

    def json(self):
        return {"chart": {"result": [self._payload]}}


class FakeSession:
    """호출될 때마다 queue에서 하나씩 꺼내 돌려준다(다 쓰면 마지막 걸 반복)."""

    def __init__(self, queue):
        self.queue = list(queue)
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        item = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        if isinstance(item, Exception):
            raise item
        return item


FULL_MONTHS = [(2025, m) for m in range(8, 13)] + [(2026, m) for m in range(1, 9)]
# 26.7월=4.592%, 26.8월=4.673% 처럼 달마다 다른 값
def yield_value(year, month):
    return 4.0 + month * 0.08 if year == 2026 else 3.9


FULL = FakeResponse(make_payload(FULL_MONTHS, yield_value))
# 사고 당시 야후가 준 것: 7·8월 두 달치뿐. 게다가 7월은 며칠만.
TRUNCATED = FakeResponse(make_payload([(2026, 7), (2026, 8)], yield_value,
                                      days_per_month={(2026, 7): 3}))


# ── 1) 정상 응답 ────────────────────────────────────────────
mb.SESSION = FakeSession([FULL])
block = mb.build_trend_block("미국채10년", "%5ETNX", "pct")
check("정상 응답이면 2026년 8개월 전부 출력",
      all(f"26.{m}월" in block for m in range(1, 9)),
      f"(줄수={len(block.splitlines())})")
check("정상 응답에는 누락 경고가 없음", "누락" not in block)
check("마지막 달만 (진행중)", block.count("(진행중)") == 1)

# ── 2) 【회귀】 2026-08-20 사고: 잘린 응답 ──────────────────
# 야후가 계속 두 달치만 준다. 예전 코드는 이걸 그대로 믿고
#   "26.7월: 4.652%" 를 정상 월평균인 척 내보냈다(실제 7월 평균은 4.592%).
mb.SESSION = FakeSession([TRUNCATED])
block = mb.build_trend_block("미국채10년", "%5ETNX", "pct")
print("  --- 잘린 응답일 때 실제 출력 ---")
for ln in block.splitlines():
    print("   " + ln)
check("[회귀] 잘린 응답이면 재시도한다",
      mb.SESSION.calls > 1, f"(호출={mb.SESSION.calls}회)")
check("[회귀] 표본 모자란 달은 숫자를 내보내지 않음(틀린 평균 방지)",
      "자료부족" in block and "26.7월: 4." not in block)
check("[회귀] 빠진 달을 조용히 넘기지 않고 경고로 남김",
      "누락" in block and all(f"{m}월" in block for m in range(1, 7)))

# ── 3) 잘렸다가 재시도에서 정상이 오면 정상 출력 ────────────
mb.SESSION = FakeSession([TRUNCATED, FULL])
block = mb.build_trend_block("미국채10년", "%5ETNX", "pct")
check("재시도에서 정상 응답이 오면 정상 출력",
      "자료부족" not in block and "누락" not in block
      and all(f"26.{m}월" in block for m in range(1, 9)),
      f"(호출={mb.SESSION.calls}회)")

# ── 4) HTTP 오류 / 예외 ────────────────────────────────────
mb.SESSION = FakeSession([FakeResponse(None, status=429)])
block = mb.build_trend_block("미국채10년", "%5ETNX", "pct")
check("429가 계속되면 재시도 후 사유를 밝힘",
      "데이터 없음" in block and "429" in block, f"(호출={mb.SESSION.calls}회)")

mb.SESSION = FakeSession([ConnectionError("연결 끊김")])
block = mb.build_trend_block("미국채10년", "%5ETNX", "pct")
check("예외가 계속되면 재시도 후 사유를 밝힘",
      "데이터 없음" in block and "연결 끊김" in block)

mb.SESSION = FakeSession([FakeResponse(None, status=429), FULL])
block = mb.build_trend_block("미국채10년", "%5ETNX", "pct")
check("429 뒤 정상이 오면 복구됨", "26.1월" in block and "데이터 없음" not in block)

# ── 5) 시세 한 줄 (format_line) ────────────────────────────
mb.SESSION = FakeSession([FULL])
line = mb.format_line("yields", "미국채10년", "%5ETNX")
check("금리 줄은 %와 bp로 표기", line.startswith("미국채10년") and "%" in line and "bp" in line,
      f"({line})")

mb.SESSION = FakeSession([FakeResponse(None, status=500)])
line = mb.format_line("yields", "미국채10년", "%5ETNX")
check("시세 조회 실패해도 브리핑 전체가 죽지 않음", "데이터 없음" in line, f"({line})")
check("시세도 실패 시 재시도함", mb.SESSION.calls > 1, f"(호출={mb.SESSION.calls}회)")

# ── 6) 값 표기 ─────────────────────────────────────────────
check("퍼센트는 소수 3자리", mb.format_trend_value("pct", 4.59247) == "4.592%",
      f"({mb.format_trend_value('pct', 4.59247)})")
check("1만 이상은 정수 표기", mb.format_trend_value("num", 90464.3) == "90,464")
check("1만 미만은 소수 2자리", mb.format_trend_value("num", 4730.857) == "4,730.86")

print("\n" + "=" * 46)
fails = [r for r in results if r[0] == "FAIL"]
print(f"총 {len(results)}건 중 실패 {len(fails)}건")
for _, n, d in fails:
    print(f"  - {n} {d}")
sys.exit(1 if fails else 0)
