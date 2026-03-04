"""GMO Reserve スクレイパー

さくら医院歯科（GMO Reserve: reserve.ne.jp）の予約カレンダーから空き枠を取得。
- ログイン → 歯科タブ切替 → datepicker swipe_move で翌日遷移
- 空き枠判定: elementsFromPoint + div_reserve テキスト検査
  (ピクセル色だけでは空き/予約済を区別不可: 両方 rgb(255,229,127))
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

    datepicker の onSelect コールバック経由で swipe_move を呼び出す。
    swipe_move は今日のボックス(display:none) → 翌日のボックス(display:block) に切替。
    """
    logger.info(f"[{clinic_name}] 翌日遷移開始: {tomorrow_str}")

    # datepicker onSelect → swipe_move 呼び出し
    nav_result = await page.evaluate('''(dateStr) => {
        const info = {method: null, error: null};
        try {
            const dpEl = document.getElementById('div_swipe_calendar');
            if (dpEl) {
                const inst = $.datepicker._getInst(dpEl);
                if (inst && inst.settings && typeof inst.settings.onSelect === 'function') {
                    inst.settings.onSelect(dateStr, inst);
                    info.method = 'onSelect_direct';
                }
            }
        } catch(e) {
            info.error = e.message;
        }
        return info;
    }''', tomorrow_str)

    if nav_result.get('error'):
        logger.warning(f"[{clinic_name}] swipe_move エラー: {nav_result['error']}")

    if nav_result.get('method'):
        # swipe_move 完了待ち
        await page.wait_for_timeout(8000)
        try:
            await page.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            pass

        # 表示中ボックスを確認
        after_state = await page.evaluate('''() => {
            const superParents = document.querySelectorAll('.fix_grid_super_parent');
            const visible = [...superParents].find(sp => {
                const s = sp.getAttribute('style') || '';
                return !s.includes('display: none') && !s.includes('display:none');
            });
            if (!visible) return {id: null, cols: 0};
            return {
                id: visible.id || '',
                cols: visible.querySelectorAll('.div_column_head').length,
            };
        }''')

        try:
            await page.screenshot(path=f'logs/screenshots/gmo_{clinic_name}_after_nav.png')
        except Exception:
            pass

        if after_state.get('id') and after_state.get('cols', 0) > 0:
            box_num = after_state['id'].replace('div_super_parent_table_box_', '')
            logger.info(f"[{clinic_name}] 翌日遷移成功: box={box_num}, 列数={after_state['cols']}")
            return True

    logger.warning(f"[{clinic_name}] 翌日遷移失敗")
    return False


