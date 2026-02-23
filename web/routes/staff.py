"""スタッフ管理API"""

import os
import json
import glob
import yaml
from flask import Blueprint, jsonify, request, current_app

bp = Blueprint('staff', __name__)

# コーポレートサイト沿革の開院順
CLINIC_ORDER = [
    'さくら歯科',
    'たんぽぽ歯科',
    'ありす歯科',
    '春日井きらり歯科',
    '松戸ありす歯科',
    '池下さくら歯科',
    '日進赤池たんぽぽ歯科',
    '春日井アップル歯科',
    'さくら医院',
    '金沢さくら医院',
    '流山ハピネス歯科',
    'イーアス春日井歯科',
    '名駅さくら医院・歯科・皮膚科',
    'きらり大森歯科',
    'クローバー歯科',
    '流山ありす歯科・矯正歯科',
    '町屋さくら歯科・矯正歯科',
]


def load_staff_rules():
    """staff_rules.yamlを読み込む"""
    config_path = current_app.config['CONFIG_PATH']
    staff_rules_path = os.path.join(config_path, 'staff_rules.yaml')

    if not os.path.exists(staff_rules_path):
        return {'staff_by_clinic': {}}

    with open(staff_rules_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {'staff_by_clinic': {}}


def save_staff_rules(data):
    """staff_rules.yamlに保存"""
    config_path = current_app.config['CONFIG_PATH']
    staff_rules_path = os.path.join(config_path, 'staff_rules.yaml')

    with open(staff_rules_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def load_clinics_config():
    """clinics.yamlを読み込む"""
    config_path = current_app.config['CONFIG_PATH']
    clinics_path = os.path.join(config_path, 'clinics.yaml')

    with open(clinics_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_all_staff_from_results():
    """チェック結果から全スタッフ名を収集（空きがなくても含む）"""
    output_path = current_app.config['OUTPUT_PATH']
    json_files = glob.glob(os.path.join(output_path, 'slot_check_*.json'))

    staff_by_clinic = {}

    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for result in data.get('results', []):
                clinic_name = result.get('clinic', '')
                if clinic_name not in staff_by_clinic:
                    staff_by_clinic[clinic_name] = set()

                for detail in result.get('details', []):
                    doctor_name = detail.get('doctor', '')
                    if doctor_name:
                        staff_by_clinic[clinic_name].add(doctor_name)

        except Exception:
            continue

    # setをlistに変換
    return {k: sorted(list(v)) for k, v in staff_by_clinic.items()}


def get_all_headers_from_debug():
    """debug_iframe.htmlから全ヘッダー（スタッフ名）を取得"""
    # TODO: スクレイピング時に全ヘッダーを保存する機能を追加
    return {}


@bp.route('/', methods=['GET'])
def get_all_staff():
    """全スタッフ情報を取得"""
    # 結果ファイルからスタッフ名を収集
    staff_from_results = get_all_staff_from_results()

    # 設定ファイルからスタッフ分類を読み込む
    staff_rules = load_staff_rules()
    staff_by_clinic = staff_rules.get('staff_by_clinic', {})

    # clinics.yaml から除外パターンを取得
    clinics_config = load_clinics_config()
    exclude_patterns = clinics_config.get('settings', {}).get('exclude_patterns', ['訪問'])

    # マージした結果を作成（開院順）
    result = {}

    # CLINIC_ORDER順 + 未知のクリニックは末尾
    all_clinic_names = set(staff_from_results.keys()) | set(staff_by_clinic.keys())
    ordered_names = list(CLINIC_ORDER)
    for name in sorted(all_clinic_names):
        if name not in ordered_names:
            ordered_names.append(name)

    for clinic_name in ordered_names:
        clinic_config = staff_by_clinic.get(clinic_name, {})
        staff_from_result = staff_from_results.get(clinic_name, [])

        # 全スタッフ名を収集（同期データ優先、なければ結果 + 設定ファイル）
        all_staff_names = set()

        # 同期データがあれば優先的に使用
        if clinic_config.get('all_staff'):
            all_staff_names.update(clinic_config.get('all_staff', []))
        else:
            # 同期データがない場合は結果ファイルから
            all_staff_names.update(staff_from_result)

        # 設定ファイルの分類済みスタッフも追加
        all_staff_names.update(clinic_config.get('doctors', []))
        all_staff_names.update(clinic_config.get('hygienists', []))
        all_staff_names.update(clinic_config.get('disabled', []))

        doctors = clinic_config.get('doctors', [])
        hygienists = clinic_config.get('hygienists', [])
        disabled = clinic_config.get('disabled', [])
        web_booking = set(clinic_config.get('web_booking', []))
        thresholds = clinic_config.get('slot_threshold', {})

        result[clinic_name] = {
            'staff': [],
            'has_web_booking_filter': len(web_booking) > 0,
            'slot_threshold': {
                'doctor': thresholds.get('doctor', 30),
                'hygienist': thresholds.get('hygienist', 30),
            }
        }

        for staff_name in sorted(all_staff_names):
            # カテゴリを判定
            category = 'unknown'
            if staff_name in doctors:
                category = 'doctor'
            elif staff_name in hygienists:
                category = 'hygienist'

            # 除外パターンに該当するかチェック
            auto_disabled = any(pattern in staff_name for pattern in exclude_patterns)

            # 有効/無効を判定
            is_disabled = staff_name in disabled or auto_disabled

            # WEB予約受付判定
            is_web_booking = staff_name in web_booking

            # メモとタグを取得
            memos = clinic_config.get('memos', {})
            tags = clinic_config.get('tags', {})

            result[clinic_name]['staff'].append({
                'name': staff_name,
                'category': category,
                'enabled': not is_disabled,
                'auto_disabled': auto_disabled,  # 自動除外（訪問系など）
                'web_booking': is_web_booking,
                'memo': memos.get(staff_name, ''),
                'tags': tags.get(staff_name, [])
            })

    return jsonify(result)


@bp.route('/<clinic_name>', methods=['GET'])
def get_clinic_staff(clinic_name):
    """特定分院のスタッフ情報を取得"""
    all_staff = get_all_staff().get_json()
    clinic_staff = all_staff.get(clinic_name, {'staff': []})
    return jsonify(clinic_staff)


@bp.route('/<clinic_name>', methods=['POST'])
def update_staff_category(clinic_name):
    """スタッフの職種を更新"""
    data = request.get_json()
    staff_name = data.get('name')
    category = data.get('category')  # 'doctor', 'hygienist', 'unknown'

    if not staff_name or not category:
        return jsonify({'error': 'name and category are required'}), 400

    # 設定を読み込み
    staff_rules = load_staff_rules()

    if 'staff_by_clinic' not in staff_rules:
        staff_rules['staff_by_clinic'] = {}

    if clinic_name not in staff_rules['staff_by_clinic']:
        staff_rules['staff_by_clinic'][clinic_name] = {'doctors': [], 'hygienists': [], 'disabled': []}

    clinic_config = staff_rules['staff_by_clinic'][clinic_name]

    # 既存の分類から削除
    if 'doctors' in clinic_config and staff_name in clinic_config['doctors']:
        clinic_config['doctors'].remove(staff_name)
    if 'hygienists' in clinic_config and staff_name in clinic_config['hygienists']:
        clinic_config['hygienists'].remove(staff_name)

    # 新しい分類に追加
    if category == 'doctor':
        if 'doctors' not in clinic_config:
            clinic_config['doctors'] = []
        clinic_config['doctors'].append(staff_name)
    elif category == 'hygienist':
        if 'hygienists' not in clinic_config:
            clinic_config['hygienists'] = []
        clinic_config['hygienists'].append(staff_name)
    # 'unknown' の場合は何も追加しない

    # 保存
    save_staff_rules(staff_rules)

    return jsonify({'success': True, 'clinic': clinic_name, 'name': staff_name, 'category': category})


@bp.route('/<clinic_name>/toggle', methods=['POST'])
def toggle_staff_enabled(clinic_name):
    """スタッフの有効/無効を切り替え"""
    data = request.get_json()
    staff_name = data.get('name')

    if not staff_name:
        return jsonify({'error': 'name is required'}), 400

    # 設定を読み込み
    staff_rules = load_staff_rules()

    if 'staff_by_clinic' not in staff_rules:
        staff_rules['staff_by_clinic'] = {}

    if clinic_name not in staff_rules['staff_by_clinic']:
        staff_rules['staff_by_clinic'][clinic_name] = {'doctors': [], 'hygienists': [], 'disabled': []}

    clinic_config = staff_rules['staff_by_clinic'][clinic_name]

    if 'disabled' not in clinic_config:
        clinic_config['disabled'] = []

    # トグル
    if staff_name in clinic_config['disabled']:
        clinic_config['disabled'].remove(staff_name)
        enabled = True
    else:
        clinic_config['disabled'].append(staff_name)
        enabled = False

    # 保存
    save_staff_rules(staff_rules)

    return jsonify({'success': True, 'clinic': clinic_name, 'name': staff_name, 'enabled': enabled})


@bp.route('/<clinic_name>/web-booking', methods=['POST'])
def toggle_web_booking(clinic_name):
    """スタッフのWEB予約受付を切り替え"""
    data = request.get_json()
    staff_name = data.get('name')

    if not staff_name:
        return jsonify({'error': 'name is required'}), 400

    # 設定を読み込み
    staff_rules = load_staff_rules()

    if 'staff_by_clinic' not in staff_rules:
        staff_rules['staff_by_clinic'] = {}

    if clinic_name not in staff_rules['staff_by_clinic']:
        staff_rules['staff_by_clinic'][clinic_name] = {}

    clinic_config = staff_rules['staff_by_clinic'][clinic_name]

    if 'web_booking' not in clinic_config:
        clinic_config['web_booking'] = []

    # トグル
    if staff_name in clinic_config['web_booking']:
        clinic_config['web_booking'].remove(staff_name)
        web_booking = False
    else:
        clinic_config['web_booking'].append(staff_name)
        web_booking = True

    # 保存
    save_staff_rules(staff_rules)

    return jsonify({'success': True, 'clinic': clinic_name, 'name': staff_name, 'web_booking': web_booking})


@bp.route('/<clinic_name>/memo', methods=['POST'])
def update_staff_memo(clinic_name):
    """スタッフのメモを更新"""
    data = request.get_json()
    staff_name = data.get('name')
    memo = data.get('memo', '')

    if not staff_name:
        return jsonify({'error': 'name is required'}), 400

    # 設定を読み込み
    staff_rules = load_staff_rules()

    if 'staff_by_clinic' not in staff_rules:
        staff_rules['staff_by_clinic'] = {}

    if clinic_name not in staff_rules['staff_by_clinic']:
        staff_rules['staff_by_clinic'][clinic_name] = {}

    clinic_config = staff_rules['staff_by_clinic'][clinic_name]

    if 'memos' not in clinic_config:
        clinic_config['memos'] = {}

    # メモを更新（空文字の場合は削除）
    if memo:
        clinic_config['memos'][staff_name] = memo
    elif staff_name in clinic_config['memos']:
        del clinic_config['memos'][staff_name]

    # 保存
    save_staff_rules(staff_rules)

    return jsonify({'success': True, 'clinic': clinic_name, 'name': staff_name, 'memo': memo})


@bp.route('/<clinic_name>/tags', methods=['POST'])
def update_staff_tags(clinic_name):
    """スタッフのタグを更新"""
    data = request.get_json()
    staff_name = data.get('name')
    tags = data.get('tags', [])

    if not staff_name:
        return jsonify({'error': 'name is required'}), 400

    # 設定を読み込み
    staff_rules = load_staff_rules()

    if 'staff_by_clinic' not in staff_rules:
        staff_rules['staff_by_clinic'] = {}

    if clinic_name not in staff_rules['staff_by_clinic']:
        staff_rules['staff_by_clinic'][clinic_name] = {}

    clinic_config = staff_rules['staff_by_clinic'][clinic_name]

    if 'tags' not in clinic_config:
        clinic_config['tags'] = {}

    # タグを更新（空リストの場合は削除）
    if tags:
        clinic_config['tags'][staff_name] = tags
    elif staff_name in clinic_config['tags']:
        del clinic_config['tags'][staff_name]

    # 保存
    save_staff_rules(staff_rules)

    return jsonify({'success': True, 'clinic': clinic_name, 'name': staff_name, 'tags': tags})


@bp.route('/<clinic_name>/threshold', methods=['POST'])
def update_threshold(clinic_name):
    """医院の空き枠判定閾値を更新"""
    data = request.get_json()
    doctor_threshold = data.get('doctor')
    hygienist_threshold = data.get('hygienist')

    if doctor_threshold is None and hygienist_threshold is None:
        return jsonify({'error': 'doctor or hygienist threshold is required'}), 400

    # 設定を読み込み
    staff_rules = load_staff_rules()

    if 'staff_by_clinic' not in staff_rules:
        staff_rules['staff_by_clinic'] = {}

    if clinic_name not in staff_rules['staff_by_clinic']:
        staff_rules['staff_by_clinic'][clinic_name] = {}

    clinic_config = staff_rules['staff_by_clinic'][clinic_name]

    if 'slot_threshold' not in clinic_config:
        clinic_config['slot_threshold'] = {}

    if doctor_threshold is not None:
        clinic_config['slot_threshold']['doctor'] = int(doctor_threshold)
    if hygienist_threshold is not None:
        clinic_config['slot_threshold']['hygienist'] = int(hygienist_threshold)

    # 保存
    save_staff_rules(staff_rules)

    return jsonify({
        'success': True,
        'clinic': clinic_name,
        'slot_threshold': clinic_config['slot_threshold']
    })


@bp.route('/sync', methods=['POST'])
def sync_staff():
    """全分院のスタッフ名を同期取得"""
    import asyncio
    import sys

    # srcモジュールをインポート
    sys.path.insert(0, current_app.config['PROJECT_ROOT'])
    from src.scraper import sync_all_staff
    from src.config_loader import load_config, get_enabled_clinics

    try:
        # 設定読み込み
        config = load_config()
        clinics = get_enabled_clinics(config)

        # 非同期処理を実行
        sync_results = asyncio.run(sync_all_staff(clinics, headless=True))

        # staff_rules.yaml を更新
        staff_rules = load_staff_rules()

        if 'staff_by_clinic' not in staff_rules:
            staff_rules['staff_by_clinic'] = {}

        for clinic_name, all_staff in sync_results.items():
            if clinic_name not in staff_rules['staff_by_clinic']:
                staff_rules['staff_by_clinic'][clinic_name] = {}

            # all_staff リストを保存
            staff_rules['staff_by_clinic'][clinic_name]['all_staff'] = all_staff

        save_staff_rules(staff_rules)

        return jsonify({
            'success': True,
            'message': 'スタッフ同期完了',
            'results': {k: len(v) for k, v in sync_results.items()}
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
