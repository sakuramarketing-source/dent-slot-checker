# Dent Slot Checker

歯科医院の予約空き枠自動チェックシステム

## 概要

複数の歯科医院の予約システムをスクレイピングし、空き枠を自動チェックするWebアプリケーション。

## 機能

- **手動チェック**: Web UIから手動で空き枠チェックを実行
- **複数システム対応**: dent-sys.net および Stransa予約システムに対応
- **リアルタイム進捗表示**: チェック進捗をリアルタイムで表示
- **結果の可視化**: 分院ごとの空き枠状況をダッシュボードに表示

## GCPデプロイ時の初回セットアップ

### 1. GCS設定ファイルのアップロード

初回デプロイ前、またはclinics.yaml更新時に実行:

```bash
# セットアップスクリプトを実行
./scripts/setup_gcs.sh

# または手動で
gsutil cp config/clinics.yaml gs://dent-slot-checker-houmo-config/config/clinics.yaml
gsutil cp config/staff_rules.yaml gs://dent-slot-checker-houmo-config/config/staff_rules.yaml
```

### 2. デプロイ前チェック

```bash
./scripts/pre_deploy_check.sh
```

### 3. デプロイ

```bash
git push  # GitHub → GCP自動デプロイ
```

## ローカル開発

### 前提条件

- Python 3.11+
- Google Chrome (Playwright用)

### セットアップ

```bash
# 依存パッケージインストール
pip install -r requirements.txt

# Playwrightブラウザインストール
playwright install chromium

# Webサーバー起動
python run_web.py
```

### 設定ファイル

- `config/clinics.yaml`: 分院設定（ID/パスワード含む）
- `config/staff_rules.yaml`: スタッフ除外ルール

**注意**: これらのファイルは機密情報を含むため、Gitで管理されていません。

## トラブルシューティング

### 「0/0分院」と表示される場合

1. GCS設定ファイルを確認:
   ```bash
   gsutil ls gs://dent-slot-checker-houmo-config/config/
   ```

2. ファイルがない場合は再アップロード:
   ```bash
   ./scripts/setup_gcs.sh
   ```

3. Cloud Runサービスを再起動

### ログ確認

ローカル:
```bash
tail -f logs/slot_checker_*.log
```

GCP:
```bash
gcloud logging read "resource.type=cloud_run_revision" \
  --format="table(timestamp,textPayload)" \
  --limit=100
```

## アーキテクチャ

- **フロントエンド**: Flask + Jinja2テンプレート
- **スクレイピング**: Playwright (Chromium)
- **タスク管理**: バックグラウンドスレッド + ファイルベース永続化
- **設定管理**: YAML + Google Cloud Storage

## ディレクトリ構造

```
dent-slot-checker/
├── config/              # 設定ファイル（.gitignore）
├── logs/                # ログファイル
├── output/              # チェック結果
│   └── tasks/           # タスク状態
├── scripts/             # デプロイスクリプト
├── src/                 # スクレイピングロジック
├── web/                 # Webアプリケーション
│   ├── routes/          # APIエンドポイント
│   ├── templates/       # HTMLテンプレート
│   └── app.py           # Flask アプリ
├── Dockerfile           # コンテナイメージ
└── requirements.txt     # Python依存パッケージ
```

## ライセンス

Private
