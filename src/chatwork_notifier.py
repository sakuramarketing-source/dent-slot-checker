"""Chatwork通知モジュール: 空き枠チェック結果を送信"""
import os
import logging
import urllib.request
import urllib.parse

logger = logging.getLogger(__name__)

CHATWORK_API_URL = "https://api.chatwork.com/v2"


def send_slot_results(combined: dict) -> bool:
    """チェック結果をChatworkに送信する。

    Args:
        combined: save_resultsに渡す形式の結果dict
            {check_date, checked_at, results: [...], summary: {...}}

    Returns:
        送信成功ならTrue
    """
    token = os.environ.get('CHATWORK_API_TOKEN')
    room_id = os.environ.get('CHATWORK_ROOM_ID_SLOT')

    if not token or not room_id:
        logger.warning("CHATWORK_API_TOKEN or CHATWORK_ROOM_ID_SLOT not set, skipping notification")
        return False

    message = _format_message(combined)

    try:
        url = f"{CHATWORK_API_URL}/rooms/{room_id}/messages"
        data = urllib.parse.urlencode({
            "body": message,
            "self_unread": 1,
        }).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={"X-ChatWorkToken": token},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info(f"Chatwork送信完了: room={room_id}, status={resp.status}")
        return True
    except Exception as e:
        logger.error(f"Chatwork送信失敗: {e}")
        return False


def _format_message(combined: dict) -> str:
    """結果を空きあり分院のみのChatworkメッセージに整形"""
    check_date = combined.get('check_date', '不明')
    summary = combined.get('summary', {})
    total = summary.get('total_clinics', 0)
    with_avail = summary.get('clinics_with_availability', 0)
    results = combined.get('results', [])

    lines = [
        f"[info][title]空き枠チェック結果（{check_date}分）[/title]",
        f"空きあり: {with_avail}/{total}分院",
        "",
    ]

    available = [r for r in results if r.get('result')]
    if available:
        for r in available:
            clinic = r.get('clinic', '不明')
            blocks = r.get('total_30min_blocks', 0)
            lines.append(f"○ {clinic}: {blocks}ブロック")
    else:
        lines.append("空き枠のある分院はありませんでした。")

    lines.append("")
    lines.append("詳細: https://checker.sakurashika-g.jp")
    lines.append("[/info]")

    return "\n".join(lines)
