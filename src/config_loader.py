"""設定ファイル読み込みモジュール"""

import yaml
from pathlib import Path
from typing import Dict, List, Any


def load_yaml(file_path: Path) -> Dict[str, Any]:
    """YAMLファイルを読み込む"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_yaml_optional(file_path: Path, default: Dict = None) -> Dict[str, Any]:
    """YAMLファイルを読み込む（存在しない場合はデフォルト値を返す）"""
    if default is None:
        default = {}
    if not file_path.exists():
        return default
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or default


def load_config(config_dir: Path = None) -> Dict[str, Any]:
    """全ての設定ファイルを読み込む"""
    if config_dir is None:
        config_dir = Path(__file__).parent.parent / 'config'

    clinics_config = load_yaml(config_dir / 'clinics.yaml')
    # staff_rules.yamlは任意（存在しない場合は空のデフォルト値）
    staff_rules = load_yaml_optional(config_dir / 'staff_rules.yaml', {'staff_categories': {}, 'special_rules': {}})

    # dent-sys.net 分院
    dent_sys_clinics = clinics_config.get('clinics', [])
    # dent-sys.net 分院にはsystemフラグを追加
    for clinic in dent_sys_clinics:
        if 'system' not in clinic:
            clinic['system'] = 'dent-sys'

    # Stransa 分院
    stransa_clinics = clinics_config.get('stransa_clinics', [])

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
