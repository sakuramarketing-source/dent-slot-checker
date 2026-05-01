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

## 空き枠判定ロジック

空き枠の判定にAI（Gemini、Claude、ChatGPT等）は一切使用していない。全てルールベースのプログラム処理（Python + Playwright）で判定する。

### 各システムの判定方法

#### dent-sys（11分院）
予約表iframe内の「新」リンク（`<a class="new">`）の有無で判定。
- 「新」リンクあり → **空き枠**（新規予約可能）
- 「新」リンクなし → 予約済みまたはブロック

#### Stransa（6分院）
スクリーンショット + Canvas APIでピクセル色を数値判定（getComputedStyleはheadless Chromiumで透明を返すため不使用）。

6ステップで判定：
1. `cancelled_koma`クラス + ピンク/赤ピクセル（R>200, R-G>30, R-B>30）→ **空き枠**
2. `waku`リンクあり → 予約済み
3. テキストあり → 予約済み
4. 子要素なし + 白ピクセル（R>240, G>240, B>240）→ **空き枠**
5. 子要素なし + 非白 → グレー（診療時間外）
6. 子要素あり → グレー（ブロック時間帯）

#### GMO Reserve（1分院：さくら医院歯科）
`elementsFromPoint()`で予約オーバーレイ（`div_reserve`）の有無を検査。
- オーバーレイなし + テキストなし + 非グレー → **空き枠**
- オーバーレイあり or テキストあり → 予約済み
- グレー（R≈G≈B, 150<R<245）→ 非稼働時間

#### Plum（1分院：イーアス春日井歯科）
時間軸×スタッフ列の2次元グリッドで、色付きDIV（予約ブロック）の有無を判定。
- 予約ブロックがない時間帯 → **空き枠**
- 営業時間外・昼休みは自動除外

### 空きの数え方

1. 各スクレイパーが5分/10分/15分刻みの空きスロットを検出
2. **連続スロットを30分単位（1ブロック）に換算**（例：5分刻みなら6連続 = 1ブロック）
3. `staff_rules.yaml`の`web_booking`リストに含まれる**WEB予約対象スタッフのみ**を集計
4. 合計が`minimum_blocks_required`（デフォルト4ブロック = 120分）以上で「**空きあり**」と判定

## 機能

- **ダッシュボード**: 最新チェック結果の一覧表示、タイムライン可視化、手動チェック実行
- **Chatwork自動通知**: 毎日17:00に自動チェック → 空き��り分院のDr/DH別ブロ��ク数をChatworkに送信
- **月次レポート**: 月ごとの分院別空き枠頻度・ブロック数・Dr/DH内訳を集計表示
- **スタッフ管理**: スタッフの職種分類（Dr/DH）���有効/無効切替、メモ���タグ
- **分院管理**: 登録分院の��覧・有効/無効切替
- **ルール設定**: 最小空き枠数、除外パターン���の設定
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

### Cloud Run設定
- **CPU**: 2 vCPU / **メモリ**: 4 GiB
- **課金モデル**: インスタンスベース（no-cpu-throttling）※CPU常に全力
- **min-instances**: 0（アイドル時インスタンス停止 → 課金ゼロ）
- **max-instances**: 1（スケールアウト防止）
- **並列数**: Semaphore(3)（dent-sys/Stransa各3並列）

### 月額費用
| 項目 | 月額 | 備考 |
|------|------|------|
| Cloud Run | ~¥300 | 1日1回チェック(5分) + アイドル15分 ≈ 20分/日 |
| ロードバランサ | ~¥8,100 | 転送ルール3つ（dent-checker HTTP/HTTPS + seo-analytics HTTPS） |
| Artifact Registry | ~¥700 | Dockerイメージ保存（古いイメージ定期削除推奨） |
| Secret Manager | ~¥100 | 9シークレット11バージョン |
| GCS | ~¥0 | 約50MB（無料枠内） |
| **合計** | **~¥9,200** | ロードバランサが88%を占める |

### コスト注意事項

#### インシデント1（2026-03-02〜03-13）: max-instances=20 + no-cpu-throttling
- **問題**: 日額約¥7,000〜¥9,000が発生（約11日間、推定被害¥77,000〜¥99,000）
- **原因**: `--no-cpu-throttling`（インスタンスベース課金）でmax-instances=20のまま運用。リクエストがなくてもインスタンス稼働中は課金され、最大20台分が課金対象に
- **対策**: max-instances=5に制限

