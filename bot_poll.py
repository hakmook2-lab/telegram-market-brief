"""Polls Telegram for commands and applies them to config.json.

Run on a schedule. Updates are confirmed back to Telegram via the offset
parameter, so each message is only ever seen once — no local state to keep.
"""

import sys

import requests

import market_briefing as mb

sys.stdout.reconfigure(encoding="utf-8")

API = f"https://api.telegram.org/bot{mb.TELEGRAM_TOKEN}"

# Chat ids allowed to issue commands. The bot is publicly reachable by anyone
# who finds its username, so every incoming message is checked against this.
ALLOWED_CHATS = {"-5385031872", "7751216143"}

SECTION_ALIASES = {
    "지수": "indices", "index": "indices", "indices": "indices",
    "환율": "fx", "유가": "fx", "fx": "fx",
    "금리": "yields", "yield": "yields", "yields": "yields",
}
SECTION_LABELS = {"indices": "지수", "fx": "환율/원자재", "yields": "금리"}

HELP = """📖 사용 가능한 명령

/지금 — 지금 바로 브리핑 발송
/목록 — 현재 감시 항목 보기
/추가 <분류> <이름> <심볼> — 항목 추가
/삭제 <이름> — 항목 제거
/도움 — 이 안내

분류: 지수 / 환율 / 금리

예시)
/추가 지수 나스닥바이오 ^NBI
/추가 환율 USD/EUR EUR=X
/삭제 다우운송

심볼은 야후 파이낸스 기준입니다.
(finance.yahoo.com 에서 검색하면 확인 가능)"""


def get_updates(offset=None):
    params = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(f"{API}/getUpdates", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("result", [])


def find_entry(config, name):
    """Return (section, index) of the entry matching name, case-insensitively."""
    target = name.casefold()
    for section, _ in mb.SECTIONS:
        for i, (entry_name, _sym) in enumerate(config.get(section, [])):
            if entry_name.casefold() == target:
                return section, i
    return None, None


def cmd_list(config):
    blocks = []
    for section, _ in mb.SECTIONS:
        entries = config.get(section, [])
        if not entries:
            continue
        lines = [f"[{SECTION_LABELS[section]}]"]
        lines += [f"· {name} ({sym.replace('%5E', '^')})" for name, sym in entries]
        blocks.append("\n".join(lines))
    return "📋 현재 감시 항목\n\n" + "\n\n".join(blocks)


def cmd_add(config, args):
    if len(args) < 3:
        return "형식이 맞지 않습니다.\n예) /추가 지수 나스닥바이오 ^NBI", False
    section = SECTION_ALIASES.get(args[0].casefold())
    if not section:
        return "분류는 지수 / 환율 / 금리 중 하나여야 합니다.", False

    name, symbol = args[1], args[2].replace("^", "%5E")
    if find_entry(config, name)[0]:
        return f"'{name}' 은(는) 이미 등록되어 있습니다.", False

    try:
        price, _, _ = mb.fetch_price_change(symbol)
    except Exception:
        return f"'{args[2]}' 심볼로 시세를 가져오지 못했습니다.\n야후 파이낸스에서 심볼을 확인해 주세요.", False

    config.setdefault(section, []).append([name, symbol])
    return f"✅ 추가했습니다.\n{SECTION_LABELS[section]} · {name} ({args[2]}) — 현재 {price:,.2f}", True


def cmd_remove(config, args):
    if not args:
        return "삭제할 이름을 적어주세요.\n예) /삭제 다우운송", False
    name = args[0]
    section, index = find_entry(config, name)
    if not section:
        return f"'{name}' 을(를) 찾지 못했습니다. /목록 으로 확인해 주세요.", False
    removed, _ = config[section].pop(index)
    return f"🗑 삭제했습니다. ({SECTION_LABELS[section]} · {removed})", True


def handle(chat_id, text, config):
    """Returns True if config was modified."""
    parts = text.split()
    command = parts[0].lstrip("/").split("@")[0].casefold()
    args = parts[1:]

    if command in ("도움", "help", "start"):
        mb.send_telegram(HELP, chat_id)
        return False

    if command in ("지금", "now"):
        mb.send_telegram(mb.build_message(config), chat_id)
        return False

    if command in ("목록", "list"):
        mb.send_telegram(cmd_list(config), chat_id)
        return False

    if command in ("추가", "add"):
        reply, changed = cmd_add(config, args)
    elif command in ("삭제", "제거", "remove", "del"):
        reply, changed = cmd_remove(config, args)
    else:
        reply, changed = f"모르는 명령입니다: /{command}\n/도움 을 입력해 보세요.", False

    mb.send_telegram(reply, chat_id)
    return changed


def main():
    updates = get_updates()
    if not updates:
        print("no updates")
        return

    config = mb.load_config()
    changed = False

    for update in updates:
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        chat_id = str(message.get("chat", {}).get("id"))
        text = (message.get("text") or "").strip()
        if chat_id not in ALLOWED_CHATS or not text.startswith("/"):
            continue
        print(f"command from {chat_id}: {text}")
        try:
            changed |= handle(chat_id, text, config)
        except Exception as exc:  # one bad command must not stall the queue
            print(f"command failed: {exc}")

    # Acknowledge everything we just read so it is not delivered again.
    get_updates(offset=updates[-1]["update_id"] + 1)

    if changed:
        mb.save_config(config)
        print("config updated")


if __name__ == "__main__":
    main()
