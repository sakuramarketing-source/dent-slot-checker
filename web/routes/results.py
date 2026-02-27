"""結果表示API"""

import os
import json
import glob
import subprocess
import sys
import time
import yaml
from flask import Blueprint, jsonify, request, current_app, send_file

# プロジェクトルートをパスに追加
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.slot_analyzer import count_30min_blocks, count_consecutive_blocks, format_time_range
from src.gcs_helper import sync_output_from_gcs

bp = Blueprint('results', __name__)

# バックグラウンドチェック状態管理
_check_process = None
_check_log_file = None
_check_started_at = None
_CHECK_TIMEOUT = 720  # 12分タイムアウト


def load_staff_rules():
    """staff_rules.yamlを読み込む（staff.pyの共通関数を使用）"""
    from web.routes.staff import load_staff_rules as _load
    return _load()


def load_clinics_settings():
    """clinics.yamlのsettingsを読み込む"""
    config_path = current_app.config['CONFIG_PATH']
    clinics_path = os.path.join(config_path, 'clinics.yaml')

    if not os.path.exists(clinics_path):
        return {}

    with open(clinics_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    return config.get('settings', {})


def apply_web_booking_filter(data, staff_rules, settings=None):
    """web_bookingリストに基づいて結果をフィルタリング

    web_bookingが設定されている分院は、WEB予約受付スタッフのみ表示。
    未設定の分院は従来通り全スタッフ表示。
    """
    staff_by_clinic = staff_rules.get('staff_by_clinic', {})
    min_blocks = (settings or {}).get('minimum_blocks_required', 4)

    has_filter = False
    clinics_with_availability = 0

    for result in data.get('results', []):
        clinic_name = result.get('clinic', '')
        clinic_config = staff_by_clinic.get(clinic_name, {})
        web_booking = clinic_config.get('web_booking', [])

        if not web_booking:
            # web_booking未設定 → フィルタなし
            if result.get('result', False):
                clinics_with_availability += 1
            continue

        has_filter = True
        web_booking_set = set(web_booking)

        # web_bookingリストのスタッフのみに絞る
        filtered_details = [
            d for d in result.get('details', [])
            if d.get('doctor', '') in web_booking_set
        ]
        result['details'] = filtered_details

        # 合計を再計算
        total = sum(d.get('blocks', 0) for d in filtered_details)
        result['total_30min_blocks'] = total

        # 判定を再計算
        result['result'] = total >= min_blocks

        if result['result']:
            clinics_with_availability += 1

    # サマリーを再計算
    if has_filter and 'summary' in data:
        data['summary']['clinics_with_availability'] = clinics_with_availability

    return data


def _recalculate_detail(detail, threshold):
    """raw_slot_timesがあれば指定閾値で枠数を再計算"""
    raw_times = detail.get('raw_slot_times')
    if not raw_times:
        return
    interval = detail.get('slot_interval', 5)
    consec = threshold // interval
    detail['blocks'] = count_30min_blocks(raw_times, interval, consec)
    _, ranges = count_consecutive_blocks(raw_times, consec, interval)
    detail['times'] = [format_time_range(s, e, interval) for s, e in ranges]
    detail['threshold_minutes'] = threshold


_output_synced = False


def get_result_files():
    """結果ファイルのリストを取得"""
    global _output_synced
    output_path = current_app.config['OUTPUT_PATH']

    # Cloud Run起動時にGCSからoutputファイルを同期（初回のみ）
    if not _output_synced:
        sync_output_from_gcs(output_path)
        _output_synced = True

    json_files = glob.glob(os.path.join(output_path, 'slot_check_*.json'))

    files = []
    for f in json_files:
        basename = os.path.basename(f)
        # slot_check_YYYYMMDD_YYYYMMDD_HHMMSS.json から日付を抽出
        parts = basename.replace('.json', '').split('_')
        if len(parts) >= 5:
            check_date = parts[2]  # YYYYMMDD (対象日)
            run_date = parts[3]    # YYYYMMDD (実行日)
            run_time = parts[4]    # HHMMSS (実行時刻)
            files.append({
                'filename': basename,
                'check_date': f"{check_date[:4]}-{check_date[4:6]}-{check_date[6:8]}",
                'path': f,
                'sort_key': f"{check_date}_{run_date}_{run_time}"  # ソート用キー
            })

    # 日付+時刻順でソート（新しい順）
    files.sort(key=lambda x: x['sort_key'], reverse=True)
    return files


@bp.route('/', methods=['GET'])
def get_latest_result():
    """最新の結果を取得"""
    files = get_result_files()

    if not files:
        return jsonify({'error': 'No results found'}), 404

    latest = files[0]

    try:
        with open(latest['path'], 'r', encoding='utf-8') as f:
            data = json.load(f)

        # WEB予約受付フィルタを適用
        staff_rules = load_staff_rules()
        settings = load_clinics_settings()
        data = apply_web_booking_filter(data, staff_rules, settings)

        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/list', methods=['GET'])
def get_result_list():
    """結果ファイルのリストを取得"""
    files = get_result_files()
    return jsonify([{'filename': f['filename'], 'check_date': f['check_date']} for f in files])


@bp.route('/<date>', methods=['GET'])
def get_result_by_date(date):
    """指定日付の結果を取得"""
    # dateは YYYY-MM-DD 形式
    date_str = date.replace('-', '')

    output_path = current_app.config['OUTPUT_PATH']
    pattern = os.path.join(output_path, f'slot_check_{date_str}_*.json')
    files = glob.glob(pattern)

    if not files:
        return jsonify({'error': f'No results found for {date}'}), 404

    # 最新のファイル（同じ日付で複数ある場合）
    latest = max(files, key=os.path.getmtime)

    try:
        with open(latest, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/with-categories', methods=['GET'])
def get_result_with_categories():
    """最新の結果を職種別集計付きで取得"""
    files = get_result_files()

    if not files:
        return jsonify({'error': 'No results found'}), 404

    latest = files[0]

    try:
        with open(latest['path'], 'r', encoding='utf-8') as f:
            data = json.load(f)

        # スタッフ分類を読み込み
        staff_rules = load_staff_rules()
        staff_by_clinic = staff_rules.get('staff_by_clinic', {})

        # 結果に職種別集計を追加
        for result in data.get('results', []):
            clinic_name = result.get('clinic', '')
            clinic_config = staff_by_clinic.get(clinic_name, {})

            doctors = set(clinic_config.get('doctors', []))
            hygienists = set(clinic_config.get('hygienists', []))
            memos = clinic_config.get('memos', {})
            thresholds = clinic_config.get('slot_threshold', {})
            dr_threshold = thresholds.get('doctor', 30)
            dh_threshold = thresholds.get('hygienist', 30)

            doctor_blocks = 0
            hygienist_blocks = 0
            other_blocks = 0

            for detail in result.get('details', []):
                staff_name = detail.get('doctor', '')

                # 職種分類と再計算
                if staff_name in doctors:
                    detail['category'] = 'doctor'
                    _recalculate_detail(detail, dr_threshold)
                    detail.setdefault('threshold_minutes', dr_threshold)
                elif staff_name in hygienists:
                    detail['category'] = 'hygienist'
                    _recalculate_detail(detail, dh_threshold)
                    detail.setdefault('threshold_minutes', dh_threshold)
                else:
                    detail['category'] = 'unknown'
                    _recalculate_detail(detail, 30)
                    detail.setdefault('threshold_minutes', 30)

                blocks = detail.get('blocks', 0)
                if detail['category'] == 'doctor':
                    doctor_blocks += blocks
                elif detail['category'] == 'hygienist':
                    hygienist_blocks += blocks
                else:
                    other_blocks += blocks

                # メモを追加
                detail['memo'] = memos.get(staff_name, '')

            # 職種別集計を追加
            result['category_summary'] = {
                'doctor': doctor_blocks,
                'hygienist': hygienist_blocks,
                'other': other_blocks
            }
            result['slot_threshold'] = {
                'doctor': dr_threshold,
                'hygienist': dh_threshold
            }

        # WEB予約受付フィルタを適用（職種分類後に実行）
        settings = load_clinics_settings()
        data = apply_web_booking_filter(data, staff_rules, settings)

        return jsonify(data)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _read_log_tail(log_path, lines=10):
    """ログファイルの末尾を読む"""
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
        return ''.join(all_lines[-lines:]).strip()
    except Exception:
        return ''


@bp.route('/check', methods=['POST'])
def run_check():
    """手動でチェックを実行（Popenでバックグラウンド起動）"""
    global _check_process, _check_log_file, _check_started_at
    project_root = current_app.config['PROJECT_ROOT']

    # 既に実行中なら拒否
    if _check_process and _check_process.poll() is None:
        elapsed = int(time.time() - (_check_started_at or time.time()))
        return jsonify({
            'success': False,
            'message': f'既にチェック実行中です（{elapsed}秒経過）'
        }), 409

    # システムフィルタ取得
    data = request.get_json(silent=True) or {}
    system_filter = data.get('system')  # 'dent-sys', 'stransa', or None

    # ログディレクトリ確保
    log_dir = os.path.join(project_root, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'check_latest.log')

    # 前回のログファイルハンドルを閉じる
    if _check_log_file and not _check_log_file.closed:
        _check_log_file.close()

    _check_log_file = open(log_path, 'w', encoding='utf-8', buffering=1)
    _check_started_at = time.time()

    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'

    cmd = [sys.executable, '-m', 'src.main']
    if system_filter in ('dent-sys', 'stransa'):
        cmd.extend(['--system', system_filter])

    _check_process = subprocess.Popen(
        cmd,
        stdout=_check_log_file,
        stderr=subprocess.STDOUT,
        cwd=project_root,
        env=env
    )

    system_label = {'dent-sys': 'dent-sys', 'stransa': 'Stransa'}.get(system_filter, '全システム')
    return jsonify({
        'success': True,
        'message': f'{system_label}のチェックを開始しました'
    })


@bp.route('/check/status', methods=['GET'])
def check_status():
    """チェック実行状態を取得"""
    global _check_process, _check_log_file
    project_root = current_app.config['PROJECT_ROOT']
    log_path = os.path.join(project_root, 'logs', 'check_latest.log')

    if _check_process is None:
        return jsonify({
            'running': False,
            'success': None,
            'message': 'チェック未実行',
            'elapsed': 0,
        })

    ret = _check_process.poll()
    elapsed = int(time.time() - (_check_started_at or time.time()))

    if ret is None:
        # タイムアウトチェック
        if elapsed > _CHECK_TIMEOUT:
            _check_process.kill()
            if _check_log_file and not _check_log_file.closed:
                _check_log_file.close()
            log_tail = _read_log_tail(log_path, 10)
            return jsonify({
                'running': False,
                'success': False,
                'message': f'タイムアウト ({_CHECK_TIMEOUT}秒)',
                'error': log_tail,
                'elapsed': elapsed,
            })
        # まだ実行中
        log_tail = _read_log_tail(log_path, 3)
        return jsonify({
            'running': True,
            'success': None,
            'message': 'チェック実行中...',
            'elapsed': elapsed,
            'log_tail': log_tail,
        })

    # 完了 — ログファイルを閉じる
    if _check_log_file and not _check_log_file.closed:
        _check_log_file.close()

    # GCSからの新しいoutputを次回読み込みで取得するためフラグリセット
    global _output_synced
    _output_synced = False

    if ret == 0:
        return jsonify({
            'running': False,
            'success': True,
            'message': 'チェック完了',
            'elapsed': elapsed,
        })
    else:
        error_detail = _read_log_tail(log_path, 15)
        return jsonify({
            'running': False,
            'success': False,
            'message': f'チェック失敗 (exit code {ret})',
            'error': error_detail,
            'elapsed': elapsed,
        })


@bp.route('/check/test-connectivity', methods=['GET'])
def test_connectivity():
    """Stransa サイトへの接続テスト"""
    import urllib.request
    import ssl

    urls = [
        'https://apo-toolboxes.stransa.co.jp/',
        'https://user.stransa.co.jp/login',
        'https://www.google.com/',
    ]
    results = {}
    ctx = ssl.create_default_context()

    for url in urls:
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0'
            })
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            results[url] = {'status': resp.status, 'ok': True}
        except Exception as e:
            results[url] = {'error': str(e), 'ok': False}

    return jsonify(results)


