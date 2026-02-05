#!/bin/bash
# デプロイ前の設定確認

set -e

BUCKET_NAME=${GCS_BUCKET:-"dent-slot-checker-houmo-config"}

echo "=== デプロイ前チェック ==="

# GCS設定ファイル確認
echo "1. GCS設定ファイル確認..."
if gsutil ls gs://$BUCKET_NAME/config/clinics.yaml > /dev/null 2>&1; then
  echo "  ✓ clinics.yaml 存在"
else
  echo "  ✗ clinics.yaml が見つかりません"
  echo "  実行: ./scripts/setup_gcs.sh"
  exit 1
fi

if gsutil ls gs://$BUCKET_NAME/config/staff_rules.yaml > /dev/null 2>&1; then
  echo "  ✓ staff_rules.yaml 存在"
else
  echo "  ✗ staff_rules.yaml が見つかりません"
  echo "  実行: ./scripts/setup_gcs.sh"
  exit 1
fi

# ローカル設定ファイル確認
echo "2. ローカル設定ファイル確認..."
if [ -f "config/clinics.yaml" ]; then
  CLINIC_COUNT=$(grep -c "^  - name:" config/clinics.yaml || echo "0")
  echo "  ✓ clinics.yaml ($CLINIC_COUNT 分院設定)"
else
  echo "  ✗ config/clinics.yaml が見つかりません"
  exit 1
fi

echo ""
echo "✓ デプロイ前チェック完了"
