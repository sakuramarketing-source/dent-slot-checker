"""Flask アプリケーション"""

import os
import sys
import logging
from flask import Flask, request, g, jsonify

# 親ディレクトリをパスに追加（srcモジュールを使用するため）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.routes import main, staff, clinics, rules, results


def _merge_staff_rules(local_data, gcs_data):
    """Docker image（コード管理）とGCS（ユーザー管理）をマージ

    コード管理キー（unit_check等）はDocker imageから、
    ユーザー管理キー（web_booking, memos等）はGCSから保持。
    """
    import copy
    merged = copy.deepcopy(local_data)

    # ユーザーがダッシュボードで管理するキー（GCSから保持）
    USER_KEYS = {
        'web_booking', 'memos', 'tags', 'disabled', 'slot_threshold',
        'doctors', 'hygienists', 'orthodontists', 'all_staff',
    }

    gcs_clinics = gcs_data.get('staff_by_clinic', {})
    merged_clinics = merged.setdefault('staff_by_clinic', {})

    for clinic_name, gcs_config in gcs_clinics.items():
        if clinic_name not in merged_clinics:
            continue  # Docker imageにないクリニックはスキップ（削除済み）
        else:
            for key in USER_KEYS:
                if key in gcs_config:
                    merged_clinics[clinic_name][key] = gcs_config[key]

    return merged


def _sync_gcs_on_startup(config_path: str):
    """起動時にGCSのユーザー設定とDocker imageの構造設定をマージ"""
    try:
        import yaml
        import tempfile
        from src.gcs_helper import download_from_gcs, upload_to_gcs

        staff_rules_path = os.path.join(config_path, 'staff_rules.yaml')

        # GCSから一時ファイルにダウンロード
        tmp_path = os.path.join(tempfile.gettempdir(), 'gcs_staff_rules.yaml')
        if not download_from_gcs('config/staff_rules.yaml', tmp_path):
            print("[STARTUP] GCS同期スキップ（ローカル環境 or GCSにファイルなし）", flush=True)
            staff._gcs_loaded = True
            return

        # Docker image版とGCS版をマージ
        with open(staff_rules_path, 'r', encoding='utf-8') as f:
            local_data = yaml.safe_load(f) or {}
        with open(tmp_path, 'r', encoding='utf-8') as f:
            gcs_data = yaml.safe_load(f) or {}

        merged = _merge_staff_rules(local_data, gcs_data)

        with open(staff_rules_path, 'w', encoding='utf-8') as f:
            yaml.dump(merged, f, allow_unicode=True, default_flow_style=False)

        # マージ結果をGCSにもアップロード
        upload_to_gcs(staff_rules_path, 'config/staff_rules.yaml')

        print("[STARTUP] GCSマージ同期完了: staff_rules.yaml", flush=True)
        staff._gcs_loaded = True
    except Exception as e:
        print(f"[STARTUP] GCS同期失敗: {e}", flush=True)


def create_app():
    """Flaskアプリケーションを作成"""
    app = Flask(__name__)

    # 設定
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
    app.config['JSON_AS_ASCII'] = False  # 日本語をそのまま表示
    app.json.sort_keys = False  # 辞書の挿入順序を保持

    # プロジェクトルートパスを設定
    app.config['PROJECT_ROOT'] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app.config['CONFIG_PATH'] = os.path.join(app.config['PROJECT_ROOT'], 'config')
    app.config['OUTPUT_PATH'] = os.path.join(app.config['PROJECT_ROOT'], 'output')

    # IAPヘッダーからユーザー情報を取得
    @app.before_request
    def set_user_from_iap():
        email = request.headers.get('X-Goog-Authenticated-User-Email', '')
        g.user_email = email.replace('accounts.google.com:', '') if email else None

    @app.context_processor
    def inject_user():
        return {'user_email': getattr(g, 'user_email', None)}

    # APIエンドポイント用JSONエラーハンドラ
    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Not found', 'success': False}), 404
        return e

    @app.errorhandler(500)
    def internal_error(e):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Internal server error', 'success': False}), 500
        return e

    # Cloud Run起動時にGCSから最新のstaff_rules.yamlをダウンロード
    _sync_gcs_on_startup(app.config['CONFIG_PATH'])

    # Playwright/Chromiumブラウザをバックグラウンドで事前起動
    import threading as _th
    from src.browser_pool import init_browser
    _th.Thread(target=init_browser, daemon=True).start()

    # Blueprintを登録
    app.register_blueprint(main.bp)
    app.register_blueprint(staff.bp, url_prefix='/api/staff')
    app.register_blueprint(clinics.bp, url_prefix='/api/clinics')
    app.register_blueprint(rules.bp, url_prefix='/api/rules')
    app.register_blueprint(results.bp, url_prefix='/api/results')

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
