"""GMO Reserve スクレイパー

さくら医院歯科（GMO Reserve: reserve.ne.jp）の予約カレンダーから空き枠を取得。
- ログイン → 歯科タブ切替 → 翌日の空き枠検出
- 空き枠判定: 黄色背景 + テキストなし
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


async def login_gmo(page, url: str, login_id: str, password: str, clinic_name: str):
    """GMO Reserveにログイン"""
    logger.info(f"[{clinic_name}] ログイン開始: {url}")
    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
    logger.info(f"[{clinic_name}] ページ読み込み完了")

    # ID/パスワード入力
    await page.fill('input[name="login_id"], input[name="id"], input[type="text"]', login_id)
    await page.fill('input[name="login_pw"], input[name="password"], input[type="password"]', password)

    # ログインボタンクリック
    login_btn = page.locator('input[type="submit"], button[type="submit"], .login-btn, #login-btn')
    await login_btn.first.click()
    await page.wait_for_load_state('domcontentloaded', timeout=15000)

    logger.info(f"[{clinic_name}] ログイン後URL: {page.url}")


async def switch_to_dental_tab(page, clinic_name: str):
    """医科→歯科タブに切り替え"""
    # 「歯科」タブを探してクリック
    dental_tab_selectors = [
        'a:has-text("歯科")',
        'li:has-text("歯科") a',
        '.tab:has-text("歯科")',
        'span:has-text("歯科")',
    ]

    for selector in dental_tab_selectors:
        try:
            tab = page.locator(selector).first
            if await tab.is_visible(timeout=3000):
                await tab.click()
                await page.wait_for_load_state('domcontentloaded', timeout=10000)
                # ページ更新を待機
                await page.wait_for_timeout(2000)
                logger.info(f"[{clinic_name}] 歯科タブ切替成功: {selector}")
                return True
        except Exception:
            continue

    logger.warning(f"[{clinic_name}] 歯科タブが見つかりません")
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

    # カレンダーのHTML構造を解析
    # GMO Reserveの週表示カレンダーからデータを抽出
    calendar_data = await page.evaluate('''(tomorrowDay) => {
        const result = {
            staffNames: [],
            timeSlots: [],
            cells: [],
            debug: {}
        };

        // カレンダーテーブルを探す
        const tables = document.querySelectorAll('table');
        let scheduleTable = null;

        for (const table of tables) {
            const text = table.textContent || '';
            // 時間表示のあるテーブルを探す
            if (text.includes('9:') || text.includes('10:') || text.includes('09:')) {
                scheduleTable = table;
                break;
            }
        }

        if (!scheduleTable) {
            result.debug.error = 'スケジュールテーブルが見つかりません';
            result.debug.tableCount = tables.length;
            return result;
        }

        const rows = scheduleTable.querySelectorAll('tr');
        result.debug.rowCount = rows.length;

        // ヘッダー行からスタッフ名を取得
        const headerRow = rows[0];
        if (headerRow) {
            const headerCells = headerRow.querySelectorAll('th, td');
            for (let i = 0; i < headerCells.length; i++) {
                const text = (headerCells[i].textContent || '').trim();
                result.staffNames.push({index: i, name: text});
            }
        }

        // 各行のセルデータを取得
        for (let rowIdx = 1; rowIdx < rows.length; rowIdx++) {
            const cells = rows[rowIdx].querySelectorAll('td, th');
            const rowData = [];

            for (let colIdx = 0; colIdx < cells.length; colIdx++) {
                const cell = cells[colIdx];
                const text = (cell.textContent || '').trim();
                const style = window.getComputedStyle(cell);
                const bgColor = style.backgroundColor;
                const classList = cell.className || '';

                rowData.push({
                    text: text,
                    bgColor: bgColor,
                    classList: classList,
                    colIdx: colIdx,
                    innerHTML: cell.innerHTML.substring(0, 100),
                    colspan: cell.getAttribute('colspan') || '',
                    rowspan: cell.getAttribute('rowspan') || '',
                });
            }

            result.cells.push(rowData);
        }

        return result;
    }''', tomorrow_day)

    logger.info(f"[{clinic_name}] カレンダー解析: "
                f"staff={len(calendar_data.get('staffNames', []))}, "
                f"rows={len(calendar_data.get('cells', []))}")

    if calendar_data.get('debug', {}).get('error'):
        logger.warning(f"[{clinic_name}] {calendar_data['debug']['error']}")
        logger.info(f"[{clinic_name}] debug: {calendar_data['debug']}")
        return {}

    # スタッフ名を解析
    staff_names = calendar_data.get('staffNames', [])
    logger.info(f"[{clinic_name}] ヘッダー: {[s['name'][:20] for s in staff_names]}")

    # 各行からデータを取得
    chair_slots: Dict[str, List[int]] = {}

    for row_idx, row_data in enumerate(calendar_data.get('cells', [])):
        if not row_data:
            continue

        # 最初のセルから時間を取得
        first_cell = row_data[0] if row_data else {}
        time_text = first_cell.get('text', '').strip()

        # 時間パース
        time_minutes = None
        if ':' in time_text:
            try:
                parts = time_text.split(':')
                hours = int(parts[0])
                mins = int(parts[1][:2])
                time_minutes = hours * 60 + mins
            except (ValueError, IndexError):
                pass

        if time_minutes is None:
            continue

        time_str = f"{time_minutes // 60}:{time_minutes % 60:02d}"

        # 各セルをチェック
        for col_idx, cell in enumerate(row_data[1:], start=1):
            # スタッフ名を取得
            staff_idx = col_idx
            if staff_idx >= len(staff_names):
                continue
            staff_name = staff_names[staff_idx].get('name', '').strip()
            if not staff_name:
                continue

            cell_text = cell.get('text', '').strip()
            bg_color = cell.get('bgColor', '')

            # 空き枠判定: 黄色背景 + テキストなし
            is_yellow = _is_yellow_background(bg_color)
            is_empty_text = len(cell_text) == 0

            if is_yellow and is_empty_text:
                if staff_name not in chair_slots:
                    chair_slots[staff_name] = []
                chair_slots[staff_name].append(time_minutes)

        # 最初の3行だけDIAG
        if row_idx < 3:
            for col_idx, cell in enumerate(row_data[:5]):
                logger.info(f"  [DIAG] row={row_idx} col={col_idx}: "
                            f"text='{cell.get('text', '')[:15]}' "
                            f"bg='{cell.get('bgColor', '')}' "
                            f"class='{cell.get('classList', '')}'")

    # 結果をログ出力
    for staff, slots in sorted(chair_slots.items()):
        times_str = ', '.join(f"{s//60}:{s%60:02d}" for s in sorted(slots)[:10])
        extra = f"... (+{len(slots) - 10})" if len(slots) > 10 else ""
        logger.info(f"  {staff}: {len(slots)}スロット: {times_str}{extra}")

    return chair_slots


def _is_yellow_background(bg_color: str) -> bool:
    """背景色が黄色系かどうかを判定

    RGB値で判定:
    - R > 200, G > 200, B < 100 → 黄色
    - または rgb(255, 255, 0) 系
    """
    if not bg_color:
        return False

    # rgb(R, G, B) or rgba(R, G, B, A) をパース
    bg_color = bg_color.strip().lower()

    if bg_color.startswith('rgb'):
        try:
            # rgb(255, 255, 0) or rgba(255, 255, 0, 1)
            nums = bg_color.replace('rgb', '').replace('a', '').replace('(', '').replace(')', '')
            parts = [int(x.strip()) for x in nums.split(',')[:3]]
            r, g, b = parts[0], parts[1], parts[2]
            # 黄色: R高, G高, B低
            return r > 200 and g > 200 and b < 100
        except (ValueError, IndexError):
            return False

    # CSSカラー名
    if bg_color in ('yellow', '#ffff00', '#ff0', '#ffff33'):
        return True

    # hex形式
    if bg_color.startswith('#'):
        try:
            hex_color = bg_color.lstrip('#')
            if len(hex_color) == 3:
                hex_color = ''.join(c * 2 for c in hex_color)
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            return r > 200 and g > 200 and b < 100
        except (ValueError, IndexError):
            return False

    return False


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

            # カレンダーヘッダーからスタッフ名を取得
            staff_names = await page.evaluate('''() => {
                const tables = document.querySelectorAll('table');
                let scheduleTable = null;
                for (const table of tables) {
                    const text = table.textContent || '';
                    if (text.includes('9:') || text.includes('10:') || text.includes('09:')) {
                        scheduleTable = table;
                        break;
                    }
                }
                if (!scheduleTable) return [];
                const headerRow = scheduleTable.querySelector('tr');
                if (!headerRow) return [];
                const cells = headerRow.querySelectorAll('th, td');
                const names = [];
                for (let i = 1; i < cells.length; i++) {
                    const text = (cells[i].textContent || '').trim();
                    if (text) names.push(text);
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
