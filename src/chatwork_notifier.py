"""Chatwork通知モジュール: 空き枠チェック結果を送信"""
import os
import re
import copy
import logging
import urllib.request
import urllib.parse

import yaml

from src.slot_analyzer import count_30min_blocks, count_consecutive_blocks

logger = logging.getLogger(__name__)

CHATWORK_API_URL = "https://api.chatwork.com/v2"


def send_slot_results(combined: dict, config_path: str) -> bool:
    """チェック結果にフィルタを適用してChatworkに送信する。

    Args:
        combined: save_resultsに渡す形式の結果dict
        config_path: config/ディレクトリのパス（staff_rules.yaml等を読む）

    Returns:
        送信成功ならTrue
    """
    token = os.environ.get('CHATWORK_API_TOKEN')
    room_id = os.environ.get('CHATWORK_ROOM_ID_SLOT')

    if not token or not room_id:
        logger.warning("CHATWORK_API_TOKEN or CHATWORK_ROOM_ID_SLOT not set, skipping notification")
        return False

    # ダッシュボードと同じフィルタを適用
    filtered = copy.deepcopy(combined)
    staff_rules = _load_staff_rules(config_path)
    settings = _load_clinics_settings(config_path)
    _apply_category_classification(filtered, staff_rules)
    _apply_web_booking_filter(filtered, staff_rules, settings)

    message = _format_message(filtered)

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


def _load_staff_rules(config_path: str) -> dict:
    path = os.path.join(config_path, 'staff_rules.yaml')
    if not os.path.exists(path):
        return {'staff_by_clinic': {}}
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {'staff_by_clinic': {}}


def _load_clinics_settings(config_path: str) -> dict:
    path = os.path.join(config_path, 'clinics.yaml')
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    return config.get('settings', {})


def _strip_suffix(name):
    return re.sub(r'\(\d+\)$', '', name).strip()


def _apply_category_classification(data: dict, staff_rules: dict):
    """職種分類+閾値再計算（main.pyと同等ロジック）"""
    staff_by_clinic = staff_rules.get('staff_by_clinic', {})

    for result in data.get('results', []):
        clinic_name = result.get('clinic', '')
        clinic_config = staff_by_clinic.get(clinic_name, {})
        doctors = set(clinic_config.get('doctors', []))
        hygienists = set(clinic_config.get('hygienists', []))
        thresholds = clinic_config.get('slot_threshold', {})
        dr_threshold = thresholds.get('doctor', 30)
        dh_threshold = thresholds.get('hygienist', 30)

        for detail in result.get('details', []):
            staff_name = detail.get('doctor', '')
            base_name = _strip_suffix(staff_name)
            if staff_name in doctors or base_name in doctors:
                detail['category'] = 'doctor'
                _recalculate_detail(detail, dr_threshold)
            elif staff_name in hygienists or base_name in hygienists:
                detail['category'] = 'hygienist'
                _recalculate_detail(detail, dh_threshold)
            else:
                detail['category'] = 'unknown'
                _recalculate_detail(detail, 30)


def _recalculate_detail(detail: dict, threshold: int):
    """raw_slot_timesがあれば指定閾値で枠数を再計算"""
    raw_times = detail.get('raw_slot_times')
    if not raw_times:
        return
    interval = detail.get('slot_interval', 5)
    consec = threshold // interval
    detail['blocks'] = count_30min_blocks(raw_times, interval, consec)
    detail['threshold_minutes'] = threshold


def _apply_web_booking_filter(data: dict, staff_rules: dict, settings: dict):
    """web_bookingフィルタ（main.pyと同等ロジック）"""
    staff_by_clinic = staff_rules.get('staff_by_clinic', {})
    min_blocks = settings.get('minimum_blocks_required', 4)
    clinics_with_availability = 0

    for result in data.get('results', []):
        clinic_name = result.get('clinic', '')
        clinic_config = staff_by_clinic.get(clinic_name, {})
        web_booking = clinic_config.get('web_booking', [])

        if not web_booking:
            result['details'] = []
            result['total_30min_blocks'] = 0
            result['result'] = False
            continue

        web_booking_set = set(web_booking)
        filtered = [
            d for d in result.get('details', [])
            if d.get('doctor', '') in web_booking_set or _strip_suffix(d.get('doctor', '')) in web_booking_set
        ]
        result['details'] = filtered
        total = sum(d.get('blocks', 0) for d in filtered)
        result['total_30min_blocks'] = total
        result['result'] = total >= min_blocks

        if result['result']:
            clinics_with_availability += 1

    if 'summary' in data:
        data['summary']['clinics_with_availability'] = clinics_with_availability


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
