"""Stransa (Apotool & Box) スクレイピングモジュール"""

import asyncio
import logging
import os
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page, Browser

logger = logging.getLogger(__name__)

# デバッグスクリーンショット保存先
_SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs', 'screenshots')


async def _debug_screenshot(page: Page, clinic_name: str, step: str):
    """デバッグ用スクリーンショットを保存"""
    try:
        os.makedirs(_SCREENSHOT_DIR, exist_ok=True)
        safe_name = clinic_name.replace('/', '_').replace(' ', '_').replace('（', '').replace('）', '')[:20]
        path = os.path.join(_SCREENSHOT_DIR, f'{safe_name}_{step}.png')
        await page.screenshot(path=path, full_page=False)
        logger.info(f"[DEBUG] screenshot saved: {step} - {clinic_name}")
    except Exception as e:
        logger.warning(f"screenshot失敗: {e}")


async def login_stransa(page: Page, clinic: Dict[str, str]) -> bool:
    """
    Stransa サイトにログイン

    Args:
        page: Playwright Page オブジェクト
        clinic: 分院設定 (url, id, password)

    Returns:
        ログイン成功したかどうか
    """
    clinic_name = clinic.get('name', '不明')
    try:
        logger.info(f"[{clinic_name}] ログイン開始: {clinic['url']}")
        await page.goto(clinic['url'], wait_until='commit', timeout=60000)
        # ログインフォームが描画されるまで待つ
        try:
            await page.wait_for_selector('input[type="password"]', timeout=30000)
        except Exception:
            await asyncio.sleep(5)
        logger.info(f"[{clinic_name}] ページ読み込み完了: {page.url}")

        # メールアドレス入力
        email_input = page.locator('input[type="text"], input[type="email"]').first
        if await email_input.count() > 0:
            await email_input.fill(clinic['id'])

        # パスワード入力
        pass_input = page.locator('input[type="password"]').first
        if await pass_input.count() > 0:
            await pass_input.fill(clinic['password'])

        # ログインボタンをクリック
        login_btn = page.locator('button[type="submit"], button:has-text("ログイン")').first
        if await login_btn.count() > 0:
            await login_btn.click()
            # ログイン後のページ遷移を待つ
            try:
                await page.wait_for_url('**/office**', timeout=15000)
            except Exception:
                try:
                    await page.wait_for_url('**/calendar/**', timeout=15000)
                except Exception:
                    await asyncio.sleep(3)

        current_url = page.url
        logger.info(f"[{clinic_name}] ログイン後URL: {current_url}")
        await _debug_screenshot(page, clinic_name, '01_after_login')

        # オフィス選択ページ (/office) の場合
        if '/office' in current_url:
            logger.info(f"[{clinic_name}] オフィス選択ページ検出")

            # ページ上のオフィスリンクを全取得してログ
            all_links = await page.locator('a').all()
            office_names = []
            for link in all_links:
                try:
                    text = (await link.inner_text()).strip()
                    if text and len(text) > 1 and len(text) < 50:
                        office_names.append(text)
                except Exception:
                    pass
            logger.info(f"[{clinic_name}] オフィス一覧: {office_names}")

            # 部分一致でオフィスを探す
            found = False
            # まず完全一致
            office_link = page.locator(f'a:has-text("{clinic_name}")')
            if await office_link.count() > 0:
                await office_link.first.click()
                found = True
                logger.info(f"[{clinic_name}] オフィス完全一致でクリック")
            else:
                # 部分一致: clinic_nameの主要部分で検索
                short_name = clinic_name.split('・')[0].replace('（歯科）', '').replace('（', '').replace('）', '')
                office_link2 = page.locator(f'a:has-text("{short_name}")')
                if await office_link2.count() > 0:
                    await office_link2.first.click()
                    found = True
                    logger.info(f"[{clinic_name}] オフィス部分一致({short_name})でクリック")

            if found:
                await asyncio.sleep(3)
            else:
                logger.warning(f"[{clinic_name}] オフィスが見つからない、URL置換でカレンダーへ")
                calendar_url = current_url.replace('/office', '/calendar/')
                await page.goto(calendar_url, wait_until='commit', timeout=60000)
                await asyncio.sleep(3)

            current_url = page.url
            logger.info(f"[{clinic_name}] オフィス選択後URL: {current_url}")
            await _debug_screenshot(page, clinic_name, '02_after_office')

            # まだofficeページの場合はリトライ
            if '/office' in current_url:
                logger.warning(f"[{clinic_name}] まだofficeページ、URL置換でリトライ")
                calendar_url = current_url.replace('/office', '/calendar/')
                await page.goto(calendar_url, wait_until='commit', timeout=60000)
                await asyncio.sleep(3)
                current_url = page.url

        if '/calendar/' in current_url:
            # SPAのカレンダー描画を待つ
            try:
                await page.wait_for_selector('table', timeout=30000)
            except Exception:
                await asyncio.sleep(5)

            await _debug_screenshot(page, clinic_name, '03_calendar_before_tab')

            # 「スタッフ」タブに切り替え（複数セレクタパターン）
            staff_switched = False
            staff_selectors = [
                'text="スタッフ"',
                'button:has-text("スタッフ")',
                'a:has-text("スタッフ")',
                'span:has-text("スタッフ")',
                'label:has-text("スタッフ")',
                'div:has-text("スタッフ")',
                'input[value="スタッフ"]',
            ]
            for sel in staff_selectors:
                try:
                    btn = page.locator(sel)
                    count = await btn.count()
                    if count > 0:
                        # 表示されている要素のみクリック
                        for i in range(min(count, 3)):
                            el = btn.nth(i)
                            if await el.is_visible():
                                await el.click()
                                await asyncio.sleep(2)
                                staff_switched = True
                                logger.info(f"[{clinic_name}] スタッフタブ切替成功: {sel}")
                                break
                    if staff_switched:
                        break
                except Exception:
                    continue

            if not staff_switched:
                logger.warning(f"[{clinic_name}] スタッフタブが見つかりません（全セレクタ試行済み）")

            await _debug_screenshot(page, clinic_name, '04_after_staff_tab')
            logger.info(f"[{clinic_name}] ログイン成功（URL: {page.url}）")
            return True
        else:
            logger.warning(f"[{clinic_name}] ログイン後のURLが想定外: {current_url}")
            await _debug_screenshot(page, clinic_name, '99_unexpected_url')
            return True

    except Exception as e:
        logger.error(f"[{clinic_name}] Stransa ログイン失敗: {e}")
        await _debug_screenshot(page, clinic_name, '99_error')
        return False


