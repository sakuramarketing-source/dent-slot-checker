"""GMO Reserve スクレイパー

さくら医院歯科（GMO Reserve: reserve.ne.jp）の予約カレンダーから空き枠を取得。
- ログイン → 歯科タブ切替 → 翌日の空き枠検出
- 空き枠判定: 黄色背景 + テキストなし
"""

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


async def login_gmo(page, url: str, login_id: str, password: str, clinic_name: str):
    """GMO Reserveにログイン"""
    logger.info(f"[{clinic_name}] ログイン開始: {url}")
    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
    logger.info(f"[{clinic_name}] ページ読み込み完了")

    # ID/パスワード入力（GMO Reserve固有のid属性）
    await page.fill('#p-Panel--loginForm__id', login_id)
    await page.fill('#p-Panel--loginForm__pw', password)

    # ログインボタンクリック（type="button"、AJAXログイン）
    await page.click('#p-Panel--loginForm__loginButton')

    # AJAXログイン → ナビゲーション or ページ変化を待機
    try:
        await page.wait_for_navigation(timeout=15000)
    except Exception:
        # ナビゲーションが発生しない場合（SPA的な遷移）→ 少し待つ
        await page.wait_for_timeout(3000)

    await page.wait_for_load_state('networkidle', timeout=15000)

    # デバッグ: ログイン後のスクリーンショット保存
    try:
        import os
        os.makedirs('logs/screenshots', exist_ok=True)
        await page.screenshot(path=f'logs/screenshots/gmo_{clinic_name}_after_login.png')
    except Exception:
        pass

    logger.info(f"[{clinic_name}] ログイン後URL: {page.url}")


async def switch_to_dental_tab(page, clinic_name: str):
    """医科→歯科タブに切り替え（メイン切替ドロップダウン）"""
    try:
        # メイン切替ドロップダウンで「【歯科】」(value=7)を選択
        await page.select_option('#select_topmenu_main', value='7')
        await page.wait_for_load_state('networkidle', timeout=15000)
        await page.wait_for_timeout(5000)
        logger.info(f"[{clinic_name}] 歯科タブ切替成功: select_topmenu_main value=7")

        # 切替後スクリーンショット
        try:
            await page.screenshot(path=f'logs/screenshots/gmo_{clinic_name}_dental_tab.png')
        except Exception:
            pass
        return True
    except Exception as e:
        logger.warning(f"[{clinic_name}] 歯科タブ切替失敗: {e}")
        return False


