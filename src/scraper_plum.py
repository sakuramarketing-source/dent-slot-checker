"""Plum予約システム スクレイパー

イーアス春日井歯科（plum-link.com）の予約カレンダーから空き枠を取得。
- React/MUI SPAベース（#/books ハッシュルーティング）
- ログイン: username + password + deviceName
- 空き枠判定: position:absolute の予約ブロックが無い時間帯 = 空き
- 時間軸: 30分刻みラベル（09:30〜19:30）
- スタッフ列: ヘッダーに色付きDIVでスタッフ名表示
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))


async def login_plum(page, url: str, login_id: str, password: str,
                     device_name: str, clinic_name: str) -> bool:
    """Plumにログイン"""
    logger.info(f"[{clinic_name}] ログイン開始: {url}")
    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
    await page.wait_for_timeout(3000)

    try:
        await page.locator('input[name="username"]').fill(login_id)
        await page.locator('input[name="password"]').fill(password)

        device_input = page.locator('input[name="deviceName"]')
        if await device_input.count() > 0:
            await device_input.fill(device_name)

        await page.locator('button[type="submit"]').click()
        await page.wait_for_timeout(5000)

        if '#/books' in page.url:
            logger.info(f"[{clinic_name}] ログイン成功")
            return True
        else:
            logger.error(f"[{clinic_name}] ログイン失敗: URL={page.url}")
            return False
    except Exception as e:
        logger.error(f"[{clinic_name}] ログインエラー: {e}")
        return False


async def navigate_to_tomorrow_plum(page, clinic_name: str) -> bool:
    """翌日に移動"""
    try:
        tomorrow = datetime.now(JST) + timedelta(days=1)
        tomorrow_str = f"{tomorrow.year}年{tomorrow.month:02d}月{tomorrow.day:02d}日"
        logger.info(f"[{clinic_name}] 翌日へ移動: {tomorrow_str}")

        # 日付ヘッダー横の>ボタン（MuiIconButton-sizeSmall）
        # 左から: ハンバーガーメニュー, <ボタン, >ボタン の順
        nav_buttons = page.locator('.MuiIconButton-root.MuiIconButton-sizeSmall')
        count = await nav_buttons.count()
        logger.info(f"[{clinic_name}] ナビボタン数: {count}")

        # 全ボタンをクリックして試す（小さいアイコンボタンの2番目が>）
        clicked = False
        for i in range(min(count, 4)):
            try:
                btn = nav_buttons.nth(i)
                box = await btn.bounding_box()
                if box and box['x'] > 30 and box['x'] < 100 and box['y'] < 50:
                    # ヘッダー内の2番目のボタン（>）
                    await btn.click()
                    await page.wait_for_timeout(3000)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            # カレンダーの翌日をクリック
            tomorrow_day = str(tomorrow.day)
            cal_day = page.locator(f'div:text-is("{tomorrow_day}")').first
            if await cal_day.count() > 0:
                await cal_day.click()
                await page.wait_for_timeout(3000)
                clicked = True

        # 日付確認
        date_text = await page.locator('p.MuiTypography-root').first.inner_text()
        logger.info(f"[{clinic_name}] 現在の日付: {date_text}")

        if tomorrow_str in date_text:
            return True

        # まだ翌日でない場合、左サイドカレンダーで翌日の数字をクリック
        logger.info(f"[{clinic_name}] ナビボタン失敗、カレンダーから翌日をクリック")
        tomorrow_day = str(tomorrow.day)
        # サイドメニュー内のカレンダー日付
        day_cells = await page.locator('.sidemenu div').all()
        for cell in day_cells:
            try:
                text = await cell.inner_text()
                if text.strip() == tomorrow_day:
                    box = await cell.bounding_box()
                    if box and box['x'] < 320:  # サイドメニュー内
                        await cell.click()
                        await page.wait_for_timeout(3000)
                        break
            except Exception:
                continue

        date_text = await page.locator('p.MuiTypography-root').first.inner_text()
        logger.info(f"[{clinic_name}] 最終日付: {date_text}")
        return tomorrow_str in date_text

    except Exception as e:
        logger.error(f"[{clinic_name}] 翌日移動エラー: {e}")
        return False


async def get_plum_empty_slots(page, clinic_name: str) -> Dict[str, List[int]]:
    """
    Plumカレンダーから空きスロットを検出

    アプローチ:
    1. ヘッダー行からスタッフ列（名前 + x座標）を取得
    2. カレンダーグリッド内の予約ブロック（色付きDIV）を全取得
    3. 各スタッフ列で予約ブロックが無い時間帯 = 空き枠
    """
    try:
        result = await page.evaluate('''() => {
            // === ステップ1: スタッフ列ヘッダーを取得 ===
            // ヘッダー行: 色付きDIVでスタッフ名が表示（y座標が小さい、幅165px付近）
            const headerDivs = [];
            const allDivs = document.querySelectorAll('div');
            for (const div of allDivs) {
                const rect = div.getBoundingClientRect();
                const style = getComputedStyle(div);
                const bg = style.backgroundColor;
                // ヘッダー行の条件: y=70-95付近、幅150-180px、高さ10-20px、色付き
                if (rect.y > 60 && rect.y < 100 && rect.width > 140 && rect.width < 200 &&
                    rect.height > 8 && rect.height < 25 &&
                    bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'rgb(255, 255, 255)' &&
                    bg !== 'rgb(240, 246, 255)') {
                    const text = div.textContent.trim();
                    if (text && text.length > 0 && text.length < 30) {
                        headerDivs.push({
                            name: text,
                            x: Math.round(rect.x),
                            width: Math.round(rect.width),
                            bg: bg
                        });
                    }
                }
            }

            // === ステップ2: 時間軸の位置マッピング ===
            // position:absoluteの時間ラベル（09:30, 10:00, ...）
            const timeLabels = [];
            for (const div of allDivs) {
                const text = div.textContent.trim();
                if (text.match(/^\\d{2}:\\d{2}$/) && div.children.length === 0) {
                    const style = getComputedStyle(div);
                    if (style.position === 'absolute') {
                        const rect = div.getBoundingClientRect();
                        const [h, m] = text.split(':').map(Number);
                        timeLabels.push({
                            time: text,
                            minutes: h * 60 + m,
                            y: rect.y
                        });
                    }
                }
            }
            timeLabels.sort((a, b) => a.y - b.y);

            // === ステップ3: 予約ブロックを全取得 ===
            // Plumの予約ブロック: position:staticの色付きDIV（カレンダーグリッド内）
            const calendarTop = timeLabels.length > 0 ? timeLabels[0].y - 20 : 90;
            const blocks = [];
            for (const div of allDivs) {
                const rect = div.getBoundingClientRect();
                if (rect.width < 50 || rect.height < 8) continue;
                if (rect.x < 360) continue; // 時間ラベル+サイドメニューをスキップ
                if (rect.y < calendarTop) continue; // ヘッダーより上をスキップ

                const style = getComputedStyle(div);
                const bg = style.backgroundColor;
                if (!bg || bg === 'rgba(0, 0, 0, 0)' || bg === 'rgb(255, 255, 255)') continue;

                // 予約ブロックの色パターン
                // rgb(150,150,150) = 通常予約（グレー）
                // rgb(245,127,23) = 要注意予約（オレンジ）
                // rgb(240,246,255) = メモ/重要/DA枠
                // rgb(255,205,210) = ピンク系
                // その他の色付き = 予約あり
                const match = bg.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
                if (!match) continue;
                const [_, r, g, b] = match.map(Number);

                // 白に近い色はスキップ
                if (r > 248 && g > 248 && b > 248) continue;
                // 背景グレー（f6f6f6等）はスキップ
                if (r === g && g === b && r > 240) continue;

                blocks.push({
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                    bg: bg,
                    text: div.textContent.trim().substring(0, 30)
                });
            }

            return { headers: headerDivs, timeLabels, blocks };
        }''')

        headers = result['headers']
        time_labels = result['timeLabels']
        blocks = result['blocks']

        logger.info(f"[{clinic_name}] スタッフ列: {len(headers)}名, "
                    f"時間ラベル: {len(time_labels)}個, 予約ブロック: {len(blocks)}個")

        if not headers or not time_labels:
            logger.warning(f"[{clinic_name}] ヘッダーまたは時間ラベルが取得できません")
            return {}

        for h in headers:
            logger.info(f"  スタッフ: {h['name']} (x={h['x']})")

        # === ステップ4: 各スタッフ列の空き枠を計算 ===
        staff_slots = {}

        # 時間ラベルからy座標→時間(分)のマッピングを作成
        # 15分間隔でチェック（30分ラベル間を2分割）
        check_interval = 15  # 分
        time_points = []
        for i in range(len(time_labels) - 1):
            t1 = time_labels[i]
            t2 = time_labels[i + 1]
            # t1の時刻から t2の時刻の直前まで、15分刻みで追加
            minutes = t1['minutes']
            while minutes < t2['minutes']:
                # y座標を線形補間
                ratio = (minutes - t1['minutes']) / (t2['minutes'] - t1['minutes'])
                y = t1['y'] + ratio * (t2['y'] - t1['y'])
                time_points.append({'minutes': minutes, 'y': y})
                minutes += check_interval

        for header in headers:
            staff_name = header['name']
            col_x = header['x']
            col_width = header['width']
            empty_slots = []

            for tp in time_points:
                check_y = tp['y']
                check_minutes = tp['minutes']

                # この時間帯・列に予約ブロックが被っているか
                is_booked = False
                for block in blocks:
                    # 列の重なり判定（x座標）
                    block_right = block['x'] + block['width']
                    col_right = col_x + col_width
                    x_overlap = block['x'] < col_right and block_right > col_x

                    # 時間の重なり判定（y座標、チェックポイントがブロック内か）
                    block_bottom = block['y'] + block['height']
                    y_overlap = check_y >= block['y'] - 2 and check_y < block_bottom - 2

                    if x_overlap and y_overlap:
                        is_booked = True
                        break

                if not is_booked:
                    empty_slots.append(check_minutes)

            staff_slots[staff_name] = empty_slots
            if empty_slots:
                times_str = [f"{m//60}:{m%60:02d}" for m in empty_slots[:5]]
                logger.info(f"  {staff_name}: {len(empty_slots)}スロット空き "
                           f"(例: {', '.join(times_str)}...)")
            else:
                logger.info(f"  {staff_name}: 空きなし")

        return staff_slots

    except Exception as e:
        logger.error(f"[{clinic_name}] 空き枠検出エラー: {e}")
        import traceback
        traceback.print_exc()
        return {}


async def scrape_plum_clinic(browser, clinic: dict) -> Optional[Dict[str, List[int]]]:
    """Plum分院1つをスクレイピング"""
    clinic_name = clinic['name']
    page = await browser.new_page(viewport={'width': 1920, 'height': 1080})
    try:
        if not await login_plum(
            page, clinic['url'], clinic['id'], clinic['password'],
            clinic.get('device_name', '瀧田セカンドPC'), clinic_name
        ):
            return None

        if not await navigate_to_tomorrow_plum(page, clinic_name):
            logger.warning(f"[{clinic_name}] 翌日移動失敗、当日のデータで続行")

        slots = await get_plum_empty_slots(page, clinic_name)
        return slots
    except Exception as e:
        logger.error(f"[{clinic_name}] スクレイピングエラー: {e}")
        return None
    finally:
        await page.close()


async def scrape_all_plum_clinics(
    clinics: list,
    headless: bool = True,
    browser=None
) -> Dict[str, Dict[str, List[int]]]:
    """全Plum分院をスクレイピング"""
    from playwright.async_api import async_playwright
    import asyncio

    results = {}
    own_browser = browser is None

    if own_browser:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=headless,
            args=['--disable-dev-shm-usage', '--no-sandbox']
        )

    for clinic in clinics:
        if not clinic.get('enabled', True):
            continue
        logger.info(f"Plum スクレイピング開始: {clinic['name']}")
        slots = await scrape_plum_clinic(browser, clinic)
        results[clinic['name']] = slots or {}

    if own_browser:
        await browser.close()
        await pw.stop()

    return results