async def navigate_to_tomorrow_stransa(page: Page) -> bool:
    """
    翌日の予約表に移動

    シンプルに「本日」→「>」で翌日へ移動
    ナビゲーションボタン:
    - « = 前月
    - < = 前日
    - 本日 = 今日
    - > = 翌日
    - » = 次月

    Returns:
        移動成功したかどうか
    """
    try:
        tomorrow = datetime.now() + timedelta(days=1)
        logger.info(f"翌日へ移動: {tomorrow.year}年{tomorrow.month}月{tomorrow.day}日")

        # Step 1: 「本日」ボタンをクリックして今日に移動
        today_clicked = False
        today_selectors = [
            'button:has-text("本日")',
            'button:has-text("本 日")',
            'a:has-text("本日")',
            'a:has-text("本 日")',
            'text="本日"',
            'text="本 日"',
        ]

        for selector in today_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    box = await btn.bounding_box()
                    if box:  # 表示されている場合のみ
                        await btn.click()
                        await asyncio.sleep(1)
                        logger.info("「本日」ボタンをクリック")
                        today_clicked = True
                        break
            except Exception:
                continue

        if not today_clicked:
            logger.warning("「本日」ボタンが見つかりません")

        # Step 2: 「›」ボタンをクリックして翌日へ
        # ボタンのテキストは「›」（右シェブロン）
        # 「»」は次月なので除外
        next_day_clicked = False

        # 翌日ボタンのパターン: 「›」または「>」
        next_day_chars = ['›', '>']

        try:
            # title="翌日" の属性で直接探す（最も確実）
            next_btn = page.locator('a[title="翌日"]').first
            if await next_btn.count() > 0:
                await next_btn.click()
                await asyncio.sleep(2)
                logger.info("翌日ボタン（title属性）で移動")
                next_day_clicked = True

            # title属性で見つからない場合、テキストで探す
            if not next_day_clicked:
                all_links = await page.locator('a').all()
                for link in all_links:
                    try:
                        text = (await link.inner_text()).strip()
                        # 「›」のみ（「»」や「››」を除外）
                        if text in next_day_chars:
                            await link.click()
                            await asyncio.sleep(3)  # 待機時間を増やす
                            logger.info(f"「{text}」リンクで翌日に移動")
                            next_day_clicked = True
                            break
                    except Exception:
                        continue

        except Exception as e:
            logger.debug(f"翌日ボタン検索エラー: {e}")

        if next_day_clicked:
            # テーブルの再描画を待つ
            await asyncio.sleep(3)
            try:
                await page.wait_for_selector('table', timeout=15000)
            except Exception:
                pass
            return True

        logger.warning("翌日への移動に失敗（本日のデータを使用）")
        return False

    except Exception as e:
        logger.error(f"翌日への移動に失敗: {e}")
        return False


