"""pgas.yoyaku.media（医院スマホ予約）スクレイパー

増田歯科が使用する「メディア」システムの空き枠を取得。
- ログイン: グループID + ユーザーID + パスワード（+ 端末登録は初回のみ）
- セッション状態を GCS に保存し、繰り返し端末登録を避ける
- 空き枠判定: TODO（カレンダー構造確認後に実装）
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

LOGIN_URL = "https://pgas.yoyaku.media/"
# GCS に保存するセッション状態のパス
GCS_SESSION_PATH = "config/media_session_state.json"
_LOCAL_SESSION_PATH = os.path.join(tempfile.gettempdir(), "media_session_state.json")


# ──────────────────────────────────────────────
# セッション状態の保存・読み込み（GCS経由）
# ──────────────────────────────────────────────

def _load_session_from_gcs() -> bool:
    """GCS から Playwright セッション状態をローカルにダウンロード"""
    try:
        from src.gcs_helper import download_from_gcs
        return download_from_gcs(GCS_SESSION_PATH, _LOCAL_SESSION_PATH)
    except Exception as e:
        logger.warning(f"[media] セッション状態ロード失敗: {e}")
        return False


def _save_session_to_gcs(state_path: str) -> bool:
    """Playwright セッション状態を GCS にアップロード"""
    try:
        from src.gcs_helper import upload_to_gcs
        return upload_to_gcs(state_path, GCS_SESSION_PATH)
    except Exception as e:
        logger.warning(f"[media] セッション状態保存失敗: {e}")
        return False


# ──────────────────────────────────────────────
# ログイン
# ──────────────────────────────────────────────

async def _close_error_modal(page) -> str:
    """エラーモーダルを閉じてメッセージ内容を返す"""
    try:
        text = await page.evaluate(
            "() => document.querySelector('#modal-modal')?.innerText?.trim() || ''"
        )
        # OK ボタンが表示されていれば閉じる
        ok = page.locator('#m-error-ok')
        if await ok.count() > 0:
            visible = await page.evaluate(
                "() => { const el = document.querySelector('#m-error-ok'); "
                "return el ? window.getComputedStyle(el).display !== 'none' : false; }"
            )
            if visible:
                await ok.click(force=True)
                await page.wait_for_timeout(800)
        return text
    except Exception:
        return ""


async def _try_simple_login(page, user_id: str, password: str) -> bool:
    """#userid + #password でログインを試みる（端末登録なし）"""
    try:
        # JS でフォーム値をセット（hidden 要素対応）
        await page.evaluate(f'''() => {{
            const uid = document.querySelector('#userid');
            const pwd = document.querySelector('#password');
            if (uid) uid.value = {json.dumps(user_id)};
            if (pwd) pwd.value = {json.dumps(password)};
        }}''')
        await page.click('#login', force=True)
        await page.wait_for_timeout(3000)

        # ログイン画面のままなら失敗
        title = await page.title()
        return "ログイン" not in title
    except Exception as e:
        logger.debug(f"[media] simple login error: {e}")
        return False


async def _register_device(page, group_id: str, user_id: str,
                            password: str, device_name: str) -> bool:
    """端末登録を実行する（初回または状態ファイルなし時のみ呼ぶ）"""
    logger.info("[media] 端末登録を実行")
    try:
        await page.evaluate(f'''() => {{
            const ids = ['#m-group', '#m-user', '#m-password', '#m-mobile'];
            const vals = {json.dumps([group_id, user_id, password, device_name])};
            ids.forEach((id, i) => {{
                const el = document.querySelector(id);
                if (el) el.value = vals[i];
            }});
        }}''')
        await page.click('#m-m-definite', force=True)
        await page.wait_for_timeout(3000)

        text = await _close_error_modal(page)
        if "完了" in text:
            logger.info("[media] 端末登録完了")
            return True
        else:
            logger.error(f"[media] 端末登録失敗: {text[:100]}")
            return False
    except Exception as e:
        logger.error(f"[media] 端末登録エラー: {e}")
        return False


