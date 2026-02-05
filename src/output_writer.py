"""出力処理モジュール"""

import json
import csv
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

from src.gcs_storage import save_result_to_gcs, is_gcs_enabled

logger = logging.getLogger(__name__)


def write_json(results: Dict[str, Any], output_path: Path) -> None:
    """JSON形式で出力"""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())


def write_csv(results: Dict[str, Any], output_path: Path) -> None:
    """CSV形式で出力"""
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)

        # ヘッダー
        writer.writerow([
            'チェック日',
            '分院名',
            '結果',
            '30分ブロック数',
            'ドクター',
            'ブロック数',
            '時間帯'
        ])

        check_date = results.get('check_date', '')

        for clinic_result in results.get('results', []):
            clinic_name = clinic_result.get('clinic', '')
            result = '○' if clinic_result.get('result', False) else '×'
            total_blocks = clinic_result.get('total_30min_blocks', 0)

            details = clinic_result.get('details', [])
            if details:
                for detail in details:
                    doctor = detail.get('doctor', '')
                    blocks = detail.get('blocks', 0)
                    times = ', '.join(detail.get('times', []))
                    writer.writerow([
                        check_date,
                        clinic_name,
                        result,
                        total_blocks,
                        doctor,
                        blocks,
                        times
                    ])
            else:
                writer.writerow([
                    check_date,
                    clinic_name,
                    result,
                    total_blocks,
                    '',
                    '',
                    ''
                ])


def create_output_filename(output_dir: Path, check_date: str, extension: str) -> Path:
    """出力ファイル名を生成"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"slot_check_{check_date}_{timestamp}.{extension}"
    return output_dir / filename


def save_results(
    results: Dict[str, Any],
    output_dir: Path = None,
    formats: List[str] = None
) -> List[Path]:
    """
    結果を保存

    Args:
        results: チェック結果
        output_dir: 出力ディレクトリ
        formats: 出力形式リスト ['json', 'csv']

    Returns:
        作成されたファイルパスのリスト
    """
    if output_dir is None:
        output_dir = Path(__file__).parent.parent / 'output'

    if formats is None:
        formats = ['json', 'csv']

    output_dir.mkdir(parents=True, exist_ok=True)

    check_date = results.get('check_date', datetime.now().strftime('%Y-%m-%d'))
    created_files = []

    for fmt in formats:
        output_path = create_output_filename(output_dir, check_date.replace('-', ''), fmt)

        if fmt == 'json':
            write_json(results, output_path)

            # GCSにも保存（Cloud Run用）
            if is_gcs_enabled():
                filename = output_path.name
                if save_result_to_gcs(filename, results):
                    logger.info(f"結果ファイルをGCSに保存: {filename}")
                else:
                    logger.warning(f"GCS保存失敗: {filename}")

        elif fmt == 'csv':
            write_csv(results, output_path)

        created_files.append(output_path)

    return created_files


def format_summary(results: Dict[str, Any]) -> str:
    """結果サマリーをフォーマット（コンソール出力用）"""
    summary = results.get('summary', {})
    lines = [
        "=" * 50,
        "チェック結果サマリー",
        "=" * 50,
        f"チェック日: {results.get('check_date', '')}",
        f"実行時刻: {results.get('checked_at', '')}",
        f"チェック分院数: {summary.get('total_clinics', 0)}",
        f"条件クリア分院数: {summary.get('clinics_with_availability', 0)}",
        "-" * 50,
    ]

    for clinic_result in results.get('results', []):
        status = '○' if clinic_result.get('result', False) else '×'
        clinic_name = clinic_result.get('clinic', '')
        blocks = clinic_result.get('total_30min_blocks', 0)
        lines.append(f"[{status}] {clinic_name}: {blocks}ブロック")

    lines.append("=" * 50)
    return '\n'.join(lines)