@bp.route('/check/log', methods=['GET'])
def check_log():
    """チェック実行ログ全文を返す"""
    project_root = current_app.config['PROJECT_ROOT']
    log_path = os.path.join(project_root, 'logs', 'check_latest.log')

    if not os.path.exists(log_path):
        return 'No log file', 404, {'Content-Type': 'text/plain; charset=utf-8'}

    with open(log_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return content, 200, {'Content-Type': 'text/plain; charset=utf-8'}


@bp.route('/check/screenshots', methods=['GET'])
def check_screenshots():
    """デバッグスクリーンショット一覧を返す"""
    project_root = current_app.config['PROJECT_ROOT']
    ss_dir = os.path.join(project_root, 'logs', 'screenshots')

    if not os.path.isdir(ss_dir):
        return jsonify({'screenshots': []})

    files = sorted(
        [f for f in os.listdir(ss_dir) if f.endswith('.png')],
        key=lambda f: os.path.getmtime(os.path.join(ss_dir, f)),
        reverse=True
    )
    return jsonify({
        'screenshots': [
            {'filename': f, 'url': f'/api/results/check/screenshots/{f}'}
            for f in files
        ]
    })


@bp.route('/check/screenshots/<filename>', methods=['GET'])
def check_screenshot_file(filename):
    """個別のデバッグスクリーンショットを返す"""
    # パストラバーサル防止
    if '/' in filename or '\\' in filename or '..' in filename:
        return 'Invalid filename', 400

    project_root = current_app.config['PROJECT_ROOT']
    file_path = os.path.join(project_root, 'logs', 'screenshots', filename)

    if not os.path.exists(file_path):
        return 'Not found', 404

    return send_file(file_path, mimetype='image/png')