async def get_stransa_chairs(page: Page) -> Dict[int, str]:
    """
    チェア（ユニット）名またはスタッフ名を取得

    Returns:
        {カラムインデックス: チェア名/スタッフ名} の辞書
    """
    chairs = {}

    try:
        # スケジュールテーブルを特定（チェアまたはスタッフ名を含むテーブル）
        tables = await page.locator('table').all()

        for table in tables:
            rows = await table.locator('tr').all()
            if len(rows) < 10:  # スケジュールテーブルは行数が多い
                continue

            first_row = table.locator('tr').first
            if await first_row.count() == 0:
                continue

            cells = await first_row.locator('td, th').all()
            found_column = False

            for i, cell in enumerate(cells):
                text = (await cell.inner_text()).strip()
                # チェアまたはスタッフ名を検出
                if is_staff_column(text):
                    chairs[i] = text
                    found_column = True
                    logger.debug(f"カラム検出: {i} -> {text}")

            if found_column:
                logger.info(f"カラム取得完了: {len(chairs)}個")
                break

    except Exception as e:
        logger.error(f"カラム取得エラー: {e}")

    return chairs


async def get_stransa_staff_from_settings(page: Page) -> List[str]:
    """
    設定 > スタッフページから全スタッフ名を取得

    カレンダーのカラムヘッダーではなく、/user/staffs の設定ページから
    実際のスタッフ名一覧を取得する。

    Returns:
        スタッフ名のリスト
    """
    staff_names = []

    try:
        await page.goto(
            'https://apo-toolboxes.stransa.co.jp/user/staffs',
            timeout=15000
        )
        await asyncio.sleep(3)

        # /user/staffs にいるか確認
        if '/user/staffs' not in page.url:
            logger.warning(f"スタッフ設定ページに遷移できず: {page.url}")
            return staff_names

        # テーブル構造: 複数tableの各行に名前列がある
        tables = await page.locator('table').all()
        for table in tables:
            rows = await table.locator('tr').all()
            for row in rows:
                cells = await row.locator('td').all()
                if cells:
                    name = (await cells[0].text_content() or '').strip()
                    # フィルタ: 空、ヘッダー、改行含み（ナビテキスト）、長すぎる名前を除外
                    if (name and name != '名前'
                            and '\n' not in name
                            and len(name) <= 30
                            and name not in staff_names):
                        staff_names.append(name)

        logger.info(f"スタッフ設定ページから{len(staff_names)}名取得")

    except Exception as e:
        logger.error(f"スタッフ設定ページ取得エラー: {e}")

    return staff_names


def is_staff_column(text: str) -> bool:
    """スタッフ/チェアカラムかどうかを判定"""
    if not text:
        return False
    text = text.strip()

    # 除外パターン（時間や日付、システム列、カレンダー要素）
    if ':' in text:  # 時間 (9:00, 10:30 etc.)
        return False

    exclude_texts = [
        '', '予約日', '空き枠数', '名前', 'AM', 'PM',
        '日', '月', '火', '水', '木', '金', '土',
        '«', '»', '<', '>',  # ナビゲーション
        '本日', '本 日', '週', '今日', 'クリア',
    ]
    if text in exclude_texts:
        return False

    # 年月パターンを除外（2026年1月 など）
    if '年' in text and '月' in text:
        return False

    # 数字のみは除外（日付など）
    if text.isdigit():
        return False

    # チェアベース: チェア1, チェア2, ...
    if text.startswith('チェア'):
        return True

    # スタッフベース: Dr○○, DH○○
    if text.startswith('Dr') or text.startswith('DH'):
        return True

    # 衛生士パターン: 衛生士(中山), 衛生士(尾崎) etc.
    if text.startswith('衛生士'):
        return True

    # 特定の役職/カラム名
    known_columns = ['TC', 'SP急患', 'SP', '急患', 'アシスト', 'TC/SP', '矯正']
    if text in known_columns:
        return True

    # スタッフ名パターン（/で区切られた名前）: 上手/中村, 赤木/藤森
    if '/' in text and 4 <= len(text) <= 12:
        return True

    # 漢字2-4文字で既知のパターン以外のスタッフ名
    # これは最後の手段として、明確にスタッフ名っぽいものだけ
    import re
    # スタッフ名: 漢字のみ2-4文字（ただし一般的な単語は除外）
    if re.match(r'^[\u4e00-\u9fff]{2,4}$', text):
        common_words = ['診療', '予約', '患者', '連絡', '掲示', '一覧', '追加', '削除', '設定', '表示', '非表示']
        if text not in common_words:
            return True

    return False