async def navigate_to_tomorrow_gmo(page, clinic_name: str, tomorrow_str: str) -> bool:
    """カレンダーを翌日に遷移

    試行順: URL パラメータ → JS関数 → 次日ボタンクリック
    """
    current_url = page.url
    logger.info(f"[{clinic_name}] 翌日遷移開始: {tomorrow_str}")

    # 方法A: URLパラメータで日付指定
    # calendar.php?view_date=YYYY-MM-DD
    if 'calendar.php' in current_url:
        base = current_url.split('?')[0]
        target_url = f"{base}?view_date={tomorrow_str}"
        logger.info(f"[{clinic_name}] URL遷移: {target_url}")
        await page.goto(target_url, wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(3000)

        # 遷移後スクリーンショット
        try:
            await page.screenshot(path=f'logs/screenshots/gmo_{clinic_name}_after_nav.png')
        except Exception:
            pass

        # URL遷移が成功したか確認
        new_url = page.url
        logger.info(f"[{clinic_name}] 遷移後URL: {new_url}")

        # ページ内の日付表示で確認
        page_date_info = await page.evaluate('''(tomorrowStr) => {
            const info = {};
            // calendar_info から日付を確認
            if (window.calendar_info) {
                info.view_date = calendar_info.view_date || null;
                info.current_date = calendar_info.current_date || null;
            }
            // ページタイトルや日付表示を確認
            const title = document.title || '';
            info.title = title;
            // body text から日付を探す（最初の500文字のみ）
            const bodyText = (document.body.innerText || '').substring(0, 500);
            info.hasDate = bodyText.includes(tomorrowStr) || bodyText.includes(tomorrowStr.replace(/-/g, '/'));
            // div_column_head が存在するか（歯科タブが維持されているか）
            info.colHeadCount = document.querySelectorAll('.div_column_head').length;
            return info;
        }''', tomorrow_str)
        logger.info(f"[{clinic_name}] 遷移確認: {page_date_info}")

        # 歯科タブが維持されていて列ヘッダーが見えればOK
        if page_date_info.get('colHeadCount', 0) > 0:
            logger.info(f"[{clinic_name}] URL遷移成功: 歯科カレンダー表示中 ({page_date_info.get('colHeadCount')}列)")
            return True

        logger.info(f"[{clinic_name}] URL遷移後、歯科カレンダーが見えない → フォールバック")

    # 方法B: JS関数で日付移動
    js_nav = await page.evaluate('''(dateStr) => {
        // GMO Reserve の一般的な日付移動関数を試行
        if (typeof move_date === 'function') {
            move_date(dateStr);
            return 'move_date';
        }
        if (typeof changeDate === 'function') {
            changeDate(dateStr);
            return 'changeDate';
        }
        if (typeof goToDate === 'function') {
            goToDate(dateStr);
            return 'goToDate';
        }
        // calendar_info から日付移動関数を探す
        if (window.calendar_info && typeof window.calendar_info.move_date === 'function') {
            window.calendar_info.move_date(dateStr);
            return 'calendar_info.move_date';
        }
        return null;
    }''', tomorrow_str)

    if js_nav:
        logger.info(f"[{clinic_name}] JS関数 {js_nav} で遷移")
        await page.wait_for_load_state('networkidle', timeout=15000)
        await page.wait_for_timeout(3000)
        return True

    # 方法C: 次日ボタンクリック
    next_selectors = [
        'a:has-text(">")', 'a:has-text("翌日")', 'a:has-text("次")',
        '.next-day', '.btn-next', '[onclick*="next"]',
        'img[alt="次"]', 'img[alt=">"]',
    ]
    for sel in next_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1000):
                await btn.click()
                await page.wait_for_load_state('networkidle', timeout=15000)
                await page.wait_for_timeout(3000)
                logger.info(f"[{clinic_name}] ボタンクリックで翌日遷移: {sel}")
                return True
        except Exception:
            continue

    logger.warning(f"[{clinic_name}] 翌日遷移失敗: すべての方法が失敗")
    return False


