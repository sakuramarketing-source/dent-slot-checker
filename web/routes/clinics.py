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
    """全分院情報を取得（dent-sys + Stransa）"""
    config = load_clinics_config()
    config_path = current_app.config['CONFIG_PATH']

    # Secret Managerから認証情報を取得（ログインID表示用）
    credentials = get_credentials(config_path)
    dent_cred_map = {c['name']: c for c in credentials.get('clinics', [])}
    stransa_cred_map = {c['name']: c for c in credentials.get('stransa_clinics', [])}

    # パスワードは返さない（セキュリティ）
    result = []

    # dent-sys分院
    for clinic in config.get('clinics', []):
        cred = dent_cred_map.get(clinic.get('name'), {})
        result.append({
            'name': clinic.get('name', ''),
            'url': clinic.get('url', ''),
            'id': cred.get('id', clinic.get('id', '')),
            'enabled': clinic.get('enabled', True),
            'system': 'dent-sys'
        })

    # Stransa分院
    for clinic in config.get('stransa_clinics', []):
        cred = stransa_cred_map.get(clinic.get('name'), {})
        result.append({
            'name': clinic.get('name', ''),
            'url': clinic.get('url', ''),
            'id': cred.get('id', clinic.get('id', '')),
            'enabled': clinic.get('enabled', True),
            'system': 'stransa'
        })

    return jsonify(result)


def _find_clinic_in_config(config, clinic_name):
    """configからclinicを検索し、(clinic, section_key)を返す"""
    for clinic in config.get('clinics', []):
        if clinic.get('name') == clinic_name:
            return clinic, 'clinics'
    for clinic in config.get('stransa_clinics', []):
        if clinic.get('name') == clinic_name:
            return clinic, 'stransa_clinics'
    return None, None


@bp.route('/<clinic_name>/toggle', methods=['POST'])
def toggle_clinic(clinic_name):
    """分院の有効/無効を切り替え"""
    config = load_clinics_config()
    clinic, section = _find_clinic_in_config(config, clinic_name)

    if not clinic:
        return jsonify({'error': 'Clinic not found'}), 404

    clinic['enabled'] = not clinic.get('enabled', True)
    save_clinics_config(config)

    return jsonify({
        'success': True,
        'clinic': clinic_name,
        'enabled': clinic['enabled']
    })


@bp.route('/<clinic_name>', methods=['PUT'])
def update_clinic(clinic_name):
    """分院情報を更新"""
    data = request.get_json()
    config_path = current_app.config['CONFIG_PATH']
    config = load_clinics_config()
    clinic, section = _find_clinic_in_config(config, clinic_name)

    if not clinic:
        return jsonify({'error': 'Clinic not found'}), 404

    if 'url' in data:
        clinic['url'] = data['url']
    if 'enabled' in data:
        clinic['enabled'] = data['enabled']

    save_clinics_config(config)

    # 認証情報が含まれる場合はSecret Managerも更新
    if 'id' in data or 'password' in data:
        cred_key = 'stransa_clinics' if section == 'stransa_clinics' else 'clinics'
        credentials = get_credentials(config_path)
        for cred in credentials.get(cred_key, []):
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

    system = data.get('system', 'dent-sys')
    section = 'stransa_clinics' if system == 'stransa' else 'clinics'

    # 非機密情報をYAMLに追加
    config = load_clinics_config()
    new_clinic_yaml = {
        'name': data['name'],
        'url': data['url'],
        'enabled': data.get('enabled', True)
    }
    config[section].append(new_clinic_yaml)
    save_clinics_config(config)

    # 認証情報をSecret Managerに追加
    credentials = get_credentials(config_path)
    if section not in credentials:
        credentials[section] = []
    credentials[section].append({
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

    # YAMLから削除（両セクションを検索）
    config = load_clinics_config()
    deleted = False
    for section in ['clinics', 'stransa_clinics']:
        items = config.get(section, [])
        original_count = len(items)
        config[section] = [c for c in items if c.get('name') != clinic_name]
        if len(config[section]) < original_count:
            deleted = True
            cred_key = section
            break

    if not deleted:
        return jsonify({'error': 'Clinic not found'}), 404

    save_clinics_config(config)

    # Secret Managerからも削除
    credentials = get_credentials(config_path)
    credentials[cred_key] = [
        c for c in credentials.get(cred_key, [])
        if c['name'] != clinic_name
    ]
    save_credentials(credentials, config_path)

    return jsonify({'success': True, 'clinic': clinic_name})
