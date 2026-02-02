"""メインページルート"""

import os
import json
import glob
from flask import Blueprint, render_template, current_app, make_response

bp = Blueprint('main', __name__)


def get_latest_result():
    """最新のチェック結果を取得"""
    output_path = current_app.config['OUTPUT_PATH']
    json_files = glob.glob(os.path.join(output_path, 'slot_check_*.json'))

    if not json_files:
        return None

    # 最新のファイルを取得
    latest_file = max(json_files, key=os.path.getmtime)

    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
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