async def get_gmo_empty_slots(
    page, clinic_name: str
) -> Dict[str, List[int]]:
    """GMO Reserveカレンダーから空き枠を取得

    Returns: {staff_name: [slot_times_in_minutes]}
    """
    tomorrow = datetime.now(JST) + timedelta(days=1)
    tomorrow_str = tomorrow.strftime('%Y-%m-%d')
    tomorrow_day = tomorrow.day
    tomorrow_weekday_ja = ['月', '火', '水', '木', '金', '土', '日'][tomorrow.weekday()]
    logger.info(f"[{clinic_name}] 翌日: {tomorrow_str} ({tomorrow_weekday_ja})")

    # ページのスクリーンショットを保存（デバッグ用）
    try:
        await page.screenshot(path=f'logs/screenshots/gmo_{clinic_name}.png', full_page=True)
    except Exception:
        pass

    # Screenshot + Canvas API で黄色セルを検出
    # GMO Reserveのカレンダーはtd背景がCSSで黄色に設定されるが、
    # getComputedStyleはheadless Chromiumで透明を返すため、ピクセル色で判定

    # viewportを拡大してカレンダー全体をキャプチャ
    await page.set_viewport_size({'width': 2500, 'height': 5000})
    await page.wait_for_timeout(2000)

    # カレンダー本体テーブルのスクリーンショット
    body_table = page.locator('#table_box_500')
    try:
        screenshot_bytes = await body_table.screenshot()
    except Exception as e:
        logger.warning(f"[{clinic_name}] テーブルスクリーンショット失敗: {e}")
        # フォールバック: フルページスクリーンショット
        screenshot_bytes = await page.screenshot(full_page=True)

    b64 = base64.b64encode(screenshot_bytes).decode()

    # デバッグ: body tableスクリーンショットを保存
    try:
        import os
        os.makedirs('logs/screenshots', exist_ok=True)
        with open(f'logs/screenshots/gmo_{clinic_name}_body_table.png', 'wb') as f:
            f.write(screenshot_bytes)
        logger.info(f"[{clinic_name}] body tableスクリーンショット保存: logs/screenshots/gmo_{clinic_name}_body_table.png")
    except Exception as e:
        logger.warning(f"[{clinic_name}] スクリーンショット保存失敗: {e}")

    # Canvas APIでピクセル色を解析 + グリッド座標を取得
    slot_data = await page.evaluate('''async ({b64, tomorrowStr}) => {
        const result = {
            staffSlots: {},
            debug: {
                gridInfo: null,
                yellowCellCount: 0,
                samplePixels: [],
            }
        };

        // screenshot → Canvas
        const img = new Image();
        img.src = 'data:image/png;base64,' + b64;
        await new Promise(r => { img.onload = r; });
        const canvas = document.createElement('canvas');
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);

        // テーブル本体の座標
        const bodyTable = document.getElementById('table_box_500');
        if (!bodyTable) {
            result.debug.error = 'table_box_500 not found';
            return result;
        }
        const bodyRect = bodyTable.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        const scaleX = canvas.width / (bodyRect.width || 1);
        const scaleY = canvas.height / (bodyRect.height || 1);

        // スタッフ列情報を取得: td_reserve セルから列の x 座標を解析
        // table_box_500 内の全 td_reserve セルを取得
        const tdReserves = bodyTable.querySelectorAll('.td_reserve');
        // td_reserve セルを行ごとにグループ化（同じ y 座標のセル = 同じ行）
        // 各列の x 座標とスタッフ名を特定
        const columnInfo = [];  // [{x, width, mmId, name}]
        const seenX = new Set();

        // div_column_head からスタッフ列情報を取得（ヘッダーテーブル内）
        const headerTable = document.getElementById('table_fix_top_table_box_500');
        const colHeads = headerTable
            ? headerTable.querySelectorAll('.div_column_head')
            : document.querySelectorAll('.div_column_head');
        for (const head of colHeads) {
            const text = (head.textContent || '').trim();
            const style = head.getAttribute('style') || '';
            const widthMatch = style.match(/width:\s*(\d+)px/);
            const w = widthMatch ? parseInt(widthMatch[1]) : 180;
            // 名前を正規化
            const name = text.replace(/^▼/, '').replace(/\(.*$/, '').replace(/（.*$/, '').trim();
            if (name) {
                columnInfo.push({name, width: w});
            }
        }

        // td_reserve セルの最初の行から各列のx座標を取得
        const firstRowCells = [];
        if (tdReserves.length > 0) {
            // 最初の行のセルを収集（y座標が同じ）
            const firstY = tdReserves[0].getBoundingClientRect().y;
            for (const td of tdReserves) {
                const r = td.getBoundingClientRect();
                if (Math.abs(r.y - firstY) < 3) {
                    firstRowCells.push({x: r.x - bodyRect.x, width: r.width, height: r.height});
                }
            }
        }

        // 列のx座標が取れた場合、columnInfoにマージ
        for (let i = 0; i < Math.min(firstRowCells.length, columnInfo.length); i++) {
            columnInfo[i].x = firstRowCells[i].x;
            columnInfo[i].cellWidth = firstRowCells[i].width;
            columnInfo[i].cellHeight = firstRowCells[i].height;
        }

        // 時間グリッド情報
        const calInfo = window.calendar_info || {};
        let minTime = 540;  // 9:00
        let maxTime = 1200; // 20:00
        if (calInfo.min_time) {
            const p = calInfo.min_time.split(':');
            minTime = parseInt(p[0]) * 60 + parseInt(p[1]);
        }
        if (calInfo.max_time) {
            const p = calInfo.max_time.split(':');
            maxTime = parseInt(p[0]) * 60 + parseInt(p[1]);
        }

        // 行の高さを推定: td_reserve の height から
        let rowHeight15min = 0;
        if (firstRowCells.length > 0 && firstRowCells[0].height > 0) {
            rowHeight15min = firstRowCells[0].height;  // 1セル = 15分
        }

        // テーブルヘッダー行の高さ（時間行の開始位置を特定）
        // 最初のtd_reserveのy座標がヘッダー下端
        let gridStartY = 0;
        if (tdReserves.length > 0) {
            gridStartY = tdReserves[0].getBoundingClientRect().y - bodyRect.y;
        }

        const totalTimeSlots = (maxTime - minTime) / 15;
        if (rowHeight15min === 0 && bodyRect.height > 0) {
            // フォールバック: テーブル高さから推定
            rowHeight15min = (bodyRect.height - gridStartY) / totalTimeSlots;
        }

        result.debug.gridInfo = {
            columns: columnInfo.length,
            columnNames: columnInfo.map(c => c.name),
            firstRowCells: firstRowCells.length,
            rowHeight15min: Math.round(rowHeight15min * 10) / 10,
            gridStartY: Math.round(gridStartY),
            bodySize: {w: Math.round(bodyRect.width), h: Math.round(bodyRect.height)},
            canvasSize: {w: canvas.width, h: canvas.height},
            dpr: dpr,
            scaleX: Math.round(scaleX * 100) / 100,
            scaleY: Math.round(scaleY * 100) / 100,
            timeRange: {min: minTime, max: maxTime, slots: totalTimeSlots},
        };

        // 各セルのピクセル色をサンプリング
        let yellowCount = 0;
        const staffSlots = {};

        for (let colIdx = 0; colIdx < columnInfo.length; colIdx++) {
            const col = columnInfo[colIdx];
            if (!col.x && col.x !== 0) continue;  // x座標不明
            const colCenterX = col.x + (col.cellWidth || col.width) / 2;

            for (let slot = 0; slot < totalTimeSlots; slot++) {
                const timeMin = minTime + slot * 15;
                const cellCenterY = gridStartY + (slot + 0.5) * rowHeight15min;

                // スクリーンショット座標に変換
                const px = Math.floor(colCenterX * scaleX);
                const py = Math.floor(cellCenterY * scaleY);

                if (px < 0 || py < 0 || px >= canvas.width || py >= canvas.height) continue;

                const d = ctx.getImageData(px, py, 1, 1).data;
                const r = d[0], g = d[1], b = d[2];

                // 空き枠判定: lemonchiffon rgb(255,250,205) 系
                // R>240, G>240, B>150 で黄色系、(R-B)>20 && (G-B)>20 でグレー除外
                const isYellow = (r > 240 && g > 240 && b > 150 && (r - b) > 20 && (g - b) > 20);

                // サンプルピクセル（デバッグ用、全列の最初10スロット）
                if (slot < 10) {
                    result.debug.samplePixels.push({
                        col: col.name,
                        time: Math.floor(timeMin/60) + ':' + String(timeMin%60).padStart(2, '0'),
                        px, py, r, g, b, isYellow,
                    });
                }

                if (isYellow) {
                    yellowCount++;
                    if (!staffSlots[col.name]) staffSlots[col.name] = [];
                    staffSlots[col.name].push(timeMin);
                }
            }
        }

        result.staffSlots = staffSlots;
        result.debug.yellowCellCount = yellowCount;

        return result;
    }''', {'b64': b64, 'tomorrowStr': tomorrow_str})

    # 結果をログ出力
    debug = slot_data.get('debug', {})
    grid = debug.get('gridInfo', {})
    logger.info(f"[{clinic_name}] グリッド: {grid.get('columns', 0)}列, "
                f"rowH={grid.get('rowHeight15min', 0)}px, "
                f"gridStartY={grid.get('gridStartY', 0)}, "
                f"canvas={grid.get('canvasSize', {})}, "
                f"timeSlots={grid.get('timeRange', {}).get('slots', 0)}")
    logger.info(f"[{clinic_name}] 列名: {grid.get('columnNames', [])}")
    logger.info(f"[{clinic_name}] 黄色セル: {debug.get('yellowCellCount', 0)}")

    # サンプルピクセル
    for sp in debug.get('samplePixels', []):
        logger.info(f"  [{sp['col']}] {sp['time']}: px=({sp['px']},{sp['py']}) "
                     f"rgb({sp['r']},{sp['g']},{sp['b']}) yellow={sp['isYellow']}")

    if debug.get('error'):
        logger.warning(f"[{clinic_name}] {debug['error']}")

    # 結果を取得
    chair_slots = slot_data.get('staffSlots', {})

    # 結果をログ出力
    if not chair_slots:
        logger.info(f"[{clinic_name}] 空き枠なし")
    for staff, slots in sorted(chair_slots.items()):
        times_str = ', '.join(f"{s//60}:{s%60:02d}" for s in sorted(slots)[:10])
        extra = f"... (+{len(slots) - 10})" if len(slots) > 10 else ""
        logger.info(f"  {staff}: {len(slots)}スロット: {times_str}{extra}")

    return chair_slots