async def login_media(page, context, clinic_name: str, url: str,
                      group_id: str, user_id: str, password: str,
                      device_name: str) -> bool:
    """
    メディアシステムにログイン。

    優先順位:
    1. GCS の保存済みセッション状態でログイン（端末登録不要）
    2. セッションなし/期限切れ → 端末登録（1回のみ）→ ログイン → 状態保存
    """
    logger.info(f"[{clinic_name}] ログイン開始: {url}")
    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(2000)

    # まず保存済みセッションでログインを試みる
    if os.path.exists(_LOCAL_SESSION_PATH):
        logger.info(f"[{clinic_name}] 保存済みセッションでログイン試行")
        if await _try_simple_login(page, user_id, password):
            logger.info(f"[{clinic_name}] セッション再利用ログイン成功")
            return True
        # セッション期限切れ → ページを再読み込み
        logger.info(f"[{clinic_name}] セッション期限切れ。ページ再読み込み")
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(2000)
    else:
        logger.info(f"[{clinic_name}] 保存済みセッションなし")

    # エラーモーダルを確認
    modal_text = await _close_error_modal(page)
    if "別の端末" in modal_text:
        logger.error(f"[{clinic_name}] 別端末がログイン中。スキップします。")
        return False

    # 端末登録 → ログイン
    if not await _register_device(page, group_id, user_id, password, device_name):
        return False

    if not await _try_simple_login(page, user_id, password):
        modal_text = await _close_error_modal(page)
        logger.error(f"[{clinic_name}] ログイン失敗: {modal_text[:100]}")
        return False

    # セッション状態を保存（GCS へ）
    try:
        state = await context.storage_state(path=_LOCAL_SESSION_PATH)
        _save_session_to_gcs(_LOCAL_SESSION_PATH)
        logger.info(f"[{clinic_name}] セッション状態をGCSに保存")
    except Exception as e:
        logger.warning(f"[{clinic_name}] セッション保存失敗: {e}")

    logger.info(f"[{clinic_name}] ログイン成功")
    return True


# ──────────────────────────────────────────────
# 翌日移動（カレンダー構造確認後に実装）
# ──────────────────────────────────────────────

async def navigate_to_tomorrow_media(page, clinic_name: str) -> bool:
    """翌日に移動（TODO: カレンダー構造確認後に実装）"""
    logger.warning(f"[{clinic_name}] navigate_to_tomorrow_media: 未実装")
    return False


# ──────────────────────────────────────────────
# 空き枠検出（カレンダー構造確認後に実装）
# ──────────────────────────────────────────────

async def get_media_empty_slots(page, clinic_name: str,
                                start_hour: int = 9,
                                end_hour: int = 19) -> Dict[str, List[int]]:
    """空き枠を取得（TODO: カレンダー構造確認後に実装）"""
    logger.warning(f"[{clinic_name}] get_media_empty_slots: 未実装")
    return {}


# ──────────────────────────────────────────────
# スタッフ同期（カレンダー構造確認後に実装）
# ──────────────────────────────────────────────

async def sync_media_staff(clinics: list, headless: bool = True) -> Dict[str, List[str]]:
    """スタッフ名を同期（TODO: カレンダー構造確認後に実装）"""
    results = {}
    for clinic in clinics:
        results[clinic['name']] = []
    return results


# ──────────────────────────────────────────────
# メイン実行関数
# ──────────────────────────────────────────────

async def scrape_all_media(clinics: list, headless: bool = True,
                           semaphore=None) -> Dict[str, Dict]:
    """全メディア分院の空き枠を取得（TODO: get_media_empty_slots 実装後に有効化）"""
    from playwright.async_api import async_playwright

    results = {}
    if not clinics:
        return results

    # 初回: GCS からセッション状態をダウンロード
    _load_session_from_gcs()

    async with async_playwright() as p:
        for clinic in clinics:
            clinic_name = clinic['name']
            try:
                storage_state = _LOCAL_SESSION_PATH if os.path.exists(_LOCAL_SESSION_PATH) else None
                context = await p.chromium.launch(headless=headless)
                ctx = await context.new_context(
                    viewport={"width": 1280, "height": 900},
                    storage_state=storage_state
                )
                page = await ctx.new_page()

                logged_in = await login_media(
                    page, ctx, clinic_name,
                    clinic.get('url', LOGIN_URL),
                    clinic.get('group_id', ''),
                    clinic.get('id', ''),
                    clinic.get('password', ''),
                    clinic.get('device_name', 'さくら会マーケPC')
                )

                if not logged_in:
                    results[clinic_name] = {'empty_slots': {}, 'error': 'login_failed'}
                    await ctx.close()
                    await context.close()
                    continue

                # TODO: カレンダー構造確認後に実装
                # await navigate_to_tomorrow_media(page, clinic_name)
                # empty_slots = await get_media_empty_slots(page, clinic_name)
                empty_slots = {}

                results[clinic_name] = {'empty_slots': empty_slots, 'error': None}
                await ctx.close()
                await context.close()

            except Exception as e:
                logger.error(f"[{clinic_name}] スクレイプエラー: {e}")
                results[clinic_name] = {'empty_slots': {}, 'error': str(e)}

    return results
