"""Google Cloud Storage ユーティリティ

Cloud Runでのデプロイ時に設定ファイルを永続化するためのGCS連携モジュール。
環境変数 GCS_BUCKET が設定されている場合のみ有効。
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 環境変数からバケット名を取得
GCS_BUCKET = os.environ.get('GCS_BUCKET')
GCS_CONFIG_PREFIX = 'config/'

# 同期対象の設定ファイル
CONFIG_FILES = ['staff_rules.yaml', 'clinics.yaml']


def is_gcs_enabled() -> bool:
    """GCSが有効かどうかを確認"""
    return bool(GCS_BUCKET)


def _get_client():
    """GCSクライアントを取得（遅延インポート）"""
    try:
        from google.cloud import storage
        return storage.Client()
    except ImportError:
        logger.error("google-cloud-storage がインストールされていません")
        return None
    except Exception as e:
        logger.error(f"GCSクライアントの初期化に失敗: {e}")
        return None


def download_config_files(local_config_path: str) -> bool:
    """GCSから設定ファイルをダウンロード

    Args:
        local_config_path: ローカルの設定ディレクトリパス

    Returns:
        成功した場合True
    """
    if not is_gcs_enabled():
        logger.debug("GCS_BUCKET が未設定のため、GCSダウンロードをスキップ")
        return False

    client = _get_client()
    if not client:
        return False

    try:
        bucket = client.bucket(GCS_BUCKET)
        local_path = Path(local_config_path)
        local_path.mkdir(parents=True, exist_ok=True)

        downloaded_count = 0
        for filename in CONFIG_FILES:
            blob_name = f"{GCS_CONFIG_PREFIX}{filename}"
            blob = bucket.blob(blob_name)

            local_file = local_path / filename

            if blob.exists():
                blob.download_to_filename(str(local_file))
                logger.info(f"GCSからダウンロード: {blob_name} -> {local_file}")
                downloaded_count += 1
            else:
                logger.debug(f"GCSにファイルなし: {blob_name}")

        logger.info(f"GCSから {downloaded_count}/{len(CONFIG_FILES)} ファイルをダウンロード")
        return downloaded_count > 0

    except Exception as e:
        logger.error(f"GCSダウンロードエラー: {e}")
        return False


def upload_config_file(local_path: str, filename: str) -> bool:
    """設定ファイルをGCSにアップロード

    Args:
        local_path: ローカルファイルのフルパス
        filename: ファイル名（例: staff_rules.yaml）

    Returns:
        成功した場合True
    """
    if not is_gcs_enabled():
        logger.debug("GCS_BUCKET が未設定のため、GCSアップロードをスキップ")
        return False

    client = _get_client()
    if not client:
        return False

    try:
        bucket = client.bucket(GCS_BUCKET)
        blob_name = f"{GCS_CONFIG_PREFIX}{filename}"
        blob = bucket.blob(blob_name)

        blob.upload_from_filename(local_path)
        logger.info(f"GCSにアップロード: {local_path} -> gs://{GCS_BUCKET}/{blob_name}")
        return True

    except Exception as e:
        logger.error(f"GCSアップロードエラー: {e}")
        return False


def upload_all_config_files(local_config_path: str) -> int:
    """全ての設定ファイルをGCSにアップロード

    Args:
        local_config_path: ローカルの設定ディレクトリパス

    Returns:
        アップロードに成功したファイル数
    """
    if not is_gcs_enabled():
        return 0

    local_path = Path(local_config_path)
    uploaded_count = 0

    for filename in CONFIG_FILES:
        local_file = local_path / filename
        if local_file.exists():
            if upload_config_file(str(local_file), filename):
                uploaded_count += 1

    return uploaded_count
