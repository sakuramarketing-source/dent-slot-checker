"""Flask アプリケーション"""

import os
import sys
from flask import Flask

# 親ディレクトリをパスに追加（srcモジュールを使用するため）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.routes import main, staff, clinics, rules, results


def create_app():
    """Flaskアプリケーションを作成"""
    app = Flask(__name__)

    # 設定
    app.config['SECRET_KEY'] = 'dent-slot-checker-secret-key'
    app.config['JSON_AS_ASCII'] = False  # 日本語をそのまま表示
    app.json.sort_keys = False  # 辞書の挿入順序を保持

    # プロジェクトルートパスを設定
    app.config['PROJECT_ROOT'] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app.config['CONFIG_PATH'] = os.path.join(app.config['PROJECT_ROOT'], 'config')
    app.config['OUTPUT_PATH'] = os.path.join(app.config['PROJECT_ROOT'], 'output')

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
