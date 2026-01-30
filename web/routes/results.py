"""結果表示API"""

import os
import json
import glob
import subprocess
import sys
import yaml
import asyncio
import threading
import queue
from flask import Blueprint, jsonify, request, current_app, Response

bp = Blueprint('results', __name__)


def load_staff_rules():
    """staff_rules.yamlを読み込む"""
    config_path = current_app.config['CONFIG_PATH']
    staff_rules_path = os.path.join(config_path, 'staff_rules.yaml')

    if not os.path.exists(staff_rules_path):
        return {'staff_by_clinic': {}}

    with open(staff_rules_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {'staff_by_clinic': {}}


def get_result_files():
    """結果ファイルのリストを取得"""
    output_path = current_app.config['OUTPUT_PATH']
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

            doctor_blocks = 0
            hygienist_blocks = 0
            other_blocks = 0

            for detail in result.get('details', []):
                staff_name = detail.get('doctor', '')
                blocks = detail.get('blocks', 0)

                # 職種分類
                if staff_name in doctors:
                    detail['category'] = 'doctor'
                    doctor_blocks += blocks
                elif staff_name in hygienists:
                    detail['category'] = 'hygienist'
                    hygienist_blocks += blocks
                else:
                    detail['category'] = 'unknown'
                    other_blocks += blocks

                # メモを追加
                detail['memo'] = memos.get(staff_name, '')

            # 職種別集計を追加
            result['category_summary'] = {
                'doctor': doctor_blocks,
                'hygienist': hygienist_blocks,
                'other': other_blocks
            }

        return jsonify(data)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/check', methods=['POST'])
def run_check():
    """手動でチェックを実行"""
    project_root = current_app.config['PROJECT_ROOT']

    try:
        # サブプロセスでチェックを実行（モジュールとして実行）
        result = subprocess.run(
            [sys.executable, '-m', 'src.main'],
            capture_output=True,
            text=True,
            cwd=project_root,
            timeout=600  # 10分タイムアウト
        )

        if result.returncode == 0:
            return jsonify({
                'success': True,
                'message': 'Check completed successfully',
                'output': result.stdout
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Check failed',
                'error': result.stderr
            }), 500

    except subprocess.TimeoutExpired:
        return jsonify({
            'success': False,
            'message': 'Check timed out'
        }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@bp.route('/check-stream', methods=['GET'])
def run_check_stream():
    """SSEで進捗を送信しながらチェックを実行"""
    project_root = current_app.config['PROJECT_ROOT']

    def generate():
        progress_queue = queue.Queue()
        result_holder = {'result': None, 'error': None}

        def progress_callback(clinic_name, current, total):
            progress_queue.put({
                'type': 'progress',
                'clinic': clinic_name,
                'current': current,
                'total': total
            })

        def run_async_check():
            try:
                # src.mainをインポート
                import sys
                sys.path.insert(0, project_root)
                from src.main import run_with_progress

                # asyncio.run()で非同期関数を実行
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(
                        run_with_progress(
                            progress_callback=progress_callback,
                            headless=True,
                            output_formats=['json']
                        )
                    )
                    result_holder['result'] = result
                finally:
                    loop.close()

            except Exception as e:
                import traceback
                result_holder['error'] = str(e) + '\n' + traceback.format_exc()
            finally:
                progress_queue.put({'type': 'done'})

        # 別スレッドで非同期チェックを実行
        thread = threading.Thread(target=run_async_check)
        thread.start()

        # 進捗をSSEとして送信
        while True:
            try:
                msg = progress_queue.get(timeout=300)  # 5分タイムアウト

                if msg['type'] == 'progress':
                    data = json.dumps({
                        'type': 'progress',
                        'clinic': msg['clinic'],
                        'current': msg['current'],
                        'total': msg['total']
                    }, ensure_ascii=False)
                    yield f"data: {data}\n\n"

                elif msg['type'] == 'done':
                    if result_holder['error']:
                        data = json.dumps({
                            'type': 'error',
                            'message': result_holder['error']
                        }, ensure_ascii=False)
                    else:
                        data = json.dumps({
                            'type': 'complete',
                            'summary': result_holder['result'].get('summary', {})
                        }, ensure_ascii=False)
                    yield f"data: {data}\n\n"
                    break

            except queue.Empty:
                # タイムアウト
                data = json.dumps({
                    'type': 'error',
                    'message': 'Timeout waiting for progress'
                }, ensure_ascii=False)
                yield f"data: {data}\n\n"
                break

        thread.join(timeout=10)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )
