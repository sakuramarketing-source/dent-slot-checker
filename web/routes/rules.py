"""ルール管理API"""

import os
import yaml
from flask import Blueprint, jsonify, request, current_app

bp = Blueprint('rules', __name__)


def load_clinics_config():
    """clinics.yamlを読み込む"""
    config_path = current_app.config['CONFIG_PATH']
    clinics_path = os.path.join(config_path, 'clinics.yaml')

    with open(clinics_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def save_clinics_config(data):
    """clinics.yamlに保存（GCS同期あり）"""
    config_path = current_app.config['CONFIG_PATH']
    clinics_path = os.path.join(config_path, 'clinics.yaml')

    with open(clinics_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    # GCSにアップロード（有効な場合のみ）
    try:
        from src.gcs_storage import upload_config_file, is_gcs_enabled
        if is_gcs_enabled():
            upload_config_file(clinics_path, 'clinics.yaml')
    except Exception:
        pass  # GCSエラーは無視してローカル保存を優先


@bp.route('/', methods=['GET'])
def get_rules():
    """ルール設定を取得"""
    config = load_clinics_config()
    settings = config.get('settings', {})

    return jsonify({
        'consecutive_slots_required': settings.get('consecutive_slots_required', 6),
        'minimum_blocks_required': settings.get('minimum_blocks_required', 4),
        'exclude_patterns': settings.get('exclude_patterns', ['訪問']),
        'check_hours': settings.get('check_hours', {'start': 9, 'end': 19}),
        'slot_interval_minutes': settings.get('slot_interval_minutes', 5)
    })


@bp.route('/', methods=['POST'])
def update_rules():
    """ルール設定を更新"""
    data = request.get_json()
    config = load_clinics_config()

    if 'settings' not in config:
        config['settings'] = {}

    settings = config['settings']

    # 各設定を更新
    if 'consecutive_slots_required' in data:
        settings['consecutive_slots_required'] = int(data['consecutive_slots_required'])

    if 'minimum_blocks_required' in data:
        settings['minimum_blocks_required'] = int(data['minimum_blocks_required'])

    if 'exclude_patterns' in data:
        patterns = data['exclude_patterns']
        if isinstance(patterns, str):
            # カンマ区切りの文字列の場合
            patterns = [p.strip() for p in patterns.split(',') if p.strip()]
        settings['exclude_patterns'] = patterns

    if 'check_hours' in data:
        hours = data['check_hours']
        if 'start' in hours:
            settings.setdefault('check_hours', {})['start'] = int(hours['start'])
        if 'end' in hours:
            settings.setdefault('check_hours', {})['end'] = int(hours['end'])

    if 'slot_interval_minutes' in data:
        settings['slot_interval_minutes'] = int(data['slot_interval_minutes'])

    save_clinics_config(config)

    return jsonify({'success': True, 'settings': settings})
