"""結果表示API"""

import os
import json
import glob
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
_check_started_at = None
_check_thread = None
_check_result = None  # None=未実行, True=成功, False=失敗
_check_error = None
_CHECK_TIMEOUT = 600  # 10分タイムアウト


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
            # web_booking未設定 → 結果をクリア（WEBタグ未設定 = 集計対象外）
            result['details'] = []
            result['total_30min_blocks'] = 0
            result['result'] = False
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


def _merge_with_latest(new_results, checked_system, output_path_str, check_date):
    """部分チェック時に他システムの結果を最新ファイルからマージ"""
    import glob as _glob

    date_str = check_date.replace('-', '')
    pattern = os.path.join(output_path_str, f'slot_check_{date_str}_*.json')
    files = _glob.glob(pattern)

    if not files:
        return None

    latest = max(files, key=os.path.getmtime)
    try:
        with open(latest, 'r', encoding='utf-8') as f:
            prev_data = json.load(f)
    except Exception:
        return None

    # 他システムの結果を保持
    other_system = [
        r for r in prev_data.get('results', [])
        if r.get('system') != checked_system
    ]

    if not other_system:
        return None

    # 新しい結果 + 他システムの結果をマージ
    merged_results = new_results + other_system
    return {'results': merged_results}


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
            orthodontists = set(clinic_config.get('orthodontists', []))
            memos = clinic_config.get('memos', {})
            thresholds = clinic_config.get('slot_threshold', {})
            dr_threshold = thresholds.get('doctor', 30)
            dh_threshold = thresholds.get('hygienist', 30)
            ortho_threshold = thresholds.get('orthodontist', 30)

            doctor_blocks = 0
            hygienist_blocks = 0
            orthodontist_blocks = 0
            other_blocks = 0

            for detail in result.get('details', []):
                staff_name = detail.get('doctor', '')

                # 職種分類と再計算（矯正が最優先）
                if staff_name in orthodontists:
                    detail['category'] = 'orthodontist'
                    _recalculate_detail(detail, ortho_threshold)
                    detail.setdefault('threshold_minutes', ortho_threshold)
                elif staff_name in doctors:
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
                elif detail['category'] == 'orthodontist':
                    orthodontist_blocks += blocks
                else:
                    other_blocks += blocks

                # メモを追加
                detail['memo'] = memos.get(staff_name, '')

            # 職種別集計を追加
            result['category_summary'] = {
                'doctor': doctor_blocks,
                'hygienist': hygienist_blocks,
                'orthodontist': orthodontist_blocks,
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
    """手動でチェックを実行（ブラウザプール利用のインプロセス実行）"""
    global _check_thread, _check_started_at, _check_result, _check_error
    project_root = current_app.config['PROJECT_ROOT']

    # 既に実行中なら拒否
    if _check_thread and _check_thread.is_alive():
        elapsed = int(time.time() - (_check_started_at or time.time()))
        return jsonify({
            'success': False,
            'message': f'既にチェック実行中です（{elapsed}秒経過）'
        }), 409

    # システムフィルタ取得
    data = request.get_json(silent=True) or {}
    system_filter = data.get('system')  # 'dent-sys', 'stransa', or None

    # ログセットアップ
    log_dir = os.path.join(project_root, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'check_latest.log')

    _check_started_at = time.time()
    _check_result = None
    _check_error = None

    # 設定読み込みに必要な情報をキャプチャ
    config_path = current_app.config['CONFIG_PATH']
    output_path = current_app.config['OUTPUT_PATH']

    def _run_check_thread():
        global _check_result, _check_error, _output_synced
        import logging

        # ファイルハンドラでログを check_latest.log に出力
        file_handler = logging.FileHandler(log_path, mode='w', encoding='utf-8')
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        # stderr にも出力（Cloud Logging用）
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)
        root_logger.addHandler(stream_handler)
        root_logger.setLevel(logging.INFO)

        try:
            import asyncio as _asyncio
            from src.browser_pool import get_browser, run_async
            from src.config_loader import load_config, get_exclude_patterns, get_slot_settings
            from src.main import analyze_results
            from src.output_writer import save_results
            from pathlib import Path
            from datetime import datetime, timedelta, timezone

            logger_t = logging.getLogger('check_thread')
            logger_t.info("インプロセスチェック開始")

            browser = get_browser()
            logger_t.info(f"ブラウザ取得完了: {browser}")

            config = load_config(Path(config_path))
            exclude_patterns = config['settings'].get('exclude_patterns', ['訪問'])
            slot_settings = {
                'consecutive_slots_required': config['settings'].get('consecutive_slots_required', 6),
                'minimum_blocks_required': config['settings'].get('minimum_blocks_required', 4),
                'slot_interval_minutes': config['settings'].get('slot_interval_minutes', 5),
            }

            # staff_rules
            import yaml
            sr_path = Path(config_path) / 'staff_rules.yaml'
            staff_by_clinic = {}
            if sr_path.exists():
                with open(sr_path, 'r', encoding='utf-8') as f:
                    sr_data = yaml.safe_load(f) or {}
                staff_by_clinic = sr_data.get('staff_by_clinic', {})

            dent_sys_clinics = [c for c in config.get('dent_sys_clinics', []) if c.get('enabled', True)]
            stransa_clinics = [c for c in config.get('stransa_clinics', []) if c.get('enabled', True)]

            if system_filter == 'dent-sys':
                stransa_clinics = []
            elif system_filter == 'stransa':
                dent_sys_clinics = []

            logger_t.info(f"dent-sys: {len(dent_sys_clinics)}分院, Stransa: {len(stransa_clinics)}分院")

            all_results = []
            total_clinics = 0
            clinics_with_availability = 0

            # dent-sys + Stransa を逐次実行（ブラウザリソース競合回避）
            # 並列実行(rev00044)でFrame detached/マッピング失敗が多発したため逐次に戻す
            async def _scrape_all():
                from src.scraper_stransa import scrape_all_stransa_clinics
                from src.scraper import scrape_all_clinics
                results = []

                if stransa_clinics:
                    logger_t.info("=== Stransa スクレイピング開始 ===")
                    try:
                        r = await scrape_all_stransa_clinics(stransa_clinics, browser=browser)
                        results.append(('stransa', r))
                        logger_t.info(f"=== Stransa 完了: {len(r)}分院 ===")
                    except Exception as e:
                        results.append(('stransa', e))
                        logger_t.error(f"Stransa失敗: {e}")

                if dent_sys_clinics:
                    logger_t.info("=== dent-sys スクレイピング開始 ===")
                    try:
                        r = await scrape_all_clinics(
                            dent_sys_clinics, exclude_patterns,
                            slot_settings['slot_interval_minutes'],
                            True, str(config_path), browser=browser
                        )
                        results.append(('dent-sys', r))
                        logger_t.info(f"=== dent-sys 完了: {len(r)}分院 ===")
                    except Exception as e:
                        results.append(('dent-sys', e))
                        logger_t.error(f"dent-sys失敗: {e}")

                return results

            scrape_results = run_async(_scrape_all())

            for label, result in scrape_results:
                if isinstance(result, Exception):
                    logger_t.error(f"{label} スクレイピング失敗: {result}")
                    continue
                analysis = analyze_results(result, slot_settings, label, staff_by_clinic)
                all_results.extend(analysis['results'])
                total_clinics += analysis['summary']['total_clinics']
                clinics_with_availability += analysis['summary']['clinics_with_availability']
                logger_t.info(f"{label}完了: {analysis['summary']}")

            # 部分チェックの場合、他システムの結果をマージ
            JST = timezone(timedelta(hours=9))
            check_date = (datetime.now(JST) + timedelta(days=1)).strftime('%Y-%m-%d')

            if system_filter and all_results:
                merged = _merge_with_latest(
                    all_results, system_filter, output_path, check_date
                )
                if merged:
                    all_results = merged['results']
                    total_clinics = len(all_results)
                    clinics_with_availability = sum(
                        1 for r in all_results if r.get('result', False)
                    )
                    logger_t.info(f"既存結果とマージ完了: {total_clinics}分院")

            # 統合結果
            combined = {
                'check_date': check_date,
                'checked_at': datetime.now(JST).isoformat(),
                'results': all_results,
                'summary': {
                    'total_clinics': total_clinics,
                    'clinics_with_availability': clinics_with_availability
                }
            }

            output_dir = Path(output_path)
            saved = save_results(combined, output_dir, ['json', 'csv'])
            for f in saved:
                logger_t.info(f"結果保存: {f}")

            _check_result = True
            _output_synced = False
            logger_t.info("チェック完了")

        except Exception as e:
            _check_result = False
            _check_error = str(e)
            logging.getLogger('check_thread').error(f"チェック失敗: {e}")
            import traceback
            traceback.print_exc()
        finally:
            root_logger.removeHandler(file_handler)
            root_logger.removeHandler(stream_handler)
            file_handler.close()

    import threading
    _check_thread = threading.Thread(target=_run_check_thread, daemon=True)
    _check_thread.start()

    system_label = {'dent-sys': 'dent-sys', 'stransa': 'Stransa'}.get(system_filter, '全システム')
    return jsonify({
        'success': True,
        'message': f'{system_label}のチェックを開始しました'
    })


