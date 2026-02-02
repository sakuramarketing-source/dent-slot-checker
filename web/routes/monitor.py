"""監視ダッシュボードルート"""

import os
import json
import glob
from flask import Blueprint, render_template, jsonify, current_app, make_response

bp = Blueprint('monitor', __name__)


def get_report_files():
    """スクレイピングレポートファイルの一覧を取得"""
    output_path = current_app.config['OUTPUT_PATH']
    report_files = glob.glob(os.path.join(output_path, 'scrape_report_*.json'))

    files = []
    for f in report_files:
        basename = os.path.basename(f)
        # scrape_report_YYYYMMDD_HHMMSS.json
        parts = basename.replace('.json', '').split('_')
        if len(parts) >= 4:
            run_id = f"{parts[2]}_{parts[3]}"
            files.append({
                'filename': basename,
                'run_id': run_id,
                'path': f,
                'mtime': os.path.getmtime(f)
            })

    files.sort(key=lambda x: x['mtime'], reverse=True)
    return files


def get_log_file_for_run(run_id: str) -> str:
    """実行IDに対応するログファイルを取得"""
    project_root = current_app.config['PROJECT_ROOT']
    log_dir = os.path.join(project_root, 'logs')

    # run_id format: YYYYMMDD_HHMMSS
    pattern = os.path.join(log_dir, f'slot_checker_{run_id}*.log')
    logs = glob.glob(pattern)

    if logs:
        return logs[0]

    # フォールバック: 日付で近いログを探す
    parts = run_id.split('_')
    if len(parts) >= 2:
        date_pattern = os.path.join(log_dir, f'slot_checker_{parts[0]}_*.log')
        logs = glob.glob(date_pattern)
        if logs:
            return max(logs, key=os.path.getmtime)

    return None


@bp.route('/')
def monitor_page():
    """監視ダッシュボードページ"""
    response = make_response(render_template('monitor.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@bp.route('/api/reports')
def get_reports():
    """レポート一覧を取得"""
    files = get_report_files()

    reports = []
    for f in files[:50]:  # 最新50件まで
        try:
            with open(f['path'], 'r', encoding='utf-8') as fp:
                data = json.load(fp)
                reports.append({
                    'run_id': f['run_id'],
                    'started_at': data.get('started_at'),
                    'completed_at': data.get('completed_at'),
                    'duration_seconds': data.get('duration_seconds'),
                    'status': data.get('status'),
                    'check_date': data.get('check_date'),
                    'summary': data.get('summary', {})
                })
        except Exception:
            continue

    return jsonify(reports)


@bp.route('/api/reports/<run_id>')
def get_report(run_id):
    """特定のレポート詳細を取得"""
    output_path = current_app.config['OUTPUT_PATH']
    report_path = os.path.join(output_path, f'scrape_report_{run_id}.json')

    if not os.path.exists(report_path):
        return jsonify({'error': 'Report not found'}), 404

    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/logs/<run_id>')
def get_logs(run_id):
    """実行のログファイル内容を取得"""
    log_path = get_log_file_for_run(run_id)

    if not log_path or not os.path.exists(log_path):
        return jsonify({'error': 'Log file not found'}), 404

    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({
            'filename': os.path.basename(log_path),
            'content': content
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/issues')
def get_issues():
    """最新レポートから問題を集計"""
    files = get_report_files()

    if not files:
        return jsonify({'error': 'No reports found'}), 404

    try:
        with open(files[0]['path'], 'r', encoding='utf-8') as f:
            data = json.load(f)

        issues = {
            'zero_slots': [],
            'excluded_by_pattern': [],
            'disabled_staff': [],
            'clinic_warnings': [],
            'clinic_errors': []
        }

        for clinic in data.get('clinics', []):
            clinic_name = clinic.get('name', '')

            # スロット0のスタッフを収集
            for staff in clinic.get('staff_zero_slots', []):
                issues['zero_slots'].append({
                    'clinic': clinic_name,
                    'staff': staff
                })

            # スキップされたスタッフを理由別に収集
            for staff in clinic.get('staff_skipped', []):
                reason = staff.get('reason', '')
                if reason == 'exclude_pattern':
                    issues['excluded_by_pattern'].append({
                        'clinic': clinic_name,
                        'staff': staff.get('name'),
                        'pattern': staff.get('pattern')
                    })
                elif reason == 'disabled':
                    issues['disabled_staff'].append({
                        'clinic': clinic_name,
                        'staff': staff.get('name')
                    })

            # 分院の警告/エラーを収集
            if clinic.get('status') == 'warning':
                issues['clinic_warnings'].append({
                    'clinic': clinic_name,
                    'message': clinic.get('warning_message', '')
                })
            elif clinic.get('status') == 'error':
                issues['clinic_errors'].append({
                    'clinic': clinic_name,
                    'error': clinic.get('error', '')
                })

        return jsonify(issues)

    except Exception as e:
        return jsonify({'error': str(e)}), 500
