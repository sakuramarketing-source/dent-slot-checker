"""GCS永続化ヘルパー

Cloud Runのエフェメラルファイルシステム対策。
設定ファイル(staff_rules.yaml)と出力ファイル(output/)をGCSに永続化。
ローカル開発時はGCSを使わずファイルシステムのみ。
"""

import os
import logging

logger = logging.getLogger(__name__)

_BUCKET_NAME = os.environ.get('CONFIG_BUCKET', 'dent-checker-config')


def _is_cloud_run() -> bool:
    return bool(os.environ.get('K_SERVICE'))


def _get_client():
    from google.cloud import storage
    return storage.Client()


def upload_to_gcs(local_path: str, gcs_path: str = None) -> bool:
    """ローカルファイルをGCSにアップロード"""
    if not _is_cloud_run():
        return False
    if gcs_path is None:
        gcs_path = os.path.basename(local_path)
    try:
        client = _get_client()
        bucket = client.bucket(_BUCKET_NAME)
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(local_path)
        logger.info(f"GCSアップロード完了: {gcs_path}")
        return True
    except Exception as e:
        logger.error(f"GCSアップロード失敗 ({gcs_path}): {e}")
        return False


def download_from_gcs(gcs_path: str, local_path: str) -> bool:
    """GCSからローカルにダウンロード"""
    if not _is_cloud_run():
        return False
    try:
        client = _get_client()
        bucket = client.bucket(_BUCKET_NAME)
        blob = bucket.blob(gcs_path)
        if not blob.exists():
            logger.info(f"GCSにファイルなし: {gcs_path}")
            return False
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        blob.download_to_filename(local_path)
        logger.info(f"GCSダウンロード完了: {gcs_path}")
        return True
    except Exception as e:
        logger.error(f"GCSダウンロード失敗 ({gcs_path}): {e}")
        return False


def list_gcs_files(prefix: str) -> list:
    """GCSのファイル一覧を取得"""
    if not _is_cloud_run():
        return []
    try:
        client = _get_client()
        bucket = client.bucket(_BUCKET_NAME)
        blobs = bucket.list_blobs(prefix=prefix)
        return [blob.name for blob in blobs]
    except Exception as e:
        logger.error(f"GCS一覧取得失敗 ({prefix}): {e}")
        return []


def sync_output_from_gcs(output_dir: str) -> int:
    """GCSのoutput/ファイルをローカルに同期"""
    if not _is_cloud_run():
        return 0
    count = 0
    try:
        client = _get_client()
        bucket = client.bucket(_BUCKET_NAME)
        blobs = bucket.list_blobs(prefix='output/')
        os.makedirs(output_dir, exist_ok=True)
        for blob in blobs:
            filename = os.path.basename(blob.name)
            if not filename:
                continue
            local_path = os.path.join(output_dir, filename)
            if not os.path.exists(local_path):
                blob.download_to_filename(local_path)
                count += 1
        if count > 0:
            logger.info(f"GCSからoutput/{count}ファイルを同期")
    except Exception as e:
        logger.error(f"GCS output同期失敗: {e}")
    return count
