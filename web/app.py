"""Flask アプリケーション"""

import os
import sys
from flask import Flask, request, g, jsonify

# 親ディレクトリをパスに追加（srcモジュールを使用するため）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.routes import main, staff, clinics, rules, results


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