async def get_gmo_empty_slots(
    page, clinic_name: str
) -> Dict[str, List[int]]:
    """GMO Reserveカレンダーから空き枠を取得

    Returns: {staff_name: [slot_times_in_minutes]}
    """
    tomorrow = datetime.now(JST) + timedelta(days=1)
    tomorrow_str = tomorrow.strftime('%Y-%m-%d')
    logger.info(f"[{clinic_name}] 翌日: {tomorrow_str}")

    # 表示中のスワイプボックスを特定
    # swipe_move後、表示中のboxは display:none でないもの
    visible_box_info = await page.evaluate('''() => {
        const superParents = document.querySelectorAll('.fix_grid_super_parent');
        for (const sp of superParents) {
            const style = sp.getAttribute('style') || '';
            if (!style.includes('display: none') && !style.includes('display:none')) {
                const boxNum = (sp.id || '').replace('div_super_parent_table_box_', '');
                return {
                    boxNum: boxNum,
                    bodyTableId: 'table_box_' + boxNum,
                    headerTableId: 'table_fix_top_table_box_' + boxNum,
                };
            }
        }
        // フォールバック: デフォルト500
        return {boxNum: '500', bodyTableId: 'table_box_500', headerTableId: 'table_fix_top_table_box_500'};
    }''')
    body_table_id = visible_box_info['bodyTableId']
    header_table_id = visible_box_info['headerTableId']
    logger.info(f"[{clinic_name}] 表示中box: {visible_box_info['boxNum']} "
                f"body={body_table_id} header={header_table_id}")

    # ページのスクリーンショットを保存（デバッグ用）
    try:
        await page.screenshot(path=f'logs/screenshots/gmo_{clinic_name}.png', full_page=True)
    except Exception:
        pass

    # DOM検査 + スクリーンショットPixelでグレー除外のハイブリッド方式
    # ピクセル色だけでは空き/予約済の区別不可（両方 rgb(255,229,127)）
    # → div_reserve オーバーレイの有無で判定
    await page.set_viewport_size({'width': 2500, 'height': 5000})
    await page.wait_for_timeout(2000)

    # 表示中ボックスのカレンダー本体テーブルのスクリーンショット（グレー判定用）
    body_table = page.locator(f'#{body_table_id}')
    try:
        screenshot_bytes = await body_table.screenshot()
    except Exception as e:
        logger.warning(f"[{clinic_name}] テーブル({body_table_id})スクリーンショット失敗: {e}")
        screenshot_bytes = await page.screenshot(full_page=True)

    b64 = base64.b64encode(screenshot_bytes).decode()

    # デバッグ: スクリーンショット保存
    try:
        import os
        os.makedirs('logs/screenshots', exist_ok=True)
        with open(f'logs/screenshots/gmo_{clinic_name}_body_table.png', 'wb') as f:
            f.write(screenshot_bytes)
    except Exception:
        pass

    # DOM検査(elementsFromPoint + div_reserve テキスト) + Canvas APIグレー判定
    slot_data = await page.evaluate('''async ({b64, bodyTableId, headerTableId}) => {
        const result = {staffSlots: {}, debug: {emptyCellCount: 0, totalCells: 0}};

        // Screenshot → Canvas（グレーセル除外用）
        const img = new Image();
        img.src = 'data:image/png;base64,' + b64;
        await new Promise(r => { img.onload = r; });
        const canvas = document.createElement('canvas');
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);

        const bodyTable = document.getElementById(bodyTableId);
        if (!bodyTable) {
            result.debug.error = bodyTableId + ' not found';
            return result;
        }
        const bodyRect = bodyTable.getBoundingClientRect();
        const scaleX = canvas.width / (bodyRect.width || 1);
        const scaleY = canvas.height / (bodyRect.height || 1);

        // Header: スタッフ名を取得
        const headerTable = document.getElementById(headerTableId);
        const colHeads = headerTable
            ? headerTable.querySelectorAll('.div_column_head')
            : [];
        const columnInfo = [];
        for (const head of colHeads) {
            const text = (head.textContent || '').trim();
            const name = text.replace(/^▼/, '').replace(/\(.*$/, '').replace(/（.*$/, '').trim();
            if (name) columnInfo.push({name});
        }

        // td_reserve セルを取得
        const allTdReserves = bodyTable.querySelectorAll('.td_reserve');
        if (allTdReserves.length === 0) {
            result.debug.error = 'No td_reserve cells found';
            return result;
        }

        // 最初の行から列のx座標を取得
        const firstY = allTdReserves[0].getBoundingClientRect().y;
        for (const td of allTdReserves) {
            const r = td.getBoundingClientRect();
            if (Math.abs(r.y - firstY) > 3) break;
            const colIdx = columnInfo.findIndex(c => !c.x);
            if (colIdx >= 0) {
                columnInfo[colIdx].x = r.x;
                columnInfo[colIdx].width = r.width;
            }
        }

        // 時間グリッド
        const calInfo = window.calendar_info || {};
        let minTime = 540, maxTime = 1200;
        if (calInfo.min_time) {
            const p = calInfo.min_time.split(':');
            minTime = parseInt(p[0]) * 60 + parseInt(p[1]);
        }
        if (calInfo.max_time) {
            const p = calInfo.max_time.split(':');
            maxTime = parseInt(p[0]) * 60 + parseInt(p[1]);
        }

        const cellHeight = allTdReserves[0].getBoundingClientRect().height;
        const gridStartY = allTdReserves[0].getBoundingClientRect().y;

        // === 各td_reserveセルの空き判定 ===
        // elementsFromPoint でセル中心の div_reserve を検査し、
        // テキスト/子要素の有無で予約済みかどうかを判定
        const staffSlots = {};
        let emptyCount = 0, totalCount = 0;

        for (const td of allTdReserves) {
            totalCount++;
            const rect = td.getBoundingClientRect();

            // 列特定
            let colIdx = -1;
            for (let i = 0; i < columnInfo.length; i++) {
                if (columnInfo[i].x && Math.abs(columnInfo[i].x - rect.x) < 5) {
                    colIdx = i;
                    break;
                }
            }
            if (colIdx < 0) continue;

            // 時間特定
            const slot = Math.round((rect.y - gridStartY) / cellHeight);
            const timeMin = minTime + slot * 15;
            if (timeMin < minTime || timeMin >= maxTime) continue;

            // 判定1: elementsFromPoint でセル中心の div_reserve を検査
            const cx = rect.x + rect.width / 2;
            const cy = rect.y + rect.height / 2;
            const elementsAtPoint = document.elementsFromPoint(cx, cy);
            let hasBookedOverlay = false;
            for (const el of elementsAtPoint) {
                if (el === td || el === bodyTable) continue;
                const cls = (typeof el.className === 'string') ? el.className : '';
                if (cls.includes('div_reserve')) {
                    // テキストあり OR 子要素あり → 予約済
                    const overlayText = (el.textContent || '').trim();
                    hasBookedOverlay = overlayText.length > 0 || el.children.length > 0;
                    break;
                }
            }

            // 判定2: テキストあり → 予約済（td自体のテキスト）
            const hasText = (td.textContent || '').trim().length > 0;

            // 判定3: グレーピクセル → 非稼働時間
            const px = Math.floor((rect.x - bodyRect.x + rect.width / 2) * scaleX);
            const py = Math.floor((rect.y - bodyRect.y + rect.height / 2) * scaleY);
            let isGray = false;
            if (px >= 0 && py >= 0 && px < canvas.width && py < canvas.height) {
                const d = ctx.getImageData(px, py, 1, 1).data;
                isGray = (Math.abs(d[0]-d[1]) < 20 && Math.abs(d[1]-d[2]) < 20 && d[0] > 150 && d[0] < 245);
            }

            // 空き枠 = 予約ブロックなし ＆ テキストなし ＆ 非グレー
            if (!hasBookedOverlay && !hasText && !isGray) {
                emptyCount++;
                const colName = columnInfo[colIdx].name;
                if (!staffSlots[colName]) staffSlots[colName] = [];
                staffSlots[colName].push(timeMin);
            }
        }

        result.staffSlots = staffSlots;
        result.debug.emptyCellCount = emptyCount;
        result.debug.totalCells = totalCount;
        result.debug.columns = columnInfo.length;
        result.debug.columnNames = columnInfo.map(c => c.name);

        return result;
    }''', {'b64': b64, 'bodyTableId': body_table_id, 'headerTableId': header_table_id})

    # 結果をログ出力
    debug = slot_data.get('debug', {})
    logger.info(f"[{clinic_name}] グリッド: {debug.get('columns', 0)}列, "
                f"total={debug.get('totalCells', 0)}セル, "
                f"空き={debug.get('emptyCellCount', 0)}セル")
    logger.info(f"[{clinic_name}] 列名: {debug.get('columnNames', [])}")

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
