"""paylight X 予約システム スクレイパー

さくら歯科（clinic.pay-light.com）の予約カレンダーから空き枠を取得。
- Keycloak OpenID Connect 認証
- React SPA ベースの予約グリッド（15分スロット）
- 空き枠判定: 予約ブロック（色付きDIV）が覆っていない時間帯 = 空き
- 時間軸: 30分刻みラベル
- スタッフ列: ヘッダー行にスタッフ名表示
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


async def login_pay_light(page, url: str, login_id: str, password: str,
                          clinic_name: str) -> bool:
    """paylight X に Keycloak 経由でログイン"""
    logger.info(f"[{clinic_name}] ログイン開始: {url}")
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await page.wait_for_timeout(3000)

        # Keycloak にリダイレクトされているはず
        if 'auth.pay-light.com' not in page.url:
            logger.warning(f"[{clinic_name}] Keycloak 未リダイレクト: {page.url}")

        # メールアドレス入力
        for sel in ('#username', 'input[name="username"]', 'input[type="email"]'):
            try:
                if await page.locator(sel).count() > 0:
                    await page.locator(sel).fill(login_id)
                    break
            except Exception:
                continue

        # パスワード入力
        for sel in ('#password', 'input[name="password"]', 'input[type="password"]'):
            try:
                if await page.locator(sel).count() > 0:
                    await page.locator(sel).fill(password)
                    break
            except Exception:
                continue

        # ログインボタン
        for sel in ('#kc-login', 'button[type="submit"]', 'input[type="submit"]'):
            try:
                if await page.locator(sel).count() > 0:
                    await page.locator(sel).click()
                    break
            except Exception:
                continue

        # リダイレクト待機
        await page.wait_for_url('**/clinic.pay-light.com/**', timeout=20000)
        await page.wait_for_timeout(4000)

        if 'clinic.pay-light.com' in page.url:
            logger.info(f"[{clinic_name}] ログイン成功: {page.url}")
            return True
        else:
            logger.error(f"[{clinic_name}] ログイン失敗: {page.url}")
            return False

    except Exception as e:
        logger.error(f"[{clinic_name}] ログインエラー: {e}")
        return False


async def navigate_to_tomorrow_pay_light(page, clinic_name: str) -> bool:
    """翌日に移動（日表示の > ボタン）"""
    try:
        tomorrow = datetime.now(JST) + timedelta(days=1)
        tomorrow_str = f"{tomorrow.month}月{tomorrow.day}日"
        logger.info(f"[{clinic_name}] 翌日へ移動: {tomorrow_str}")

        # 「日」ビューに切り替え（既に日表示のこともあるが念のため）
        day_btn = page.locator('button:has-text("日")').first
        if await day_btn.count() > 0:
            try:
                await day_btn.click()
                await page.wait_for_timeout(1000)
            except Exception:
                pass

        # > ボタン（翌日へのナビゲーション）
        # ChevronRight アイコンボタン、または > テキスト含むボタンを探す
        clicked = False

        # 方法1: aria-label で探す
        for label in ('翌日', 'next', 'forward', '次', '>'):
            btn = page.locator(f'button[aria-label*="{label}"]').first
            if await btn.count() > 0:
                await btn.click()
                await page.wait_for_timeout(2000)
                clicked = True
                break

        # 方法2: SVGアイコン（ChevronRight）を持つボタン
        if not clicked:
            btn = page.locator('button:has(svg[data-testid="ChevronRightIcon"])').first
            if await btn.count() > 0:
                await btn.click()
                await page.wait_for_timeout(2000)
                clicked = True

        # 方法3: ページ上部のボタンを位置で特定（<  今日  > の右側）
        if not clicked:
            buttons = page.locator('button')
            count = await buttons.count()
            header_btns = []
            for i in range(min(count, 30)):
                try:
                    btn = buttons.nth(i)
                    box = await btn.bounding_box()
                    if box and box['y'] < 60 and box['x'] > 300:
                        header_btns.append((box['x'], i))
                except Exception:
                    continue

            if header_btns:
                header_btns.sort()
                # 最も右にあるボタンが > ボタン
                _, idx = header_btns[-1]
                await buttons.nth(idx).click()
                await page.wait_for_timeout(2000)
                clicked = True

        if not clicked:
            logger.warning(f"[{clinic_name}] > ボタンが見つかりません")
            return False

        logger.info(f"[{clinic_name}] 翌日移動完了")
        return True

    except Exception as e:
        logger.error(f"[{clinic_name}] 翌日移動エラー: {e}")
        return False


async def get_pay_light_empty_slots(page, clinic_name: str,
                                    slot_interval: int = 15,
                                    start_hour: int = 9,
                                    end_hour: int = 19) -> Dict[str, List[int]]:
    """
    paylight X カレンダーから空きスロットを検出

    アプローチ（Plum と同様の座標ベース検出）:
    1. スタッフ列ヘッダー（x座標・幅）を取得
    2. 時間軸ラベル（y座標）から時間→pixel位置マッピングを構築
    3. 予約ブロック（色付きDIV）を全取得（白・透明を除外）
    4. 各スタッフ列 × 時間ポイントで予約ブロックの被覆を判定
    5. 被覆なし = 空き枠
    """
    try:
        # React SPA のレンダリング完了を待機
        await page.wait_for_timeout(5000)

        # 予約ブロック数が安定するまでポーリング（最大25秒）
        prev_count = 0
        stable = 0
        for i in range(25):
            count = await page.evaluate('''() => {
                let n = 0;
                for (const el of document.querySelectorAll('div')) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 30 || r.height < 10 || r.y < 100) continue;
                    const bg = getComputedStyle(el).backgroundColor;
                    if (!bg || bg === 'rgba(0, 0, 0, 0)' || bg === 'rgb(255, 255, 255)') continue;
                    n++;
                }
                return n;
            }''')
            if count >= 5 and count == prev_count:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
            prev_count = count
            await page.wait_for_timeout(1000)

        logger.info(f"[{clinic_name}] レンダリング待機: {prev_count}要素 (ループ{i+1}回)")

        result = await page.evaluate('''() => {
            const allEls = Array.from(document.querySelectorAll('div, span'));

            // === ステップ1: スタッフ列ヘッダーを取得 ===
            // ページ上部（y=60-150）に水平に並ぶカラムヘッダー
            // スタッフ名テキストを含み、十分な幅の要素
            const headerCandidates = [];
            for (const el of allEls) {
                const rect = el.getBoundingClientRect();
                const text = el.textContent.trim().split('\\n')[0].trim();
                if (rect.y < 60 || rect.y > 150) continue;
                if (rect.width < 50 || rect.width > 250 || rect.height < 15 || rect.height > 60) continue;
                if (!text || text.length === 0 || text.length > 50) continue;
                // 時間ラベル（HH:MM）は除外
                if (text.match(/^\\d{1,2}:\\d{2}$/)) continue;
                // 「今日」「日」「月」などUI要素は除外
                if (['今日', '日', '月', '週', '月表示', '日表示'].includes(text)) continue;
                if (text.match(/^\\d{4}年/)) continue;  // 日付ヘッダーは除外

                headerCandidates.push({
                    name: text,
                    x: Math.round(rect.x),
                    width: Math.round(rect.width),
                    y: Math.round(rect.y)
                });
            }

            // x座標でソートし、重複除去（20px以内は同一とみなす）
            headerCandidates.sort((a, b) => a.x - b.x);
            const headers = [];
            let lastX = -999;
            for (const h of headerCandidates) {
                if (h.x - lastX > 20) {
                    headers.push(h);
                    lastX = h.x;
                }
            }

            // === ステップ2: 時間軸ラベル（y座標）を取得 ===
            // 左側固定の「H:MM」または「HH:MM」形式ラベル
            const timeLabels = [];
            const seenTimes = new Set();
            const allTimeEls = Array.from(document.querySelectorAll('div, span, p, li, td, label'));

            // デバッグ: 時刻らしいテキストを持つ全要素を収集（ログ用）
            const debugTimeCandidates = [];
            for (const el of allTimeEls) {
                const text = el.textContent.trim();
                if (text.match(/\d{1,2}:\d{2}/) && text.length < 20) {
                    const rect = el.getBoundingClientRect();
                    if (debugTimeCandidates.length < 10) {
                        debugTimeCandidates.push({t: text, x: Math.round(rect.x), y: Math.round(rect.y), tag: el.tagName});
                    }
                }
            }

            for (const el of allTimeEls) {
                const text = el.textContent.trim();
                // HH:MM または HH:MM AM/PM 形式（全幅を含む）
                const m = text.match(/^(\d{1,2}:\d{2})(\s*(AM|PM|am|pm))?$/);
                if (!m) continue;
                const timeStr = m[1];
                const rect = el.getBoundingClientRect();
                if (rect.y < 100) continue;
                if (seenTimes.has(timeStr)) continue;
                seenTimes.add(timeStr);
                const parts = timeStr.split(':').map(Number);
                let minutes = parts[0] * 60 + parts[1];
                // PM補正（12時以外）
                if (m[3] && m[3].toUpperCase() === 'PM' && parts[0] !== 12) minutes += 720;
                timeLabels.push({
                    time: timeStr,
                    minutes: minutes,
                    y: rect.y + rect.height / 2
                });
            }
            timeLabels.sort((a, b) => a.y - b.y);

            // === ステップ3: 予約ブロック（色付きDIV）を取得 ===
            const minX = headers.length > 0 ? Math.min(...headers.map(h => h.x)) - 20 : 80;
            const minY = timeLabels.length > 0 ? timeLabels[0].y - 40 : 100;

            const blocks = [];
            for (const el of allEls) {
                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 10) continue;
                if (rect.x < minX || rect.y < minY) continue;

                const style = getComputedStyle(el);
                const bg = style.backgroundColor;
                if (!bg || bg === 'rgba(0, 0, 0, 0)' || bg === 'rgb(255, 255, 255)') continue;

                const m = bg.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                if (!m) continue;
                const [, r, g, b] = m.map(Number);

                // 白・薄いグレー・背景色をスキップ
                if (r > 245 && g > 245 && b > 245) continue;
                if (Math.abs(r - g) < 10 && Math.abs(g - b) < 10 && r > 230) continue;

                blocks.push({
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                    bg: bg,
                    text: el.textContent.trim().substring(0, 40)
                });
            }

            return { headers, timeLabels, blocks, debugTimeCandidates };
        }''')

        headers = result['headers']
        time_labels = result['timeLabels']
        blocks = result['blocks']
        debug_time = result.get('debugTimeCandidates', [])

        logger.info(f"[{clinic_name}] スタッフ列: {len(headers)}名, "
                    f"時間ラベル: {len(time_labels)}個, 予約ブロック: {len(blocks)}個")
        for h in headers[:8]:
            logger.info(f"  ヘッダー: '{h['name']}' x={h['x']} w={h['width']}")
        for t in time_labels[:6]:
            logger.info(f"  時間: {t['time']} y={t['y']:.0f}")
        if len(time_labels) == 0 and debug_time:
            logger.warning(f"[{clinic_name}] 時間候補(デバッグ): {debug_time}")
        elif len(time_labels) == 0:
            logger.warning(f"[{clinic_name}] 時間候補なし（DOM上に時刻テキスト要素が見つからない）")

        if not headers or not time_labels:
            logger.warning(f"[{clinic_name}] ヘッダーまたは時間ラベルが取得できません "
                           f"(headers={len(headers)}, timeLabels={len(time_labels)})")
            return {}

        # === ステップ4: 各スタッフ列の空き枠を計算 ===
        # 時間ラベル間を補間して全チェックポイント（y座標）を生成
        time_points = []
        start_min = start_hour * 60
        end_min = end_hour * 60

        for i in range(len(time_labels) - 1):
            t1 = time_labels[i]
            t2 = time_labels[i + 1]
            minutes = max(t1['minutes'], start_min)
            while minutes < t2['minutes'] and minutes < end_min:
                ratio = (minutes - t1['minutes']) / (t2['minutes'] - t1['minutes'])
                y = t1['y'] + ratio * (t2['y'] - t1['y'])
                if minutes >= start_min:
                    time_points.append({'minutes': minutes, 'y': y})
                minutes += slot_interval

        # 最後の時間ラベル以降を外挿
        if time_labels and len(time_labels) >= 2:
            last_t = time_labels[-1]
            dt = time_labels[-1]['minutes'] - time_labels[-2]['minutes']
            dy = time_labels[-1]['y'] - time_labels[-2]['y']
            minutes = last_t['minutes']
            while minutes < end_min:
                if dt > 0:
                    ratio = (minutes - last_t['minutes']) / dt
                    y = last_t['y'] + ratio * dy
                else:
                    y = last_t['y']
                if minutes >= start_min:
                    time_points.append({'minutes': minutes, 'y': y})
                minutes += slot_interval

        logger.info(f"[{clinic_name}] チェックポイント: {len(time_points)}個 "
                    f"({start_hour}:00-{end_hour}:00, {slot_interval}分間隔)")

        staff_slots = {}
        for header in headers:
            staff_name = header['name']
            col_x = header['x']
            col_width = header['width']
            empty_slots = []

            for tp in time_points:
                check_y = tp['y']
                is_booked = False

                for block in blocks:
                    block_right = block['x'] + block['width']
                    col_right = col_x + col_width
                    x_overlap = block['x'] < col_right and block_right > col_x
                    # 上下8px の許容幅（ピクセルギャップ吸収）
                    y_overlap = check_y >= block['y'] - 8 and check_y < block['y'] + block['height'] + 8
                    if x_overlap and y_overlap:
                        is_booked = True
                        break

                if not is_booked:
                    empty_slots.append(tp['minutes'])

            # 孤立した偽空きスロットを除去
            if empty_slots and len(empty_slots) < len(time_points):
                booked_set = {tp['minutes'] for tp in time_points} - set(empty_slots)
                filtered = []
                for slot in empty_slots:
                    has_before = any(b < slot and slot - b <= 30 for b in booked_set)
                    has_after = any(b > slot and b - slot <= 30 for b in booked_set)
                    if has_before and has_after:
                        continue
                    filtered.append(slot)
                empty_slots = filtered

            staff_slots[staff_name] = empty_slots
            if empty_slots:
                sample = [f"{m//60}:{m%60:02d}" for m in empty_slots[:4]]
                logger.info(f"  {staff_name}: 空き={len(empty_slots)}/{len(time_points)} "
                            f"(例: {', '.join(sample)})")
            else:
                logger.info(f"  {staff_name}: 空きなし")

        return staff_slots

    except Exception as e:
        logger.error(f"[{clinic_name}] 空き枠検出エラー: {e}")
        import traceback
        traceback.print_exc()
        return {}


async def get_pay_light_staff_names(page, clinic_name: str) -> List[str]:
    """スタッフ同期用: ヘッダー行からスタッフ名一覧を取得"""
    try:
        result = await page.evaluate('''() => {
            const names = [];
            const seen = new Set();
            for (const el of document.querySelectorAll('div, span')) {
                const rect = el.getBoundingClientRect();
                const text = el.textContent.trim().split('\\n')[0].trim();
                if (rect.y < 60 || rect.y > 150) continue;
                if (rect.width < 50 || !text || text.length > 50) continue;
                if (text.match(/^\\d{1,2}:\\d{2}$/) || text.match(/^\\d{4}年/)) continue;
                if (['今日', '日', '月', '週'].includes(text)) continue;
                if (!seen.has(text)) {
                    seen.add(text);
                    names.push(text);
                }
            }
            return names;
        }''')
        return [n for n in result if n]
    except Exception as e:
        logger.error(f"[{clinic_name}] スタッフ名取得エラー: {e}")
        return []


async def scrape_pay_light_clinic(browser, clinic: dict,
                                   start_hour: int = 9,
                                   end_hour: int = 19) -> Optional[Dict[str, List[int]]]:
    """paylight X 分院1つをスクレイピング"""
    clinic_name = clinic['name']
    page = await browser.new_page(viewport={'width': 1920, 'height': 1080})

    console_errors = []
    page.on('console', lambda msg: console_errors.append(
        f"{msg.type}: {msg.text}") if msg.type in ('error', 'warning') else None)

    try:
        store_url = clinic.get('url', '')
        login_id = clinic.get('id', '')
        password = clinic.get('password', '')

        if not await login_pay_light(page, store_url, login_id, password, clinic_name):
            return None

        if not await navigate_to_tomorrow_pay_light(page, clinic_name):
            logger.warning(f"[{clinic_name}] 翌日移動失敗、当日データで続行")

        slots = await get_pay_light_empty_slots(
            page, clinic_name,
            slot_interval=15,
            start_hour=start_hour,
            end_hour=end_hour
        )

        if console_errors:
            logger.debug(f"[{clinic_name}] コンソール警告: {console_errors[:3]}")

        return slots if slots else None

    except Exception as e:
        logger.error(f"[{clinic_name}] スクレイピングエラー: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        await page.close()


async def scrape_all_pay_light_clinics(clinics: list, headless: bool = True,
                                        browser=None) -> Dict[str, Dict[str, List[int]]]:
    """全 paylight 分院をスクレイピング"""
    from playwright.async_api import async_playwright

    results = {}
    own_playwright = browser is None

    p = None
    try:
        if own_playwright:
            p = await async_playwright().start()
            browser = await p.chromium.launch(
                headless=headless,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
            )

        for clinic in clinics:
            if not clinic.get('enabled', True):
                continue
            try:
                clinic_results = await scrape_pay_light_clinic(browser, clinic) or {}
                results[clinic['name']] = clinic_results
                if clinic_results:
                    logger.info(f"[{clinic['name']}] 完了: {len(clinic_results)}名分")
                else:
                    logger.warning(f"[{clinic['name']}] 結果なし（0枠）")
            except Exception as e:
                logger.error(f"[{clinic['name']}] エラー: {e}")
                results[clinic['name']] = {}

        return results

    finally:
        if own_playwright:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            if p:
                try:
                    await p.stop()
                except Exception:
                    pass


async def sync_pay_light_staff(clinics: list, headless: bool = True) -> Dict[str, List[str]]:
    """スタッフ同期用: paylight 分院のスタッフ名一覧を取得"""
    from playwright.async_api import async_playwright

    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        try:
            for clinic in clinics:
                if not clinic.get('enabled', True):
                    continue
                page = await browser.new_page(viewport={'width': 1920, 'height': 1080})
                try:
                    clinic_name = clinic['name']
                    if await login_pay_light(
                        page, clinic['url'], clinic['id'], clinic['password'], clinic_name
                    ):
                        staff_names = await get_pay_light_staff_names(page, clinic_name)
                        results[clinic_name] = staff_names
                        logger.info(f"[{clinic_name}] スタッフ同期: {len(staff_names)}名")
                except Exception as e:
                    logger.error(f"[{clinic.get('name', '?')}] スタッフ同期エラー: {e}")
                finally:
                    await page.close()
        finally:
            await browser.close()

    return results
