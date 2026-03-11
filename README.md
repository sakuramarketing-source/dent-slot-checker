# dent-slot-checker

歯科予約システムの空き枠を自動チェックし、Web管理画面で可視化するツール。

## 概要

さくら会グループの各分院が利用する予約システム（dent-sys.net / Stransa / GMO Reserve / Plum）にログインし、翌日の予約状況をスクレイピング。30分以上の連続空き枠を検出し、分院ごとの空き状況をダッシュボードで確認できる。

## 対応システム

| システム | スロット間隔 | 30分空き枠の判定 |
|---------|-----------|----------------|
| dent-sys.net | 5分 or 10分（自動検出） | 6連続 or 3連続スロット |
| Stransa (Apotool & Box) | 15分 | 2連続スロット |
| GMO Reserve (reserve.ne.jp) | 15分 | div_reserveオーバーレイなし+非グレー |
| Plum (plum-link.com) | 15分 | 色付きブロック未カバー時間帯 |

## 機能

- **ダッシュボード**: 最新チェック結果の一覧表示、タイムライン可視化、手動チェック実行
- **スタッフ管理**: スタッフの職種分類（Dr/DH）、有効/無効切替、メモ・タグ
- **分院管理**: 登録分院の一覧・有効/無効切替
- **ルール設定**: 最小空き枠数、除外パターン等の設定
- **結果一覧**: 過去のチェック結果を日付別に閲覧

## データフロー

```
予約システム (dent-sys.net / Stransa / GMO Reserve / Plum)
  ↓ Playwright スクレイピング
空きスロット収集 (5分/10分/15分 自動検出)
  ↓ slot_analyzer.py
30分空き枠の検出 & 集計
  ↓ output_writer.py
JSON/CSV 出力 → Web画面で表示
```

## 認証・セキュリティ

### IAP（Identity-Aware Proxy）認証

本番環境はGCPのIAPで保護されており、許可されたGoogleアカウントのみアクセス可能。

- **本番URL**: `https://checker.sakurashika-g.jp`
- **認証方式**: Googleログイン（IAP経由）
- **許可ユーザー**: sakura.marketing@s-sakurakai.jp, houmon@s-sakurakai.jp

### 認証情報管理（Secret Manager）

分院のログインID・パスワードはGCP Secret Managerで管理。

- **シークレット名**: `clinic-credentials`（プロジェクト: `seo-analytics-app-485802`）
- **ローカル開発**: `config/clinics.yaml`にid/passwordを手動追加（gitignore対象外だが認証情報は含めないこと）
- **本番環境**: Cloud RunがSecret Managerから自動取得（`K_SERVICE`環境変数で判定）

## インフラ構成

```
ユーザー
  ↓ HTTPS (checker.sakurashika-g.jp)
静的IP (34.120.247.156)
  ↓ フォワーディングルール
HTTPS Proxy (dent-checker-https-proxy)
  ↓ SSL証明書 (Google管理)
URL Map (dent-checker-urlmap)
  ↓
バックエンドサービス (dent-checker-backend) ← IAP有効
  ↓ サーバーレスNEG
Cloud Run (dent-slot-checker)
```

- **GCPプロジェクト**: `seo-analytics-app-485802`
- **リージョン**: asia-northeast1
- **DNS**: checker.sakurashika-g.jp → A 34.120.247.156（Xサーバー）

## セットアップ

### ローカル起動

```bash
# 依存パッケージをインストール
pip install -r requirements.txt

# Playwrightブラウザをインストール
playwright install chromium

# clinics.yaml にローカル用の認証情報を追加（id/password）
# ※本番ではSecret Managerから取得されるため不要

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
  --memory 2Gi \
  --timeout 900
```

※ IAP有効化後は `--no-allow-unauthenticated` に変更し、IAP経由のみアクセス可能にする。

**デプロイ前**: GCSから最新設定を同期してからデプロイすること：
```bash
gcloud storage cp gs://dent-checker-config/config/staff_rules.yaml config/staff_rules.yaml
```