#### インシデント2（2026-03-13〜03-20）: 古いリビジョン残存
- **問題**: max-instances=5に修正後も日額約¥10,000が継続
- **原因**: 古いリビジョン154個が残存。特にmin-instances=1 + no-cpu-throttling設定の古いリビジョンがインスタンスを維持し続け課金が発生
- **対策**: 古いリビジョンを全削除、課金モデルをcpu-throttling（リクエストベース）+ min-instances=0に変更、cloudbuild.yamlも同様に修正

#### 設定変更時の注意
- `--no-cpu-throttling` / `--cpu-throttling` / `min-instances` / `max-instances` はCloud Runの課金モデルに直結するため、変更時は必ずコスト影響を確認すること
- **リビジョン管理**: 設定変更後は古いリビジョンを削除すること。古いリビジョンにmin-instances設定が残っていると課金が継続する

## 更新履歴

- **2026-05-01** デプロイ後にスタッフ同期データが消える問題を修正: 起動時GCSマージの `USER_KEYS` に `all_staff`/`doctors`/`hygienists`/`orthodontists` を追加。スタッフ同期やUI設定でGCSに保存したデータがデプロイのたびにDockerイメージ版で上書きされていた
- **2026-04-30** paylight Vue仮想スクローラー対応: JS の `scrollTop=0` / `dispatchEvent('scroll')` は Vue に無視されるため Playwright ネイティブ `mouse.wheel(0, -1000)` × 10 に変更。カレンダーが下スクロール状態のままだと時間ラベルがビューポート外に追い出され `timeLabels=0` となり空き枠ゼロになっていた問題を解消
- **2026-04-27** paylight X スタッフ名ゴミデータ修正: `p.c-calendar__date__label` は予約タイプ列・部屋列にも使われるクラスだったため親コンテナの `.staff` クラスでフィルタ。スタッフ管理ページへの pay_light_clinics 追加・スタッフ同期後のゴミエントリ削除処理も追加
- **2026-04-27** paylight X（さくら歯科）スタッフ同期修正: ログイン後に「日」ビュー切替 + `p.c-calendar__date__label` 描画待ち（15秒）を追加。Vue.js SPA の描画前にセレクタを叩いて空/誤ったスタッフ名になっていた問題を解消
- **2026-04-27** スタッフ同期並列化: dent-sys・Stransa の同期を semaphore=3 で並列実行。逐次処理（最悪36分）から6〜8分に短縮。per-clinic 進捗メッセージ・per-system 5分タイムアウト・バックグラウンドスレッドの StreamHandler ロギングを追加
- **2026-04-27** paylight X ダッシュボード対応: `web/routes/results.py` に pay-light チェックルートを追加。さくら歯科が19分院目として結果に表示されるように。「pay-lightのみ」チェックボタン・フィルターボタンも追加
- **2026-04-20** ルール設定GCS永続化: ルール設定保存時にclinics.yamlをGCSにアップロード。起動時にsettingsセクションのみGCSから復元（clinic listはイメージが正）。デプロイ後もルール設定がリセットされない
- **2026-04-20** スタッフ同期タイムアウト修正: 同期処理をバックグラウンドスレッド化して524エラーを解消。フロントエンドが3秒ごとにポーリングし経過秒数を表示。/api/staff/sync-statusエンドポイント追加
- **2026-03-23** 月次レポート機能追加: `/monthly-report`で月ごとの分院別空き枠頻度・合計ブロック数・Dr/DH内訳���集計表示。開院順/頻度順/ブロック数順でソート可能
- **2026-03-23** Chatwork通知Dr/DH内訳追加: 各分院の空きブロック数にDr/DH別の内訳を表示（例: `○ さくら歯科: 4ブロック（Dr:2 / DH:2）`）
- **2026-03-23** Chatwork自動通知: 毎日17:00に自動チェック→空きあり分院のみChatwork送信（ルーム427934070）。Cloud Scheduler + notify_chatworkパラメータで手動/自動を区別。手��チェックは通知なし
- **2026-03-23** コスト最適化: no-cpu-throttling + min-instances=0 + max-instances=1に変更。Semaphore(3)で3並列化。チェック5分以内・コスト約¥10/日を実現。古いリビジョンは都度削除
- **2026-03-20** コスト修正: cpu-throttling（リクエストベース課金）+ min-instances=0に変更。古いリビジョン154個を削除（min-instances=1+no-cpu-throttlingの旧リビジョンが課金を継続していた）。cloudbuild.yamlも同様に修正
- **2026-03-13** Cloud Run設定変更: no-cpu-throttling + min-instances=1 + メモリ4GiB + max-instances=5。インスタンスベース課金（月額約¥23,000）、コールドスタートなし
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
