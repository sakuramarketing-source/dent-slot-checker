"""メインページルート"""

import os
import json
import glob
from flask import Blueprint, render_template, current_app, make_response

bp = Blueprint('main', __name__)


def get_latest_result():
    """最新のチェック結果を取得（GCS優先）"""
    import logging
    from src.gcs_storage import list_results_from_gcs, load_result_from_gcs, is_gcs_enabled

    logger = logging.getLogger(__name__)

    # 1. GCSから結果を取得（Cloud Run用）
    if is_gcs_enabled():
        logger.info("GCSから最新結果を取得中...")
        result_files = list_results_from_gcs()

        if result_files:
            latest_file = result_files[0]  # 既にソート済み（新しい順）
            logger.info(f"GCS最新結果: {latest_file['name']}")

            result_data = load_result_from_gcs(latest_file['name'])
            if result_data:
                result_count = len(result_data.get('results', []))
                logger.info(f"GCSから結果読み込み: {result_count} 分院")
                return result_data
            else:
                logger.warning(f"GCS結果読み込み失敗: {latest_file['name']}")
        else:
            logger.info("GCSに結果ファイルなし")

    # 2. ローカルファイルから取得（ローカル開発用）
    output_path = current_app.config['OUTPUT_PATH']
    json_files = glob.glob(os.path.join(output_path, 'slot_check_*.json'))

    logger.info(f"ローカル結果ファイル: {len(json_files)} 件")

    if not json_files:
        logger.warning("結果ファイルが見つかりません（ローカル・GCS共に）")
        return None

    # 最新のファイルを取得
    latest_file = max(json_files, key=os.path.getmtime)
    logger.info(f"ローカル最新結果: {os.path.basename(latest_file)}")

    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        result_count = len(data.get('results', []))
        logger.info(f"ローカルから結果読み込み: {result_count} 分院")
        return data
    except Exception as e:
        logger.error(f"ローカルファイル読み込み失敗 {latest_file}: {e}")
        return None


@bp.route('/')
def index():
    """ダッシュボード"""
    result = get_latest_result()
    response = make_response(render_template('index.html', result=result))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@bp.route('/staff')
def staff_page():
    """スタッフ管理画面"""
    return render_template('staff.html')


@bp.route('/clinics')
def clinics_page():
    """分院管理画面"""
    return render_template('clinics.html')


@bp.route('/rules')
def rules_page():
    """ルール設定画面"""
    return render_template('rules.html')


@bp.route('/results')
def results_page():
    """結果表示画面"""
    return render_template('results.html')
