"""分院管理API"""

import os
import yaml
from flask import Blueprint, jsonify, request, current_app

bp = Blueprint('clinics', __name__)


def load_clinics_config():
    """clinics.yamlを読み込む"""
    config_path = current_app.config['CONFIG_PATH']
    clinics_path = os.path.join(config_path, 'clinics.yaml')

    with open(clinics_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def save_clinics_config(data):
    """clinics.yamlに保存"""
    config_path = current_app.config['CONFIG_PATH']
    clinics_path = os.path.join(config_path, 'clinics.yaml')

    with open(clinics_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


@bp.route('/', methods=['GET'])
def get_clinics():
    """全分院情報を取得"""
    config = load_clinics_config()
    clinics = config.get('clinics', [])

    # パスワードは返さない（セキュリティ）
    result = []
    for clinic in clinics:
        result.append({
            'name': clinic.get('name', ''),
            'url': clinic.get('url', ''),
            'id': clinic.get('id', ''),
            'enabled': clinic.get('enabled', True)
        })

    return jsonify(result)


@bp.route('/<clinic_name>/toggle', methods=['POST'])
def toggle_clinic(clinic_name):
    """分院の有効/無効を切り替え"""
    config = load_clinics_config()
    clinics = config.get('clinics', [])

    found = False
    new_enabled = None

    for clinic in clinics:
        if clinic.get('name') == clinic_name:
            clinic['enabled'] = not clinic.get('enabled', True)
            new_enabled = clinic['enabled']
            found = True
            break

    if not found:
        return jsonify({'error': 'Clinic not found'}), 404

    save_clinics_config(config)

    return jsonify({
        'success': True,
        'clinic': clinic_name,
        'enabled': new_enabled
    })


@bp.route('/<clinic_name>', methods=['PUT'])
def update_clinic(clinic_name):
    """分院情報を更新"""
    data = request.get_json()
    config = load_clinics_config()
    clinics = config.get('clinics', [])

    found = False
    for clinic in clinics:
        if clinic.get('name') == clinic_name:
            if 'url' in data:
                clinic['url'] = data['url']
            if 'id' in data:
                clinic['id'] = data['id']
            if 'password' in data:
                clinic['password'] = data['password']
            if 'enabled' in data:
                clinic['enabled'] = data['enabled']
            found = True
            break

    if not found:
        return jsonify({'error': 'Clinic not found'}), 404

    save_clinics_config(config)

    return jsonify({'success': True, 'clinic': clinic_name})


@bp.route('/', methods=['POST'])
def add_clinic():
    """新しい分院を追加"""
    data = request.get_json()

    required_fields = ['name', 'url', 'id', 'password']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'{field} is required'}), 400

    config = load_clinics_config()

    new_clinic = {
        'name': data['name'],
        'url': data['url'],
        'id': data['id'],
        'password': data['password'],
        'enabled': data.get('enabled', True)
    }

    config['clinics'].append(new_clinic)
    save_clinics_config(config)

    return jsonify({'success': True, 'clinic': data['name']})


@bp.route('/<clinic_name>', methods=['DELETE'])
def delete_clinic(clinic_name):
    """分院を削除"""
    config = load_clinics_config()
    clinics = config.get('clinics', [])

    original_count = len(clinics)
    config['clinics'] = [c for c in clinics if c.get('name') != clinic_name]

    if len(config['clinics']) == original_count:
        return jsonify({'error': 'Clinic not found'}), 404

    save_clinics_config(config)

    return jsonify({'success': True, 'clinic': clinic_name})
