"""Playwright ブラウザプール管理

Flaskアプリ起動時にPlaywright/Chromiumを1回だけ起動し、
チェック実行時は既存ブラウザを再利用することで起動時間を排除する。
"""

import asyncio
import threading
import logging
import time

logger = logging.getLogger(__name__)

_playwright = None
_browser = None
_loop = None
_thread = None
_ready = threading.Event()


def _log(msg):
    """Cloud Runでも確実に表示されるログ出力"""
    print(f"[BROWSER_POOL] {msg}", flush=True)
    logger.info(msg)


def _run_loop():
    """イベントループをバックグラウンドスレッドで実行"""
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


async def _start_browser():
    """Playwright + Chromium を起動"""
    global _playwright, _browser
    from playwright.async_api import async_playwright

    t0 = time.time()
    _log("Playwright起動中...")
    _playwright = await async_playwright().start()
    _log(f"Playwright起動完了 ({time.time()-t0:.1f}s)")

    t1 = time.time()
    _log("Chromium起動中...")
    _browser = await _playwright.chromium.launch(
        headless=True,
        args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
    )
    _log(f"Chromium起動完了 ({time.time()-t1:.1f}s), 合計: {time.time()-t0:.1f}s")


def init_browser():
    """Flask起動時に呼び出し: ブラウザを事前起動"""
    global _thread
    try:
        _log("初期化開始...")
        _thread = threading.Thread(target=_run_loop, daemon=True)
        _thread.start()

        # イベントループが開始されるまで少し待つ
        time.sleep(0.1)

        future = asyncio.run_coroutine_threadsafe(_start_browser(), _loop)
        future.result(timeout=600)  # 最大10分待つ
        _ready.set()
        _log("初期化完了 - ブラウザ準備OK")
    except Exception as e:
        _log(f"初期化失敗: {e}")
        import traceback
        traceback.print_exc()


def get_browser():
    """起動済みブラウザを取得"""
    if not _ready.wait(timeout=600):
        raise RuntimeError("ブラウザプール: 初期化タイムアウト")
    return _browser


def run_async(coro):
    """async関数をFlaskのsyncコンテキストから実行"""
    if _loop is None:
        raise RuntimeError("ブラウザプール: イベントループ未初期化")
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=600)


def is_ready():
    """ブラウザプールが準備完了か"""
    return _ready.is_set()