@bp.route('/check/status', methods=['GET'])
def check_status():
    """チェック実行状態を取得"""
    global _check_thread
    project_root = current_app.config['PROJECT_ROOT']
    log_path = os.path.join(project_root, 'logs', 'check_latest.log')

    if _check_thread is None:
        return jsonify({
            'running': False,
            'success': None,
            'message': 'チェック未実行',
            'elapsed': 0,
        })

    elapsed = int(time.time() - (_check_started_at or time.time()))

    if _check_thread.is_alive():
        # タイムアウトチェック
        if elapsed > _CHECK_TIMEOUT:
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

    # 完了
    global _output_synced
    _output_synced = False

    if _check_result:
        return jsonify({
            'running': False,
            'success': True,
            'message': 'チェック完了',
            'elapsed': elapsed,
        })
    else:
        error_detail = _check_error or _read_log_tail(log_path, 15)
        return jsonify({
            'running': False,
            'success': False,
            'message': 'チェック失敗',
            'error': error_detail,
            'elapsed': elapsed,
        })


@bp.route('/check/test-connectivity', methods=['GET'])
def test_connectivity():
    """Stransa サイトへの接続テスト（urllib + Playwright）"""
    import urllib.request
    import ssl
    import asyncio

    urls = [
        'https://apo-toolboxes.stransa.co.jp/',
        'https://user.stransa.co.jp/login',
        'https://www.google.com/',
    ]
    results = {'urllib': {}, 'playwright': {}}
    ctx = ssl.create_default_context()

    # urllib テスト
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0'
            })
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            results['urllib'][url] = {'status': resp.status, 'ok': True}
        except Exception as e:
            results['urllib'][url] = {'error': str(e), 'ok': False}

    # Playwright テスト（google.comのみ、軽量に）
    async def test_playwright():
        from playwright.async_api import async_playwright
        pw_results = {}
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
                )
                page = await browser.new_page()
                for url in ['https://www.google.com/', 'https://apo-toolboxes.stransa.co.jp/']:
                    try:
                        resp = await page.goto(url, wait_until='commit', timeout=30000)
                        pw_results[url] = {
                            'status': resp.status if resp else None,
                            'ok': True
                        }
                    except Exception as e:
                        pw_results[url] = {'error': str(e)[:200], 'ok': False}
                await browser.close()
        except Exception as e:
            pw_results['_error'] = str(e)[:200]
        return pw_results

    try:
        results['playwright'] = asyncio.run(test_playwright())
    except Exception as e:
        results['playwright'] = {'_error': str(e)[:200]}

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