## 設定ファイル

### config/clinics.yaml

分院のURL・有効/無効の設定。認証情報（id/password）はSecret Managerで管理。

```yaml
clinics:                    # dent-sys.net 分院
  - name: "分院名"
    url: "https://www.dent-sys.net/..."
    enabled: true

stransa_clinics:            # Stransa 分院
  - name: "医院名"
    url: "https://user.stransa.co.jp/login"
    enabled: true

plum_clinics:               # Plum 分院
  - name: "医院名"
    url: "https://xxx.plum-link.com/#/books"
    enabled: true
    device_name: "端末名"

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
    scraper.py     dent-sys.net スクレイパー（並列実行対応）
    scraper_stransa.py  Stransa スクレイパー
    scraper_gmo.py      GMO Reserve スクレイパー
    scraper_plum.py     Plum スクレイパー（name_mapping対応）
    slot_analyzer.py    空きスロット解析
    config_loader.py    設定読み込み（Secret Manager統合）
    secret_manager.py   GCP Secret Manager連携
    output_writer.py    結果出力
  web/
    app.py         Flask アプリケーション
    routes/        APIルート (main, results, staff, clinics, rules)
    templates/     HTMLテンプレート
    static/        CSS/JS
  run_web.py       Web管理画面起動スクリプト
  Dockerfile       コンテナビルド設定
```

## 運用コスト（GCP）

### チェック1回あたり
- Cloud Run従量課金（2GB RAM / 2 CPU, 約4分/回）: **約$0.02〜0.03**（約3〜4円）

### 月額固定費
| 項目 | 月額 | 備考 |
|------|------|------|
| Cloud Run (min-instances=0) | ~$0-5 | 従量課金のみ（コールドスタート5-10秒） |
| 静的IP・ロードバランサ | ~$3-5 | analytics.sakurashika-g.jp と共用 |
| Secret Manager | ~$0.10 | clinic-credentials（11分院の認証情報） |
| GCS | ~$0.50 | staff_rules.yaml + 結果ファイル |
| 外部API | $0 | DataForSEO/Claude API は未実装 |
| **合計** | **~$4-11** | |

### コスト削減オプション
- `min-instances=0` に変更 → 月$80削減（ただしコールドスタート5-10秒発生）

## 更新履歴

