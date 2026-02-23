"""メインページルート"""

import os
import json
import glob
import yaml
from flask import Blueprint, render_template, current_app

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


def _load_staff_rules():
    """staff_rules.yamlを読み込む"""
    config_path = current_app.config['CONFIG_PATH']
    path = os.path.join(config_path, 'staff_rules.yaml')
    if not os.path.exists(path):
        return {'staff_by_clinic': {}}
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {'staff_by_clinic': {}}


def _load_clinics_settings():
    """clinics.yamlのsettingsを読み込む"""
    config_path = current_app.config['CONFIG_PATH']
    path = os.path.join(config_path, 'clinics.yaml')
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    return config.get('settings', {})


def _apply_category_classification(data):
    """スタッフに職種分類(doctor/hygienist)と閾値情報を付与"""
    staff_rules = _load_staff_rules()
    staff_by_clinic = staff_rules.get('staff_by_clinic', {})

    for result in data.get('results', []):
        clinic_name = result.get('clinic', '')
        clinic_config = staff_by_clinic.get(clinic_name, {})
        doctors = set(clinic_config.get('doctors', []))
        hygienists = set(clinic_config.get('hygienists', []))
        thresholds = clinic_config.get('slot_threshold', {})
        dr_threshold = thresholds.get('doctor', 30)
        dh_threshold = thresholds.get('hygienist', 30)

        dr_blocks = hyg_blocks = other_blocks = 0
        for detail in result.get('details', []):
            staff_name = detail.get('doctor', '')
            blocks = detail.get('blocks', 0)
            if staff_name in doctors:
                detail['category'] = 'doctor'
                detail.setdefault('threshold_minutes', dr_threshold)
                dr_blocks += blocks
            elif staff_name in hygienists:
                detail['category'] = 'hygienist'
                detail.setdefault('threshold_minutes', dh_threshold)
                hyg_blocks += blocks
            else:
                detail['category'] = 'unknown'
                detail.setdefault('threshold_minutes', 30)
                other_blocks += blocks

        result['category_summary'] = {
            'doctor': dr_blocks, 'hygienist': hyg_blocks, 'other': other_blocks
        }
        result['slot_threshold'] = {
            'doctor': dr_threshold, 'hygienist': dh_threshold
        }
    return data


def _apply_web_booking_filter(data):
    """web_bookingフィルタを適用"""
    staff_rules = _load_staff_rules()
    settings = _load_clinics_settings()
    staff_by_clinic = staff_rules.get('staff_by_clinic', {})
    min_blocks = settings.get('minimum_blocks_required', 4)

    clinics_with_availability = 0

    for result in data.get('results', []):
        clinic_name = result.get('clinic', '')
        clinic_config = staff_by_clinic.get(clinic_name, {})
        web_booking = clinic_config.get('web_booking', [])

        if not web_booking:
            if result.get('result', False):
                clinics_with_availability += 1
            continue

        web_booking_set = set(web_booking)
        filtered = [
            d for d in result.get('details', [])
            if d.get('doctor', '') in web_booking_set
        ]
        result['details'] = filtered
        total = sum(d.get('blocks', 0) for d in filtered)
        result['total_30min_blocks'] = total
        result['result'] = total >= min_blocks

        if result['result']:
            clinics_with_availability += 1

    if 'summary' in data:
        data['summary']['clinics_with_availability'] = clinics_with_availability

    return data


@bp.route('/')
def index():
    """ダッシュボード"""
    result = get_latest_result()
    settings = _load_clinics_settings()
    min_blocks = settings.get('minimum_blocks_required', 4)
    if result:
        result = _apply_category_classification(result)
        result = _apply_web_booking_filter(result)
    return render_template('index.html', result=result, min_blocks=min_blocks)


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
