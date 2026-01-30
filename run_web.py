"""Flask Webアプリケーション起動スクリプト"""

from web.app import create_app

if __name__ == '__main__':
    app = create_app()
    print("=" * 50)
    print("予約空き状況チェッカー Web管理画面")
    print("=" * 50)
    print("ブラウザで http://localhost:8080 を開いてください")
    print("終了するには Ctrl+C を押してください")
    print("=" * 50)
    app.run(debug=True, host='0.0.0.0', port=8080)
