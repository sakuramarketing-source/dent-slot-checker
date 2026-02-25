"""歯科予約空き状況チェッカー メインモジュール

dent-sys.net および Stransa (Apotool & Box) に対応
"""

import asyncio
import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any

from .config_loader import (
    load_config,
    get_enabled_clinics,
    get_exclude_patterns,
    get_slot_settings
)
from .scraper import scrape_all_clinics
from .scraper_stransa import scrape_all_stransa_clinics
from .slot_analyzer import analyze_doctor_slots, check_clinic_availability, count_30min_blocks
from .output_writer import save_results, format_summary


# ロギング設定
def setup_logging(log_dir: Path = None):
    """ロギングを設定"""
    if log_dir is None:
        log_dir = Path(__file__).parent.parent / 'logs'

    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = log_dir / f'slot_checker_{timestamp}.log'

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )


def analyze_results(
    scrape_results: Dict[str, Dict[str, List[int]]],
    slot_settings: Dict[str, int],
    system_type: str = 'dent-sys',
    staff_by_clinic: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    スクレイピング結果を分析してチェック結果を生成

    Args:
        scrape_results: {分院名: {先生名/チェア名: [スロット時間のリスト]}}
        slot_settings: スロット設定
        system_type: システムタイプ ('dent-sys' or 'stransa')
        staff_by_clinic: 医院別スタッフ設定（職種分類・閾値）

    Returns:
        分析結果
    """
    check_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    checked_at = datetime.now().isoformat()

    clinic_results = []
    clinics_with_availability = 0

    minimum_blocks = slot_settings['minimum_blocks_required']
    staff_by_clinic = staff_by_clinic or {}

    # システムタイプによってスロット設定を変更
    if system_type == 'stransa':
        # Stransa: 15分刻み、2連続で30分
        consecutive_required = 2
        interval = 15
    else:
        # dent-sys.net: 5分刻み、6連続で30分
        consecutive_required = slot_settings['consecutive_slots_required']
        interval = slot_settings['slot_interval_minutes']

    for clinic_name, doctor_slots in scrape_results.items():
        doctor_results = []

        # 医院別・職種別の閾値を取得
        clinic_config = staff_by_clinic.get(clinic_name, {})
        doctors_set = set(clinic_config.get('doctors', []))
        hygienists_set = set(clinic_config.get('hygienists', []))
        thresholds = clinic_config.get('slot_threshold', {})
        dr_threshold = thresholds.get('doctor', 30)
        dh_threshold = thresholds.get('hygienist', 30)

        for doctor_name, slot_times in doctor_slots.items():
            # スタッフの職種に応じた閾値を決定
            if doctor_name in doctors_set:
                threshold = dr_threshold
            elif doctor_name in hygienists_set:
                threshold = dh_threshold
            else:
                threshold = 30  # 未分類はデフォルト30分

            analysis = analyze_doctor_slots(
                doctor_name,
                slot_times,
                consecutive_required,
                interval,
                threshold_minutes=threshold
            )
            if analysis['blocks'] > 0:
                doctor_results.append(analysis)

        is_available, total_blocks = check_clinic_availability(
            doctor_results, minimum_blocks
        )

        if is_available:
            clinics_with_availability += 1

        clinic_results.append({
            'clinic': clinic_name,
            'system': system_type,
            'result': is_available,
            'total_30min_blocks': total_blocks,
            'details': doctor_results
        })

    return {
        'check_date': check_date,
        'checked_at': checked_at,
        'results': clinic_results,
        'summary': {
            'total_clinics': len(clinic_results),
            'clinics_with_availability': clinics_with_availability
        }
    }


async def main_async(
    headless: bool = True,
    output_formats: List[str] = None,
    system_filter: str = None
) -> Dict[str, Any]:
    """
    メイン処理（非同期）

    Args:
        headless: ヘッドレスモードで実行するか
        output_formats: 出力形式リスト
        system_filter: 対象システム ('dent-sys', 'stransa', None=全て)

    Returns:
        チェック結果
    """
    logger = logging.getLogger(__name__)

    if output_formats is None:
        output_formats = ['json', 'csv']

    # 設定読み込み
    config = load_config()
    exclude_patterns = get_exclude_patterns(config)
    slot_settings = get_slot_settings(config)

    # スタッフ設定（職種分類・閾値）読み込み
    staff_by_clinic = config.get('staff_categories', {}) or {}
    # staff_rules.yaml の staff_by_clinic を直接読み込む
    config_path_sr = Path(__file__).parent.parent / 'config' / 'staff_rules.yaml'
    if config_path_sr.exists():
        import yaml
        with open(config_path_sr, 'r', encoding='utf-8') as f:
            sr_data = yaml.safe_load(f) or {}
        staff_by_clinic = sr_data.get('staff_by_clinic', {})

    # システム別に分院を分類
    dent_sys_clinics = [
        c for c in config.get('dent_sys_clinics', [])
        if c.get('enabled', True)
    ]
    stransa_clinics = [
        c for c in config.get('stransa_clinics', [])
        if c.get('enabled', True)
    ]

    # フィルタを適用
    if system_filter == 'dent-sys':
        stransa_clinics = []
    elif system_filter == 'stransa':
        dent_sys_clinics = []

    logger.info(f"dent-sys.net 分院数: {len(dent_sys_clinics)}")
    logger.info(f"Stransa 分院数: {len(stransa_clinics)}")
    logger.info(f"除外パターン: {exclude_patterns}")
    logger.info(f"スロット設定: {slot_settings}")

    # 設定ファイルパス
    config_path = Path(__file__).parent.parent / 'config'

    all_results = []
    total_clinics = 0
    clinics_with_availability = 0

    # dent-sys + Stransa を並列スクレイピング
    scrape_tasks = []
    task_labels = []

    if dent_sys_clinics:
        logger.info("=== dent-sys.net スクレイピング開始 ===")
        scrape_tasks.append(scrape_all_clinics(
            dent_sys_clinics,
            exclude_patterns,
            slot_settings['slot_interval_minutes'],
            headless,
            str(config_path)
        ))
        task_labels.append('dent-sys')

    if stransa_clinics:
        logger.info("=== Stransa スクレイピング開始 ===")
        scrape_tasks.append(scrape_all_stransa_clinics(
            stransa_clinics,
            headless
        ))
        task_labels.append('stransa')

    # 両システム同時実行
    scrape_results_list = await asyncio.gather(*scrape_tasks, return_exceptions=True)

    for label, scrape_result in zip(task_labels, scrape_results_list):
        if isinstance(scrape_result, Exception):
            logger.error(f"{label} スクレイピング失敗: {scrape_result}")
            continue
        system_type = label
        analysis = analyze_results(scrape_result, slot_settings, system_type, staff_by_clinic)
        all_results.extend(analysis['results'])
        total_clinics += analysis['summary']['total_clinics']
        clinics_with_availability += analysis['summary']['clinics_with_availability']

    # 統合結果を作成
    check_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    checked_at = datetime.now().isoformat()

    combined_results = {
        'check_date': check_date,
        'checked_at': checked_at,
        'results': all_results,
        'summary': {
            'total_clinics': total_clinics,
            'clinics_with_availability': clinics_with_availability
        }
    }

    # 結果出力
    output_dir = Path(__file__).parent.parent / 'output'
    saved_files = save_results(combined_results, output_dir, output_formats)

    for f in saved_files:
        logger.info(f"結果保存: {f}")

    # サマリー出力
    print(format_summary(combined_results))

    return combined_results


def main():
    """メイン関数（エントリーポイント）"""
    parser = argparse.ArgumentParser(
        description='歯科予約空き状況チェッカー (dent-sys.net / Stransa対応)'
    )
    parser.add_argument(
        '--no-headless',
        action='store_true',
        help='ブラウザを表示して実行（デバッグ用）'
    )
    parser.add_argument(
        '--format',
        choices=['json', 'csv', 'both'],
        default='both',
        help='出力形式（デフォルト: both）'
    )
    parser.add_argument(
        '--system',
        choices=['dent-sys', 'stransa', 'all'],
        default='all',
        help='対象システム（デフォルト: all）'
    )

    args = parser.parse_args()

    setup_logging()

    headless = not args.no_headless

    if args.format == 'both':
        output_formats = ['json', 'csv']
    else:
        output_formats = [args.format]

    system_filter = None if args.system == 'all' else args.system

    # 非同期処理を実行
    results = asyncio.run(main_async(headless, output_formats, system_filter))

    # チェック完了 → 常に0（空きの有無に関わらず正常終了）
    sys.exit(0)


if __name__ == '__main__':
    main()