async def get_stransa_empty_slots(page: Page) -> Dict[str, List[int]]:
    """
    空きスロットを取得

    Stransa は15分刻みなので、30分空き = 2連続スロット

    Returns:
        {チェア名/スタッフ名: [空きスロット時間（分）のリスト]} の辞書
    """
    chair_slots: Dict[str, List[int]] = {}

    try:
        # スケジュールテーブルを特定（チェアまたはスタッフ名を含むテーブル）
        tables = await page.locator('table').all()
        schedule_table = None
        chairs = {}

        for table in tables:
            rows = await table.locator('tr').all()
            if len(rows) < 10:  # スケジュールテーブルは行数が多い
                continue

            first_row = rows[0] if rows else None
            if not first_row:
                continue

            cells = await first_row.locator('td, th').all()
            for i, cell in enumerate(cells):
                text = (await cell.inner_text()).strip()
                # チェアまたはスタッフ名を検出
                if is_staff_column(text):
                    chairs[i] = text

            if chairs:
                schedule_table = table
                logger.info(f"スケジュールテーブル発見: {len(chairs)}カラム, {len(rows)}行")
                break

        if not schedule_table or not chairs:
            logger.warning("スケジュールテーブルまたはスタッフカラムが見つかりません")
            return {}

        # 時間行を処理
        rows = await schedule_table.locator('tr').all()

        for row in rows:
            cells = await row.locator('td, th').all()
            if len(cells) < 2:
                continue

            # 最初のセルから時間を取得
            first_cell_text = (await cells[0].inner_text()).strip()

            # 時間形式（H:MM または HH:MM）かチェック
            if ':' not in first_cell_text:
                continue

            # 改行がある場合は最初の行だけ使用
            first_cell_text = first_cell_text.split('\n')[0].strip()

            try:
                parts = first_cell_text.split(':')
                hours = int(parts[0])
                mins = int(parts[1][:2])  # 秒がある場合に対応
                time_minutes = hours * 60 + mins
            except (ValueError, IndexError):
                continue

            # 各チェア列をチェック
            time_str = f"{hours}:{mins:02d}"
            for col_idx, chair_name in chairs.items():
                if col_idx >= len(cells):
                    continue

                cell = cells[col_idx]

                # セルの内容を取得（テキスト・HTML・属性）
                cell_text = (await cell.inner_text()).strip()
                cell_clean = cell_text.replace('\xa0', '').replace('\u200b', '').strip()

                # テキストがあれば予約済み → スキップ
                if cell_clean:
                    continue

                # ヘッダー行スキップ
                if cell_clean.startswith('チェア'):
                    continue

                # innerHTML で子要素チェック（ブロック済みセルは空テキストでもHTML要素あり）
                cell_html = await cell.inner_html()
                cell_html_clean = cell_html.strip().replace('\xa0', '').replace('\u200b', '').replace('<br>', '').replace('<br/>', '').strip()
                if '<' in cell_html_clean:
                    # 子要素がある = 予約枠やブロック済み
                    logger.debug(f"  [{chair_name}] {time_str}: HTML子要素あり → スキップ (html={cell_html_clean[:80]})")
                    continue

                # CSSクラスでブロック判定
                cell_class = (await cell.get_attribute('class')) or ''
                blocked_indicators = ['closed', 'blocked', 'disabled', 'holiday', 'off',
                                      'gray', 'lunch', 'break', 'reserve', 'past']
                if any(ind in cell_class.lower() for ind in blocked_indicators):
                    logger.debug(f"  [{chair_name}] {time_str}: ブロックCSS → スキップ (class={cell_class})")
                    continue

                # style属性でブロック判定（背景色付き = ブロック）
                cell_style = (await cell.get_attribute('style')) or ''
                if cell_style:
                    style_lower = cell_style.lower()
                    # background-color が設定されていて白/透明でなければブロック
                    if 'background' in style_lower:
                        # 白(#fff, white, rgb(255)等)や透明はOK
                        if not any(w in style_lower for w in ['#fff', 'white', 'transparent', 'rgb(255']):
                            logger.debug(f"  [{chair_name}] {time_str}: 背景色あり → スキップ (style={cell_style[:80]})")
                            continue

                # 全チェックをパス → 空き枠
                if chair_name not in chair_slots:
                    chair_slots[chair_name] = []
                chair_slots[chair_name].append(time_minutes)

        # 結果をログ出力
        for chair, slots in sorted(chair_slots.items()):
            times_str = ', '.join(f"{s//60}:{s%60:02d}" for s in sorted(slots)[:10])
            if len(slots) > 10:
                times_str += f"... (+{len(slots)-10})"
            logger.info(f"  {chair}: {len(slots)}スロット（15分枠）: {times_str}")

    except Exception as e:
        logger.error(f"空きスロット取得エラー: {e}")
        import traceback
        traceback.print_exc()

    return chair_slots