- **2026-03-11** 継続的デプロイ設定: Cloud Build + GitHubトリガーを追加。masterブランチへのpush時に自動でCloud Runにデプロイ（cloudbuild.yaml追加）
- **2026-03-09** Plum Cloud Run対応: API fallback追加（DOM検出が>80%空き枠の場合、REST API直接呼び出しに切替）。SPAのauthorizationヘッダーをキャプチャしてpage.evaluate(fetch)で認証付きAPI取得
- **2026-03-09** ヒロデンタル ユニットチェック修正: name_contains→explicitグループマッピングに変更。Dr/DHそれぞれ職種グループ単位でユニット共有（個別1:1マッチ→グループ内どれか空いていればOK）。main.py閾値判定に(N)サフィックス除去追加
- **2026-03-09** Plumスクレイパー追加: イーアス春日井歯科（plum-link.com）対応。React/MUI SPA対応（ログイン・翌日遷移・空き枠検出）、15分刻みスロット検出、name_mappingによるカレンダー表示名→スタッフ管理名変換
- **2026-03-09** ヘルプパネル追加: 全ページ共通のスライドアウト式マニュアルを実装（右側パネル、操作しながら閲覧可能）。アクセスアカウント追加（sakurakai.daini@gmail.com）。コスト削減のためmin-instances=0に変更
- **2026-03-05** GCS設定マージ: デプロイ時にダッシュボードのユーザー設定（web_booking, memos等）がGit版で上書きされる問題を修正。起動時にDocker imageとGCSをマージし、ユーザー設定を保持
- **2026-03-05** ユニットチェック機能: 長久手・ヒロデンタルでスタッフ空き枠とユニット空き枠のAND条件フィルタを追加（スタッフに空きがあってもユニットが埋まっていれば除外）
- **2026-03-05** Stransaオーバーレイカバレッジ精度修正: Stage2 JSにcellHeight/blockHeight実測値を追加（cellH=20px固定→実測30px等）、オーバーレイ行数の過大推定を解消（名駅さくら大橋5→8スロット、阪上1→2スロット）
- **2026-03-05** 4件バグ修正: GMO時間ずれ(Math.round→Math.floor)、Stransa未使用列フィルタにblock_coverage追加(名駅さくら阪上・大橋検出)、白閾値248+9点多数決判定(きらり大森Dr秋葉偽空き枠解消)、きた矯正歯科を除外
- **2026-03-04** GMO Reserve空き枠精度根本修正: elementsFromPoint+div_reserveテキスト検査方式に変更（ピクセル色だけでは空き/予約済を区別不可）、datepicker swipe_moveで翌日遷移（view_dateパラメータ不可→onSelectコールバック経由）、全24スタッフをstaff_rules.yamlに登録
- **2026-03-03** 予約ブロックオーバーレイ検出: position:absoluteの予約ブロック（アシスト等）が後続セルをカバーする範囲を追跡し偽空き枠を排除、detect_slot_interval最小有効ギャップ方式に改善（ヒロ10分間隔対応）
- **2026-03-03** GMO Reserve対応: さくら医院歯科（reserve.ne.jp）スクレイパー追加、ログイン→歯科タブ切替→黄色背景空き枠検出
- **2026-03-03** きらり大森DH祢津精度修正: 予約ブロックのオーバーレイ検出追加（position:absolute+height:NNNpxパース）、ヒロデンタル10分間隔対応（detect_slot_interval最小有効ギャップ方式に変更）
- **2026-03-03** 町屋精度修正: Stransa slot_interval自動検出バイパス（疎スロットで60→30スナップ→consecutive=1バグ修正）、cancelled_koma再予約済み判定追加（白背景/ストライプのみ空き枠、色付き背景はスキップ）
- **2026-03-02** キャンセル枠改善: cancelled_koma吹き出しのみケースも空き枠として採用（斜線パターン必須を撤廃）、カテゴリ分類にサフィックス除去追加（DH小森(1)→DH小森でhygienistマッチ）
- **2026-03-02** 精度修正: Stransaテーブル選択（最多スタッフ列）、is_staff_column()サフィックス(1)/(2)対応、未使用列フィルタ（予約ゼロ列除外）、web_bookingサフィックス除去、CJK互換漢字(﨑)対応、Cloud Run CPU throttling解消(--no-cpu-throttling)
- **2026-03-02** 並列実行を逐次に戻す: ページ描画タイムアウト→スタッフタブ未検出→web_bookingフィルタで全滅する精度問題を修正
- **2026-02-28** 速度改善: Stransa+dent-sys並列実行を復活（asyncio.gather）、メモリ4GBに増量でFrame detached解消
- **2026-02-28** 結果マージ修正: システム別チェック時に他システムの結果が消える問題を修正（GCS同期追加+マージ条件改善）、医院表示順をCLINIC_ORDER準拠に
- **2026-02-28** ブラウザプール修正: イベントループ初期化のレースコンディション解消（time.sleep→Event.wait）、初期化失敗時の即時エラー返却
- **2026-02-28** Stransa精度改善: cancelled_komaにもピクセル色検証追加（ピンク/赤系のみキャンセル枠として採用）、DIAG20セル拡張
- **2026-02-28** Stransa精度根本修正: スクリーンショット+Canvas APIでピクセル色判定（getComputedStyleが透明を返す問題を回避）、children>0フォールスルーバグ修正
- **2026-02-28** 安定性修正: 並列実行を逐次に戻し（リソース競合解消）、Stransa判定をwaku/cancelled_komaクラス+childCountベースに改善、CSSロード待機追加
- **2026-02-28** 速度最適化: Stransa+dent-sys並列実行（asyncio.gather）、iframeポーリング高速化（0.5秒間隔）、sleep削減
- **2026-02-28** Stransa精度修正: CSSクラス(cancelled_koma)でキャンセル枠検出、子要素背景色チェック追加
- **2026-02-28** Stransa空き枠検出: getComputedStyleベース判定に変更（インラインstyle→実際の描画色）
- **2026-02-28** Stransa速度最適化: domcontentloaded+要素ベース待機、sleep短縮
- **2026-02-28** dent-sys速度最適化: domcontentloaded+要素ベース待機、evaluate()一括化、Semaphore(3)
- **2026-02-26** Stransa空き枠修正: スタッフタブ切替を追加（デフォルトのユニット表示→スタッフ表示）、空きセル判定改善
- **2026-02-25** システム別チェック: ダッシュボードに「全て/dent-sys/Stransa」選択ボタン追加
- **2026-02-25** dent-sys+Stransa並列スクレイピング: asyncio.gatherで同時実行、CPU 2コア化、min-instances=1
- **2026-02-25** PYTHONUNBUFFERED=1追加、サブプロセス5分タイムアウト、ログ末尾リアルタイム表示、GCSからstaff_rules同期コミット
- **2026-02-25** GCS起動時同期: アプリ起動時にGCSからstaff_rules.yamlを即座にダウンロード（デプロイ時の設定消失防止）
- **2026-02-25** 手動チェック安定化: Popen+poll方式に変更（PIPEバッファ問題解消）、サブプロセスGCS同期追加
- **2026-02-25** GCS永続化: staff_rules.yaml・出力ファイルをGCSバケットに保存（Cloud Run再起動時の設定消失対策）
- **2026-02-25** 手動チェック非同期化: バックグラウンド実行+ポーリング方式に変更（タイムアウト解消）
- **2026-02-25** APIエラーハンドラ追加: 404/500でJSON返却（HTMLによるパースエラー解消）
- **2026-02-25** Stransa スタッフ同期を設定ページ(/user/staffs)から取得に変更（カレンダーヘッダー→実スタッフ名）
- **2026-02-25** スタッフ同期完了: 全18分院のスタッフデータをローカル同期で永続化
- **2026-02-25** Stransa ログイン安定化: オフィス選択ページ対応強化、SPA描画待ち追加
- **2026-02-24** Stransa分院にsystemフィールド設定（スクレイピング・同期動作修正）
- **2026-02-24** 手動チェック3点修正: Semaphore(6/5)高速化、exit(0)修正、Stransa同期追加
- **2026-02-24** Stransa並列スクレイピング: asyncio.gather + Semaphore(5)、Cloud Runタイムアウト900s
- **2026-02-24** 医院並び順を沿革順に更新（全18院）、金沢さくら医院リネーム
- **2026-02-24** IAP認証対応: Googleログインによるアクセス制御、ナビバーにユーザー表示
- **2026-02-24** Secret Manager統合: 分院認証情報をGCP Secret Managerで安全に管理
- **2026-02-24** 並列スクレイピング: asyncio.gather + Semaphore(3)で手動チェックを高速化
- **2026-02-24** clinics.yamlから認証情報削除（id/password → Secret Managerに移行）
- **2026-02-23** 医院別・職種別の空き枠判定閾値を設定可能に（Dr/DH別、デフォルト30分）
- **2026-02-23** スタッフ管理画面: 開院順ソート（全17院）+ アコーディオンUI
- **2026-02-23** Dr/DH/全てフィルタ切替トグルを追加（ダッシュボード・結果一覧）
- **2026-02-23** スクレイパー時刻計算を根本修正: 行-時刻マッピング構築で昼休みギャップに対応、スロット間隔自動検出（5分/10分）
- **2025-02-19** タイムライン表示をハイブリッド型に改善（上部タイムライン + 詳細タグ表示）
- **2025-02-19** タイムライン可視化を追加（ダッシュボード・結果一覧）
- **2025-02-19** 初期コミット: Flask Web管理画面付きの空き枠チェッカー
