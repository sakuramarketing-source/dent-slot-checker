"""分院管理API"""

import os
import yaml
from flask import Blueprint, jsonify, request, current_app
from src.secret_manager import get_credentials, save_credentials

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
    config_path = current_app.config['CONFIG_PATH']

    # Secret Managerから認証情報を取得（ログインID表示用）
    credentials = get_credentials(config_path)
    cred_map = {c['name']: c for c in credentials.get('clinics', [])}

    # パスワードは返さない（セキュリティ）
    result = []
    for clinic in clinics:
        cred = cred_map.get(clinic.get('name'), {})
        result.append({
            'name': clinic.get('name', ''),
            'url': clinic.get('url', ''),
            'id': cred.get('id', clinic.get('id', '')),
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
    config_path = current_app.config['CONFIG_PATH']
    config = load_clinics_config()
    clinics = config.get('clinics', [])

    found = False
    for clinic in clinics:
        if clinic.get('name') == clinic_name:
            if 'url' in data:
                clinic['url'] = data['url']
            if 'enabled' in data:
                clinic['enabled'] = data['enabled']
            found = True
            break

    if not found:
        return jsonify({'error': 'Clinic not found'}), 404

    save_clinics_config(config)

    # 認証情報が含まれる場合はSecret Managerも更新
    if 'id' in data or 'password' in data:
        credentials = get_credentials(config_path)
        for cred in credentials.get('clinics', []):
            if cred['name'] == clinic_name:
                if 'id' in data:
                    cred['id'] = data['id']
                if 'password' in data:
                    cred['password'] = data['password']
                break
        save_credentials(credentials, config_path)

    return jsonify({'success': True, 'clinic': clinic_name})


@bp.route('/', methods=['POST'])
def add_clinic():
    """新しい分院を追加"""
    data = request.get_json()
    config_path = current_app.config['CONFIG_PATH']

    required_fields = ['name', 'url', 'id', 'password']
    for field in required_fields:
        if field not in data:
            return jsonify({'error': f'{field} is required'}), 400

    # 非機密情報をYAMLに追加
    config = load_clinics_config()
    new_clinic_yaml = {
        'name': data['name'],
        'url': data['url'],
        'enabled': data.get('enabled', True)
    }
    config['clinics'].append(new_clinic_yaml)
    save_clinics_config(config)

    # 認証情報をSecret Managerに追加
    credentials = get_credentials(config_path)
    credentials['clinics'].append({
        'name': data['name'],
        'id': data['id'],
        'password': data['password']
    })
    save_credentials(credentials, config_path)

    return jsonify({'success': True, 'clinic': data['name']})


@bp.route('/<clinic_name>', methods=['DELETE'])
def delete_clinic(clinic_name):
    """分院を削除"""
    config_path = current_app.config['CONFIG_PATH']

    # YAMLから削除
    config = load_clinics_config()
    clinics = config.get('clinics', [])
    original_count = len(clinics)
    config['clinics'] = [c for c in clinics if c.get('name') != clinic_name]

    if len(config['clinics']) == original_count:
        return jsonify({'error': 'Clinic not found'}), 404

    save_clinics_config(config)

    # Secret Managerからも削除
    credentials = get_credentials(config_path)
    credentials['clinics'] = [
        c for c in credentials.get('clinics', [])
        if c['name'] != clinic_name
    ]
    save_credentials(credentials, config_path)

    return jsonify({'success': True, 'clinic': clinic_name})