async def scrape_stransa_clinic(
    browser: Browser,
    clinic: Dict[str, str]
) -> Optional[Dict[str, List[int]]]:
    """
    1つのStransa分院をスクレイピング

    Args:
        browser: Browser オブジェクト
        clinic: 分院設定

    Returns:
        {チェア名: [スロット時間（分）のリスト]} または失敗時 None
    """
    page = await browser.new_page()

    try:
        # ログイン
        if not await login_stransa(page, clinic):
            return None

        # 翌日に移動
        if not await navigate_to_tomorrow_stransa(page):
            logger.warning(f"{clinic['name']}: 翌日への移動に失敗")

        # 空きスロットを取得
        chair_slots = await get_stransa_empty_slots(page)

        return chair_slots

    except Exception as e:
        logger.error(f"Stransa スクレイピングエラー: {clinic['name']} - {e}")
        return None

    finally:
        await page.close()


async def scrape_all_stransa_clinics(
    clinics: List[Dict[str, str]],
    headless: bool = True,
    browser=None
) -> Dict[str, Dict[str, List[int]]]:
    """
    全てのStransa分院をスクレイピング

    Args:
        clinics: 分院設定リスト
        headless: ヘッドレスモードで実行するか
        browser: 既存ブラウザ（ブラウザプールから渡された場合）

    Returns:
        {分院名: {チェア名: [スロット時間のリスト]}} の辞書
    """
    results = {}
    sem = asyncio.Semaphore(3)

    stransa_clinics = [c for c in clinics if c.get('system') == 'stransa']
    logger.info(f"Stransa対象分院数: {len(stransa_clinics)}")

    own_browser = browser is None  # 自前起動かどうか

    try:
        if own_browser:
            logger.info("Playwright起動中...")
            pw = await async_playwright().start()
            logger.info("Chromium起動中...")
            browser = await pw.chromium.launch(
                headless=headless,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
            )
            logger.info("Chromium起動完了")
        else:
            logger.info("ブラウザプールのブラウザを使用")

        async def scrape_with_sem(clinic):
            async with sem:
                logger.info(f"Stransa スクレイピング開始: {clinic['name']}")
                try:
                    chair_slots = await scrape_stransa_clinic(browser, clinic)
                    logger.info(f"Stransa スクレイピング完了: {clinic['name']}")
                    return clinic['name'], chair_slots if chair_slots is not None else {}
                except Exception as e:
                    logger.error(f"Stransa スクレイピングエラー: {clinic['name']} - {e}")
                    return clinic['name'], {}

        tasks = [scrape_with_sem(c) for c in stransa_clinics]
        for name, slots in await asyncio.gather(*tasks):
            results[name] = slots

        if own_browser:
            await browser.close()
            await pw.stop()
            logger.info("Chromium終了")

    except Exception as e:
        logger.error(f"Playwright/Chromium起動失敗: {e}")
        import traceback
        traceback.print_exc()

    return results


async def sync_stransa_staff(
    clinics: List[Dict[str, str]],
    headless: bool = True
) -> Dict[str, List[str]]:
    """
    全Stransa分院のスタッフ名を設定ページから同期取得

    Args:
        clinics: 分院設定リスト
        headless: ヘッドレスモードで実行するか

    Returns:
        {分院名: [全スタッフ名リスト]} の辞書
    """
    results = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)

        for clinic in clinics:
            if clinic.get('system') != 'stransa':
                continue

            logger.info(f"Stransa スタッフ同期中: {clinic['name']}")
            page = await browser.new_page()

            try:
                if not await login_stransa(page, clinic):
                    results[clinic['name']] = []
                    continue

                staff = await get_stransa_staff_from_settings(page)
                results[clinic['name']] = staff
                logger.info(f"{clinic['name']}: {len(staff)}名取得")

            except Exception as e:
                logger.error(f"同期エラー: {clinic['name']} - {e}")
                results[clinic['name']] = []

            finally:
                await page.close()

        await browser.close()

    return results
