"""空きスロット分析モジュール"""

from collections import Counter
from typing import List, Dict, Any, Tuple


def detect_slot_interval(slot_times: List[int], default: int = 5) -> int:
    """スロット時間リストから実際のスロット間隔を自動検出"""
    if len(slot_times) < 2:
        return default
    sorted_times = sorted(slot_times)
    gaps = [sorted_times[i+1] - sorted_times[i] for i in range(len(sorted_times) - 1) if sorted_times[i+1] > sorted_times[i]]
    if not gaps:
        return default
    detected = Counter(gaps).most_common(1)[0][0]
    if detected in (5, 10, 15, 20, 30):
        return detected
    return min((5, 10, 15, 20, 30), key=lambda x: abs(x - detected))


def count_consecutive_blocks(
    slot_times: List[int],
    required_consecutive: int = 6,
    interval: int = 5
) -> Tuple[int, List[Tuple[int, int]]]:
    """
    連続するスロットをカウントし、required_consecutive個以上の連続が何セットあるか返す

    Args:
        slot_times: 空きスロットの時間リスト（分単位、例: [565, 570, 575, ...] = 9:25, 9:30, 9:35, ...）
        required_consecutive: 必要な連続スロット数（デフォルト: 6 = 30分）
        interval: スロット間隔（分、デフォルト: 5）

    Returns:
        (ブロック数, [(開始時間, 終了時間), ...])

    例:
        slot_times = [565, 570, 575, 580, 585, 590, 600, 605, 610, 615, 620, 625]
        → 565-590が6連続、600-625が6連続 → (2, [(565, 590), (600, 625)])
    """
    if not slot_times:
        return 0, []

    sorted_times = sorted(slot_times)
    blocks = []
    current_start = sorted_times[0]
    current_count = 1
    prev_time = sorted_times[0]

    for time in sorted_times[1:]:
        if time == prev_time + interval:
            current_count += 1
        else:
            if current_count >= required_consecutive:
                blocks.append((current_start, prev_time))
            current_start = time
            current_count = 1
        prev_time = time

    # 最後のグループをチェック
    if current_count >= required_consecutive:
        blocks.append((current_start, prev_time))

    return len(blocks), blocks


def minutes_to_time_str(minutes: int) -> str:
    """分を時刻文字列に変換 (例: 565 → "9:25")"""
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}:{mins:02d}"


def format_time_range(start_minutes: int, end_minutes: int, slot_interval: int = 5) -> str:
    """時間範囲をフォーマット (例: (565, 590) → "9:25-9:55")"""
    # 終了時間はスロットの終わり時刻なので、interval分を足す
    end_actual = end_minutes + slot_interval
    return f"{minutes_to_time_str(start_minutes)}-{minutes_to_time_str(end_actual)}"


def analyze_doctor_slots(
    doctor_name: str,
    slot_times: List[int],
    required_consecutive: int = 6,
    interval: int = 5,
    threshold_minutes: int = 30
) -> Dict[str, Any]:
    """
    特定のドクターのスロットを分析

    Args:
        threshold_minutes: 空き枠判定の閾値（分）。デフォルト30分。医院・職種別に変更可能。

    Returns:
        {
            'doctor': '橋本',
            'blocks': 2,
            'times': ['9:25-9:55', '14:00-14:30'],
            'threshold_minutes': 30
        }
    """
    # 実際のスロット間隔を自動検出（分院ごとに異なる可能性: 5分/10分）
    actual_interval = detect_slot_interval(slot_times, interval)
    actual_consecutive = threshold_minutes // actual_interval  # 閾値分に必要な連続数

    # 時間範囲表示用にグループを取得
    _, block_ranges = count_consecutive_blocks(
        slot_times, actual_consecutive, actual_interval
    )

    time_strs = [
        format_time_range(start, end, actual_interval)
        for start, end in block_ranges
    ]

    # 正しい30分ブロック数を計算
    actual_blocks = count_30min_blocks(slot_times, actual_interval, actual_consecutive)

    return {
        'doctor': doctor_name,
        'blocks': actual_blocks,
        'times': time_strs,
        'threshold_minutes': threshold_minutes,
        'raw_slot_times': sorted(slot_times),
        'slot_interval': actual_interval
    }


def check_clinic_availability(
    doctor_results: List[Dict[str, Any]],
    minimum_blocks: int = 4
) -> Tuple[bool, int]:
    """
    分院全体の空き状況をチェック

    Args:
        doctor_results: 各ドクターの分析結果リスト
        minimum_blocks: 必要な最小ブロック数

    Returns:
        (条件を満たすか, 合計ブロック数)
    """
    total_blocks = sum(dr['blocks'] for dr in doctor_results)
    return total_blocks >= minimum_blocks, total_blocks


def count_30min_blocks(
    slot_times: List[int],
    slot_interval: int = 5,
    consecutive_required: int = 6
) -> int:
    """
    30分ブロック（連続スロット）の数をカウント

    連続するスロットを見つけ、その中に含まれる「30分ブロック」の数を返す。
    例: 12連続の5分スロット → 2個の30分ブロック（6スロット × 2）

    Args:
        slot_times: 空きスロットの時間リスト（分単位）
        slot_interval: スロット間隔（分）
            - dent-sys.net: 5分 → 6連続で30分
            - Stransa: 15分 → 2連続で30分
        consecutive_required: 30分に必要な連続スロット数

    Returns:
        30分ブロックの数
    """
    if not slot_times:
        return 0

    sorted_times = sorted(slot_times)
    total_blocks = 0
    current_count = 1
    prev_time = sorted_times[0]

    for time in sorted_times[1:]:
        if time == prev_time + slot_interval:
            current_count += 1
        else:
            # 連続が途切れた - これまでの連続から30分ブロック数を計算
            total_blocks += current_count // consecutive_required
            current_count = 1
        prev_time = time

    # 最後のグループからも30分ブロックを計算
    total_blocks += current_count // consecutive_required

    return total_blocks