async def scrape_gmo_clinic(
    browser, clinic: dict
) -> tuple:
    """単一GMOクリニックのスクレイピング

    Returns: (clinic_name, {staff_name: [slot_times]})
    """
    clinic_name = clinic['name']
    url = clinic['url']
    login_id = clinic.get('id', '')
    password = clinic.get('password', '')

    if not login_id or not password:
        logger.error(f"[{clinic_name}] 認証情報がありません")
        return clinic_name, {}

    logger.info(f"GMO スクレイピング開始: {clinic_name}")

    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        locale='ja-JP'
    )
    page = await context.new_page()

    try:
        # ログイン
        await login_gmo(page, url, login_id, password, clinic_name)

        # 歯科タブ切替
        await switch_to_dental_tab(page, clinic_name)

        # 翌日に遷移
        tomorrow = datetime.now(JST) + timedelta(days=1)
        tomorrow_str = tomorrow.strftime('%Y-%m-%d')
        await navigate_to_tomorrow_gmo(page, clinic_name, tomorrow_str)

        # 空き枠取得
        slots = await get_gmo_empty_slots(page, clinic_name)

        logger.info(f"GMO スクレイピング完了: {clinic_name}")
        return clinic_name, slots

    except Exception as e:
        logger.error(f"[{clinic_name}] GMOスクレイピング失敗: {e}")
        return clinic_name, {}

    finally:
        await context.close()


