"""GMO Reserve スクレイパー

さくら医院歯科（GMO Reserve: reserve.ne.jp）の予約カレンダーから空き枠を取得。
- ログイン → 歯科タブ切替 → 翌日の空き枠検出
- 空き枠判定: 黄色背景 + テキストなし
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any

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

    # rdata（JS予約データ）から空き枠を直接計算
    # GMO Reserveのカレンダーは3テーブル分割+CSS配置のため、DOM解析ではなくJS変数を使用
    slot_data = await page.evaluate('''(tomorrowStr) => {
        const result = {
            staffSlots: {},
            debug: {
                rdataTotal: 0,
                tomorrowCount: 0,
                mmIds: [],
            }
        };

        // rdata から翌日の予約を抽出
        const allRdata = Object.values(window.rdata || {});
        result.debug.rdataTotal = allRdata.length;

        const tomorrowData = allRdata.filter(r => r.r_date === tomorrowStr);
        result.debug.tomorrowCount = tomorrowData.length;

        // mm_id のユニーク値（スタッフ列）
        const uniqueMmIds = [...new Set(tomorrowData.map(r => r.r_mm_id))];
        result.debug.mmIds = uniqueMmIds;

        // mm_id → スタッフ名のマッピングを構築
        // ドロップダウン(select_topmenu_main)の option value = mm_id
        const mmIdToName = {};
        const select = document.getElementById('select_topmenu_main');
        if (select) {
            for (const opt of select.options) {
                const val = parseInt(opt.value);
                const text = (opt.textContent || '').trim();
                if (!isNaN(val) && text) {
                    // 「▼」を除去し、括弧以降を除去してクリーンな名前に
                    const clean = text.replace(/^▼/, '').replace(/\(.*$/, '').replace(/（.*$/, '').trim();
                    mmIdToName[val] = clean;
                }
            }
        }
        // 翌日の全予約を mm_id（スタッフ列）ごとにグループ化
        const occupied = {};  // {mm_id: Set<time_minutes>}
        const uniqueMmIdsFromData = new Set();

        for (const r of tomorrowData) {
            const mmId = r.r_mm_id;
            uniqueMmIdsFromData.add(mmId);

            if (!occupied[mmId]) occupied[mmId] = new Set();
            const timeParts = (r.r_time || '').split(':');
            if (timeParts.length >= 2) {
                const startMin = parseInt(timeParts[0]) * 60 + parseInt(timeParts[1]);
                const duration = r.r_minute || 15;
                // 15分刻みで占有マーク
                for (let t = startMin; t < startMin + duration; t += 15) {
                    occupied[mmId].add(t);
                }
            }
        }

        // 営業時間のスロット生成（calendar_infoのmin_time〜max_time、15分刻み）
        const calInfoTime = window.calendar_info || {};
        let minTime = 540;  // デフォルト 9:00
        let maxTime = 1200; // デフォルト 20:00
        if (calInfoTime.min_time) {
            const parts = calInfoTime.min_time.split(':');
            minTime = parseInt(parts[0]) * 60 + parseInt(parts[1]);
        }
        if (calInfoTime.max_time) {
            const parts = calInfoTime.max_time.split(':');
            maxTime = parseInt(parts[0]) * 60 + parseInt(parts[1]);
        }
        const allSlots = [];
        for (let t = minTime; t < maxTime; t += 15) {
            allSlots.push(t);
        }

        // 各mm_id（スタッフ列）の空きスロットを計算
        for (const mmId of uniqueMmIdsFromData) {
            const occ = occupied[mmId] || new Set();
            const emptySlots = allSlots.filter(t => !occ.has(t));
            const staffName = mmIdToName[mmId] || `mm_${mmId}`;

            if (emptySlots.length > 0 && emptySlots.length < allSlots.length) {
                result.staffSlots[staffName] = emptySlots;
            }
        }

        return result;
    }''', tomorrow_str)

    # デバッグ情報をログ出力
    debug = slot_data.get('debug', {})
    logger.info(f"[{clinic_name}] rdata解析: total={debug.get('rdataTotal', 0)}, "
                f"tomorrow={debug.get('tomorrowCount', 0)}, "
                f"mmIds={debug.get('mmIds', [])}")
    logger.info(f"[{clinic_name}] staffSlots keys: {list(slot_data.get('staffSlots', {}).keys())}")

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


def _is_yellow_inline_style(style: str) -> bool:
    """inline style文字列から黄色背景を判定

    例: 'background-color: #ffff00;' or 'background: yellow;' or 'background-color:rgb(255,255,0)'
    """
    if not style:
        return False
    style = style.lower()
    # 直接的なカラー名
    if 'yellow' in style and ('background' in style):
        return True
    # hex形式
    for hex_val in ['#ffff00', '#ff0', '#ffff33', '#ffd700']:
        if hex_val in style and 'background' in style:
            return True
    # rgb形式をbackground内で探す
    bg_match = re.search(r'background[^;]*rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', style)
    if bg_match:
        r, g, b = int(bg_match.group(1)), int(bg_match.group(2)), int(bg_match.group(3))
        if r > 200 and g > 200 and b < 100:
            return True
    return False


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
