#!/bin/bash
# GCS初回セットアップスクリプト

set -e

BUCKET_NAME=${GCS_BUCKET:-"dent-slot-checker-houmo-config"}

echo "=== GCS設定ファイルセットアップ ==="
echo "バケット: $BUCKET_NAME"

# バケット存在確認
if ! gsutil ls -b gs://$BUCKET_NAME > /dev/null 2>&1; then
  echo "エラー: バケット gs://$BUCKET_NAME が存在しません"
  exit 1
fi

# 設定ファイルのアップロード
echo "clinics.yamlをアップロード中..."
gsutil cp config/clinics.yaml gs://$BUCKET_NAME/config/clinics.yaml

echo "staff_rules.yamlをアップロード中..."
gsutil cp config/staff_rules.yaml gs://$BUCKET_NAME/config/staff_rules.yaml

# 確認
echo ""
echo "=== アップロード完了 ==="
gsutil ls -lh gs://$BUCKET_NAME/config/

echo ""
echo "✓ GCSセットアップ完了"
