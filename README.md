# dent-slot-checker

歯科予約システムの空き枠を自動チェックし、Web管理画面で可視化するツール。

## 概要

さくら会グループの各分院が利用する予約システム（dent-sys.net / Stransa）にログインし、翌日の予約状況をスクレイピング。30分以上の連続空き枠を検出し、分院ごとの空き状況をダッシュボードで確認できる。

## 対応システム

| システム | スロット間隔 | 30分空き枠の判定 |
|---------|-----------|----------------|
| dent-sys.net | 5分 | 6連続スロット |
| Stransa (Apotool & Box) | 15分 | 2連続スロット |

## 機能

- **ダッシュボード**: 最新チェック結果の一覧表示、タイムライン可視化、手動チェック実行
- **スタッフ管理**: スタッフの職種分類（Dr/DH）、有効/無効切替、メモ・タグ
- **分院管理**: 登録分院の一覧・有効/無効切替
- **ルール設定**: 最小空き枠数、除外パターン等の設定
- **結果一覧**: 過去のチェック結果を日付別に閲覧

## データフロー

```
予約システム (dent-sys.net / Stransa)
  ↓ Playwright スクレイピング
空きスロット収集 (5分/15分刻み)
  ↓ slot_analyzer.py
30分空き枠の検出 & 集計
  ↓ output_writer.py
JSON/CSV 出力 → Web画面で表示
```

## セットアップ

### ローカル起動

```bash
# 依存パッケージをインストール
pip install -r requirements.txt

# Playwrightブラウザをインストール
playwright install chromium

# 設定ファイルを作成
cp config/clinics.yaml.example config/clinics.yaml
# clinics.yaml に各分院のURL・ID・パスワードを設定

# Web管理画面を起動
python run_web.py
# → http://localhost:8080
```

### Docker

```bash
docker build -t dent-slot-checker .
docker run -p 8080:8080 dent-slot-checker
```

### Cloud Run デプロイ

```bash
gcloud run deploy dent-slot-checker \
  --source . \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --memory 1Gi
```

## 設定ファイル

### config/clinics.yaml

```yaml
clinics:                    # dent-sys.net 分院
  - name: "分院名"
    url: "https://www.dent-sys.net/..."
    id: "ログインID"
    password: "パスワード"
    enabled: true

stransa_clinics:            # Stransa 分院
  - name: "医院名"
    url: "https://user.stransa.co.jp/login"
    id: "メールアドレス"
    password: "パスワード"
    enabled: true

settings:
  consecutive_slots_required: 6   # 30分空き枠に必要な連続スロット数
  minimum_blocks_required: 4      # 不足判定の最小空き枠数
  slot_interval_minutes: 5        # dent-sys スロット間隔
  exclude_patterns: ["訪問"]       # 除外するスタッフ名パターン
```

### config/staff_rules.yaml

```yaml
staff_by_clinic:
  "分院名":
    doctors: ["橋本", "田中"]       # Dr分類
    hygienists: ["鈴木", "佐藤"]    # DH分類
    disabled: ["訪問太郎"]          # 無効化スタッフ
    memos:
      "橋本": "院長"
```

## ディレクトリ構成

```
dent-slot-checker/
  config/          設定ファイル (clinics.yaml, staff_rules.yaml)
  output/          チェック結果 (JSON/CSV)
  src/
    main.py        メイン処理（スクレイピング→解析→出力）
    scraper.py     dent-sys.net スクレイパー
    scraper_stransa.py  Stransa スクレイパー
    slot_analyzer.py    空きスロット解析
    config_loader.py    設定読み込み
    output_writer.py    結果出力
  web/
    app.py         Flask アプリケーション
    routes/        APIルート (main, results, staff, clinics, rules)
    templates/     HTMLテンプレート
    static/        CSS/JS
  run_web.py       Web管理画面起動スクリプト
  Dockerfile       コンテナビルド設定
```

## 更新履歴

- **2025-02-19** タイムライン表示をハイブリッド型に改善（上部タイムライン + 詳細タグ表示）
- **2025-02-19** タイムライン可視化を追加（ダッシュボード・結果一覧）
- **2025-02-19** 初期コミット: Flask Web管理画面付きの空き枠チェッカー
