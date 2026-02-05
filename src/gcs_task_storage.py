"""Google Cloud Storage タスク永続化

Cloud Runでタスク状態を永続化するためのGCS連携モジュール。
"""

import os
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

GCS_BUCKET = os.environ.get('GCS_BUCKET')
GCS_TASKS_PREFIX = 'tasks/'


def is_gcs_enabled() -> bool:
    """GCSが有効かどうかを確認"""
    return bool(GCS_BUCKET)


def _get_client():
    """GCSクライアントを取得"""
    try:
        from google.cloud import storage
        return storage.Client()
    except ImportError:
        logger.error("google-cloud-storage がインストールされていません")
        return None
    except Exception as e:
        logger.error(f"GCSクライアントの初期化に失敗: {e}")
        return None


def save_task_to_gcs(task_id: str, task_data: Dict[str, Any]) -> bool:
    """タスクをGCSに保存

    Args:
        task_id: タスクID
        task_data: タスクデータ（辞書）

    Returns:
        成功した場合True
    """
    if not is_gcs_enabled():
        return False

    client = _get_client()
    if not client:
        return False

    try:
        bucket = client.bucket(GCS_BUCKET)
        blob_name = f"{GCS_TASKS_PREFIX}task_{task_id}.json"
        blob = bucket.blob(blob_name)

        # JSON文字列として保存
        json_str = json.dumps(task_data, ensure_ascii=False, indent=2)
        blob.upload_from_string(json_str, content_type='application/json')

        logger.debug(f"タスクをGCSに保存: {blob_name}")
        return True

    except Exception as e:
        logger.error(f"GCSタスク保存エラー: {e}")
        return False


def load_task_from_gcs(task_id: str) -> Optional[Dict[str, Any]]:
    """GCSからタスクを読み込み

    Args:
        task_id: タスクID

    Returns:
        タスクデータ（辞書）、存在しない場合None
    """
    if not is_gcs_enabled():
        return None

    client = _get_client()
    if not client:
        return None

    try:
        bucket = client.bucket(GCS_BUCKET)
        blob_name = f"{GCS_TASKS_PREFIX}task_{task_id}.json"
        blob = bucket.blob(blob_name)

        if not blob.exists():
            logger.debug(f"GCSにタスクなし: {blob_name}")
            return None

        # JSON文字列をダウンロード
        json_str = blob.download_as_text()
        task_data = json.loads(json_str)

        logger.debug(f"GCSからタスク読み込み: {blob_name}")
        return task_data

    except Exception as e:
        logger.error(f"GCSタスク読み込みエラー: {e}")
        return None


def delete_task_from_gcs(task_id: str) -> bool:
    """GCSからタスクを削除

    Args:
        task_id: タスクID

    Returns:
        成功した場合True
    """
    if not is_gcs_enabled():
        return False

    client = _get_client()
    if not client:
        return False

    try:
        bucket = client.bucket(GCS_BUCKET)
        blob_name = f"{GCS_TASKS_PREFIX}task_{task_id}.json"
        blob = bucket.blob(blob_name)

        if blob.exists():
            blob.delete()
            logger.info(f"GCSからタスク削除: {blob_name}")

        return True

    except Exception as e:
        logger.error(f"GCSタスク削除エラー: {e}")
        return False