async def scrape_all_gmo_clinics(
    gmo_clinics: list,
    headless: bool = True,
    browser=None
) -> Dict[str, Dict[str, List[int]]]:
    """全GMOクリニックをスクレイピング

    Returns: {clinic_name: {staff_name: [slot_times_in_minutes]}}
    """
    if not gmo_clinics:
        return {}

    results = {}
    own_browser = False

    if browser is None:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=headless)
        own_browser = True

    logger.info(f"GMO スクレイピング: {len(gmo_clinics)}クリニック")

    for clinic in gmo_clinics:
        if not clinic.get('enabled', True):
            continue
        name, slots = await scrape_gmo_clinic(browser, clinic)
        results[name] = slots

    if own_browser:
        await browser.close()

    return results


async def sync_gmo_staff(
    clinics: list,
    headless: bool = True
) -> Dict[str, List[str]]:
    """GMO Reserve 全分院のスタッフ名を同期取得

    ログイン → 歯科タブ切替 → カレンダーヘッダーからスタッフ名を取得

    Returns: {clinic_name: [staff_name, ...]}
    """
    from playwright.async_api import async_playwright

    results = {}
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless)

    for clinic in clinics:
        if not clinic.get('enabled', True):
            continue

        clinic_name = clinic['name']
        login_id = clinic.get('id', '')
        password = clinic.get('password', '')

        if not login_id or not password:
            logger.warning(f"[{clinic_name}] 認証情報なし、スキップ")
            continue

        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            locale='ja-JP'
        )
        page = await context.new_page()

        try:
            await login_gmo(page, clinic['url'], login_id, password, clinic_name)
            await switch_to_dental_tab(page, clinic_name)

            # ヘッダーテーブル + select dropdown からスタッフ名を取得
            staff_names = await page.evaluate('''() => {
                const names = [];

                // 方法1: select_topmenu_main のoption（個別スタッフ）
                const select = document.getElementById('select_topmenu_main');
                if (select) {
                    for (const opt of select.options) {
                        const text = (opt.textContent || '').trim();
                        // 歯科スタッフのオプション（「先生」「DH」「検診」を含む）
                        if (text.includes('先生') || text.includes('DH') || text.includes('検診')) {
                            // 括弧以降を除去してクリーンな名前に
                            const clean = text.replace(/\(.*$/, '').replace(/（.*$/, '').trim();
                            if (clean && !names.includes(clean)) {
                                names.push(clean);
                            }
                        }
                    }
                }

                // 方法2: ヘッダーテーブルからスタッフ名を取得（フォールバック）
                if (names.length === 0) {
                    const headerTable = document.getElementById('table_fix_top_table_box_500');
                    if (headerTable) {
                        const text = headerTable.textContent || '';
                        // ▼で区切られたスタッフ名を抽出
                        const parts = text.split('▼').filter(p => p.trim());
                        for (const part of parts) {
                            const clean = part.replace(/\(.*$/, '').replace(/（.*$/, '').trim();
                            if (clean && (clean.includes('先生') || clean.includes('DH') || clean.includes('検診'))) {
                                if (!names.includes(clean)) {
                                    names.push(clean);
                                }
                            }
                        }
                    }
                }

                return names;
            }''')

            results[clinic_name] = staff_names
            logger.info(f"[{clinic_name}] スタッフ同期: {len(staff_names)}名 - {staff_names}")

        except Exception as e:
            logger.error(f"[{clinic_name}] スタッフ同期失敗: {e}")
            results[clinic_name] = []
        finally:
            await context.close()

    await browser.close()
    return results
