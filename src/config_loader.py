"""設定ファイル読み込みモジュール"""

import yaml
from pathlib import Path
from typing import Dict, List, Any

from .secret_manager import get_credentials
from .gcs_helper import download_from_gcs


def load_yaml(file_path: Path) -> Dict[str, Any]:
    """YAMLファイルを読み込む"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_config(config_dir: Path = None) -> Dict[str, Any]:
    """全ての設定ファイルを読み込む"""
    if config_dir is None:
        config_dir = Path(__file__).parent.parent / 'config'

    # Cloud RunではGCSから最新のstaff_rules.yamlを取得
    download_from_gcs('config/staff_rules.yaml', str(config_dir / 'staff_rules.yaml'))

    clinics_config = load_yaml(config_dir / 'clinics.yaml')
    staff_rules = load_yaml(config_dir / 'staff_rules.yaml')

    # Secret Managerまたはclinics.yamlから認証情報を取得
    credentials = get_credentials(str(config_dir))
    cred_map = {c['name']: c for c in credentials.get('clinics', [])}
    stransa_cred_map = {c['name']: c for c in credentials.get('stransa_clinics', [])}

    # dent-sys.net 分院
    dent_sys_clinics = clinics_config.get('clinics', [])
    for clinic in dent_sys_clinics:
        if 'system' not in clinic:
            clinic['system'] = 'dent-sys'
        # 認証情報をマージ
        cred = cred_map.get(clinic.get('name'))
        if cred:
            clinic.setdefault('id', cred['id'])
            clinic.setdefault('password', cred['password'])

    # Stransa 分院
    stransa_clinics = clinics_config.get('stransa_clinics', [])
    for clinic in stransa_clinics:
        clinic['system'] = 'stransa'
        cred = stransa_cred_map.get(clinic.get('name'))
        if cred:
            clinic.setdefault('id', cred['id'])
            clinic.setdefault('password', cred['password'])

    # 全分院を統合
    all_clinics = dent_sys_clinics + stransa_clinics

    return {
        'clinics': all_clinics,
        'dent_sys_clinics': dent_sys_clinics,
        'stransa_clinics': stransa_clinics,
        'settings': clinics_config.get('settings', {}),
        'staff_categories': staff_rules.get('staff_categories', {}),
        'special_rules': staff_rules.get('special_rules', {}),
    }


def get_enabled_clinics(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """有効な分院のみを取得"""
    return [c for c in config['clinics'] if c.get('enabled', True)]


def get_exclude_patterns(config: Dict[str, Any]) -> List[str]:
    """除外パターンを取得"""
    return config['settings'].get('exclude_patterns', ['訪問'])


def get_slot_settings(config: Dict[str, Any]) -> Dict[str, int]:
    """スロット設定を取得"""
    settings = config['settings']
    return {
        'consecutive_slots_required': settings.get('consecutive_slots_required', 6),
        'minimum_blocks_required': settings.get('minimum_blocks_required', 4),
        'slot_interval_minutes': settings.get('slot_interval_minutes', 5),
    }
