"""Google Cloud Secret Manager連携モジュール

本番(Cloud Run): Secret Managerから認証情報を読み取り
ローカル開発: clinics.yamlにフォールバック
"""

import json
import logging
import os
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# モジュールレベルキャッシュ（worker=1なので安全）
_cached_credentials: Optional[Dict[str, Any]] = None


def _is_secret_manager_available() -> bool:
    """Secret Managerを使用するか判定"""
    if os.environ.get('USE_LOCAL_CREDENTIALS', '').lower() in ('1', 'true', 'yes'):
        return False
    # Cloud RunではK_SERVICEが自動設定される
    if os.environ.get('K_SERVICE'):
        return True
    # ローカルテスト用の明示的オプトイン
    if os.environ.get('USE_SECRET_MANAGER', '').lower() in ('1', 'true', 'yes'):
        return True
    return False


def _get_gcp_project_id() -> str:
    """GCPプロジェクトIDを取得"""
    project_id = os.environ.get('GCP_PROJECT_ID')
    if project_id:
        return project_id
    # Cloud Runではメタデータサーバーから取得可能
    try:
        import requests
        resp = requests.get(
            'http://metadata.google.internal/computeMetadata/v1/project/project-id',
            headers={'Metadata-Flavor': 'Google'},
            timeout=2
        )
        return resp.text
    except Exception:
        raise RuntimeError("GCPプロジェクトIDが取得できません。GCP_PROJECT_ID環境変数を設定してください。")


def _load_from_secret_manager() -> Dict[str, Any]:
    """Secret Managerから認証情報を読み取り"""
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    project_id = _get_gcp_project_id()
    secret_name = os.environ.get('CREDENTIALS_SECRET_NAME', 'clinic-credentials')

    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    payload = response.payload.data.decode("UTF-8")

    return json.loads(payload)


def _save_to_secret_manager(credentials: Dict[str, Any]) -> None:
    """Secret Managerに認証情報を新バージョンとして保存"""
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    project_id = _get_gcp_project_id()
    secret_name = os.environ.get('CREDENTIALS_SECRET_NAME', 'clinic-credentials')

    parent = f"projects/{project_id}/secrets/{secret_name}"
    payload = json.dumps(credentials, ensure_ascii=False).encode("UTF-8")

    client.add_secret_version(
        request={"parent": parent, "payload": {"data": payload}}
    )


def _load_from_yaml(config_dir: str) -> Dict[str, Any]:
    """clinics.yamlから認証情報を抽出（ローカル開発用フォールバック）"""
    import yaml
    clinics_path = os.path.join(config_dir, 'clinics.yaml')
    with open(clinics_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    result = {"clinics": [], "stransa_clinics": []}
    for clinic in config.get('clinics', []):
        result["clinics"].append({
            "name": clinic.get("name", ""),
            "id": clinic.get("id", ""),
            "password": clinic.get("password", "")
        })
    for clinic in config.get('stransa_clinics', []):
        result["stransa_clinics"].append({
            "name": clinic.get("name", ""),
            "id": clinic.get("id", ""),
            "password": clinic.get("password", "")
        })
    return result


def _save_to_yaml(credentials: Dict[str, Any], config_dir: str) -> None:
    """認証情報をclinics.yamlにマージ保存（ローカル開発用）"""
    import yaml
    clinics_path = os.path.join(config_dir, 'clinics.yaml')
    with open(clinics_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    cred_map = {c['name']: c for c in credentials.get('clinics', [])}
    for clinic in config.get('clinics', []):
        cred = cred_map.get(clinic.get('name'))
        if cred:
            clinic['id'] = cred['id']
            clinic['password'] = cred['password']

    stransa_map = {c['name']: c for c in credentials.get('stransa_clinics', [])}
    for clinic in config.get('stransa_clinics', []):
        cred = stransa_map.get(clinic.get('name'))
        if cred:
            clinic['id'] = cred['id']
            clinic['password'] = cred['password']

    with open(clinics_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


def get_credentials(config_dir: str = None) -> Dict[str, Any]:
    """認証情報を取得（Secret Manager or YAMLフォールバック）

    Returns:
        {'clinics': [{'name', 'id', 'password'}, ...], 'stransa_clinics': [...]}
    """
    global _cached_credentials

    if _cached_credentials is not None:
        return _cached_credentials

    if config_dir is None:
        from pathlib import Path
        config_dir = str(Path(__file__).parent.parent / 'config')

    if _is_secret_manager_available():
        try:
            creds = _load_from_secret_manager()
            _cached_credentials = creds
            logger.info("Secret Managerから認証情報を読み込みました")
            return creds
        except Exception as e:
            logger.error(f"Secret Manager読み取り失敗: {e}")
            logger.warning("clinics.yamlにフォールバック")
            return _load_from_yaml(config_dir)
    else:
        creds = _load_from_yaml(config_dir)
        _cached_credentials = creds
        logger.info("clinics.yamlから認証情報を読み込みました（ローカルモード）")
        return creds


def save_credentials(credentials: Dict[str, Any], config_dir: str = None) -> None:
    """認証情報を保存（Secret Manager or YAMLフォールバック）"""
    invalidate_cache()

    if config_dir is None:
        from pathlib import Path
        config_dir = str(Path(__file__).parent.parent / 'config')

    if _is_secret_manager_available():
        _save_to_secret_manager(credentials)
        logger.info("Secret Managerに認証情報を保存しました")
    else:
        _save_to_yaml(credentials, config_dir)
        logger.info("clinics.yamlに認証情報を保存しました（ローカルモード）")


def invalidate_cache() -> None:
    """キャッシュを無効化（次回アクセス時に再読み込み）"""
    global _cached_credentials
    _cached_credentials = None
