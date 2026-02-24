"""clinics.yamlからSecret Managerへの一回限りの移行スクリプト

使い方:
  pip install google-cloud-secret-manager pyyaml
  gcloud auth application-default login
  python scripts/migrate_secrets.py --project-id YOUR_PROJECT_ID

  # まずdry-runで確認:
  python scripts/migrate_secrets.py --project-id YOUR_PROJECT_ID --dry-run
"""

import argparse
import json
import yaml


def main():
    parser = argparse.ArgumentParser(
        description='clinics.yamlの認証情報をSecret Managerに移行'
    )
    parser.add_argument('--project-id', required=True, help='GCPプロジェクトID')
    parser.add_argument('--secret-name', default='clinic-credentials', help='Secret名')
    parser.add_argument('--config-path', default='config/clinics.yaml', help='clinics.yamlのパス')
    parser.add_argument('--dry-run', action='store_true', help='実際には実行せず内容を表示')
    args = parser.parse_args()

    # clinics.yaml読み込み
    with open(args.config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 認証情報を抽出
    credentials = {"clinics": [], "stransa_clinics": []}
    for clinic in config.get('clinics', []):
        credentials["clinics"].append({
            "name": clinic.get("name", ""),
            "id": clinic.get("id", ""),
            "password": clinic.get("password", "")
        })
    for clinic in config.get('stransa_clinics', []):
        credentials["stransa_clinics"].append({
            "name": clinic.get("name", ""),
            "id": clinic.get("id", ""),
            "password": clinic.get("password", "")
        })

    if args.dry_run:
        print("=== DRY RUN ===")
        print(f"Secret名: {args.secret_name}")
        print(f"プロジェクト: {args.project_id}")
        print(f"分院数: {len(credentials['clinics'])} (dent-sys), "
              f"{len(credentials['stransa_clinics'])} (stransa)")
        print()
        for c in credentials['clinics']:
            print(f"  - {c['name']} (id: {c['id']})")
        for c in credentials['stransa_clinics']:
            print(f"  - {c['name']} (id: {c['id']}) [stransa]")
        return

    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{args.project_id}"

    # Secret作成
    try:
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": args.secret_name,
                "secret": {"replication": {"automatic": {}}},
            }
        )
        print(f"Secret作成完了: {args.secret_name}")
    except Exception as e:
        if "ALREADY_EXISTS" in str(e):
            print(f"Secret {args.secret_name} は既に存在します。新バージョンを追加します。")
        else:
            raise

    # バージョン追加
    secret_path = f"{parent}/secrets/{args.secret_name}"
    payload = json.dumps(credentials, ensure_ascii=False).encode("UTF-8")

    client.add_secret_version(
        request={
            "parent": secret_path,
            "payload": {"data": payload},
        }
    )
    print(f"認証情報を登録しました（{len(credentials['clinics'])}分院）")


if __name__ == '__main__':
    main()
