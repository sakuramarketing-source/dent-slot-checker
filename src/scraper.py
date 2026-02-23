"""Playwrightスクレイピングモジュール"""

import asyncio
import logging
import os
import re
import yaml
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page, Browser, Frame

logger = logging.getLogger(__name__)


def load_staff_rules(config_path: str) -> Dict:
    """staff_rules.yamlを読み込む"""
    staff_rules_path = os.path.join(config_path, 'staff_rules.yaml')

    if not os.path.exists(staff_rules_path):
        return {'staff_by_clinic': {}}

    with open(staff_rules_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {'staff_by_clinic': {}}


async def login(page: Page, clinic: Dict[str, str]) -> bool:
    """
    分院サイトにログイン

    Args:
        page: Playwright Page オブジェクト
        clinic: 分院設定 (url, id, password)

    Returns:
        ログイン成功したかどうか
    """
    try:
        await page.goto(clinic['url'])
        await page.wait_for_load_state('networkidle')

        # ログインフォームの存在確認
        # dent-sys.net のログインフォームは通常 input[type="text"] と input[name="password"]
        id_input = page.locator('input[type="text"]').first
        pass_input = page.locator('input[name="password"], input[type="password"]').first

        if await id_input.count() > 0 and await pass_input.count() > 0:
            await id_input.fill(clinic['id'])
            await pass_input.fill(clinic['password'])

            # ログインボタンをクリック
            submit_btn = page.locator('input[type="submit"], button[type="submit"], input[value="ログイン"]').first
            if await submit_btn.count() > 0:
                await submit_btn.click()
                await page.wait_for_load_state('networkidle')

        logger.info(f"ログイン完了: {clinic['name']}")
        return True

    except Exception as e:
        logger.error(f"ログイン失敗: {clinic['name']} - {e}")
        return False


async def navigate_to_tomorrow(page: Page) -> bool:
    """
    翌日の予約表に移動

    Returns:
        移動成功したかどうか
    """
    try:
        # 「翌日」ボタンをクリック（input[value="翌日"]）
        tomorrow_btn = page.locator('input[value="翌日"]').first
        if await tomorrow_btn.count() > 0:
            await tomorrow_btn.click()
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)  # iframe読み込み待ち
            logger.info("翌日に移動しました")
            return True

        # 代替: リンクを探す
        tomorrow_link = page.locator('a:has-text("翌日"), a:has-text("次の日")').first
        if await tomorrow_link.count() > 0:
            await tomorrow_link.click()
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)
            logger.info("翌日に移動しました")
            return True

        logger.warning("翌日ボタン/リンクが見つかりません")
        return False

    except Exception as e:
        logger.error(f"翌日への移動に失敗: {e}")
        return False


async def get_schedule_iframe(page: Page) -> Optional[Frame]:
    """
    スケジュール表示用のiframeを取得
    （二重にネストされている: ts_timetable.php > ts_timetable_week.php）

    Returns:
        iframe の Frame オブジェクト、または None
    """
    try:
        # page.frames から ts_timetable_week を含むURLのフレームを探す
        # これが実際のスケジュールテーブルを含むiframe
        for frame in page.frames:
            if 'ts_timetable_week' in frame.url:
                logger.info(f"スケジュールiframeを取得しました: {frame.url}")
                return frame

        logger.warning("スケジュールiframeが見つかりません")
        return None

    except Exception as e:
        logger.error(f"iframe取得エラー: {e}")
        return None


async def get_column_headers_from_main_page(
    page: Page,
    exclude_patterns: List[str],
    disabled_staff: List[str] = None
) -> Dict[int, str]:
    """
    メインページのヘッダー行から先生名を取得

    Args:
        page: Page オブジェクト
        exclude_patterns: 除外するパターンリスト
        disabled_staff: 無効化されたスタッフ名リスト

    Returns:
        {カラムインデックス: 先生名} の辞書（除外パターンに該当するものを除く）
    """
    headers = {}
    disabled_staff = disabled_staff or []

    try:
        # ヘッダー行の全<th>要素を取得（時刻列含む）
        # 構造: <tr class="d_info"><th>時刻</th><th><a>先生名</a></th>...</tr>
        # col_idx = テーブル上の実際の列番号（ts_set_newのcol引数と一致させる）
        all_th_cells = await page.locator('tr.d_info th').all()

        for col_idx, th_cell in enumerate(all_th_cells):
            # <a>タグを含む<th>のみがスタッフ列
            a_tags = await th_cell.locator('a').all()
            if not a_tags:
                logger.debug(f"列{col_idx}: <a>なし（時刻列等）- スキップ")
                continue

            text = await a_tags[0].inner_text()
            text = text.strip()

            if not text:
                continue

            # 除外パターンに該当するかチェック
            excluded = False
            for pattern in exclude_patterns:
                if pattern in text:
                    excluded = True
                    logger.debug(f"除外: {text} (パターン: {pattern})")
                    break

            # 無効化されたスタッフかチェック
            if not excluded and text in disabled_staff:
                excluded = True
                logger.debug(f"除外（無効化）: {text}")

            if not excluded:
                headers[col_idx] = text

        logger.info(f"ヘッダー取得完了: {len(headers)}カラム（列番号: {list(headers.keys())}）")

    except Exception as e:
        logger.error(f"ヘッダー取得エラー: {e}")

    return headers


async def detect_start_time_from_iframe(frame: Frame, default_hour: int = 8, default_minute: int = 30) -> int:
    """
    iframe内のスケジュール表から開始時刻を検出

    dent-sysのスケジュール表では、各行の左端に時刻表示がある。
    最初の時刻表示を取得して開始時刻を特定する。

    Returns:
        開始時刻（分単位、例: 8:30 = 510）
    """
    default_minutes = default_hour * 60 + default_minute

    try:
        # 方法1: テーブル行の最初のセルから時刻を探す
        rows = await frame.locator('table tr').all()
        for row in rows[:20]:  # 最初の20行を調べる
            cells = await row.locator('th, td').all()
            if not cells:
                continue
            first_text = (await cells[0].inner_text()).strip()
            if not first_text:
                continue

            # "H:MM" or "HH:MM" 形式の時刻を検索
            time_match = re.match(r'^(\d{1,2}):(\d{2})$', first_text)
            if time_match:
                hours = int(time_match.group(1))
                minutes = int(time_match.group(2))
                if 0 <= hours <= 23 and 0 <= minutes < 60:
                    detected = hours * 60 + minutes
                    logger.info(f"開始時刻を検出: {hours}:{minutes:02d} (row_idx=0)")
                    return detected

            # 時間のみ（"8" or "9"）の場合
            if first_text.isdigit():
                hours = int(first_text)
                if 6 <= hours <= 12:
                    # 次のセルや行から分を推測（通常00分）
                    detected = hours * 60
                    logger.info(f"開始時刻を検出（時のみ）: {hours}:00 (row_idx=0)")
                    return detected

        logger.info(f"開始時刻検出できず、デフォルト使用: {default_hour}:{default_minute:02d}")
    except Exception as e:
        logger.debug(f"開始時刻検出エラー: {e}")

    return default_minutes


async def build_row_time_mapping(frame: Frame, slot_interval: int = 5) -> Dict[int, int]:
    """
    iframe内のスケジュール表から row_idx → 実時刻(分) のマッピングを構築

    dent-sysの表構造:
    - 各時間の最初の行: 左端セルに "9" や "14" 等の時表示
    - 以降の行: 左端セルに "10", "20", "30" 等の分表示
    - 昼休み: 行自体が存在しない（スキップされる）
    - スロット間隔は分院により異なる（5分/10分）

    時間 vs 分の区別ルール: 時間は常に前進する。
    値を分として解釈した時刻が前の行より戻る場合、それは新しい時間マーカー。

    Returns:
        {row_idx: time_minutes} の辞書
    """
    row_map: Dict[int, int] = {}
    current_hour: Optional[int] = None
    row_idx = 0

    try:
        rows = await frame.locator('table tr').all()

        for row in rows:
            cells = await row.locator('th, td').all()
            if len(cells) < 2:
                continue

            first_text = (await cells[0].inner_text()).strip()

            # "H:MM" or "HH:MM" 形式
            time_match = re.match(r'^(\d{1,2}):(\d{2})$', first_text)
            if time_match:
                current_hour = int(time_match.group(1))
                current_min = int(time_match.group(2))
                row_map[row_idx] = current_hour * 60 + current_min
                row_idx += 1
                continue

            if first_text.isdigit():
                val = int(first_text)

                if current_hour is None:
                    # 最初の数値 → 時間として扱う
                    if 0 <= val <= 23:
                        current_hour = val
                        row_map[row_idx] = current_hour * 60
                        row_idx += 1
                        continue
                else:
                    # 分として解釈した場合の時刻
                    candidate_as_minute = current_hour * 60 + val
                    prev_time = row_map.get(row_idx - 1, -1)

                    if 0 <= val < 60 and candidate_as_minute > prev_time:
                        # 時間が前進する → 分として扱う
                        row_map[row_idx] = candidate_as_minute
                        row_idx += 1
                        continue
                    elif 0 <= val <= 23 and val > current_hour:
                        # 分として解釈すると前進しないが、時間としては前進 → 新しい時間
                        current_hour = val
                        row_map[row_idx] = current_hour * 60
                        row_idx += 1
                        continue
                    elif 0 <= val <= 23 and val == current_hour:
                        # 同じ時間の繰り返し → 分の可能性（0分）
                        # 前の行が xx:50 で次が xx:00 の場合
                        candidate_as_hour = val * 60
                        if candidate_as_hour > prev_time:
                            current_hour = val
                            row_map[row_idx] = current_hour * 60
                            row_idx += 1
                            continue

            # 空セルだが予約スロット行の可能性（<a>タグがある行）
            has_links = await row.locator('a').count()
            if has_links > 0 and current_hour is not None:
                if (row_idx - 1) in row_map:
                    row_map[row_idx] = row_map[row_idx - 1] + slot_interval
                row_idx += 1
                continue

        if row_map:
            from .slot_analyzer import minutes_to_time_str
            first_time = minutes_to_time_str(min(row_map.values()))
            last_time = minutes_to_time_str(max(row_map.values()))
            logger.info(f"行-時刻マッピング構築完了: {len(row_map)}行 ({first_time}〜{last_time})")
        else:
            logger.warning("行-時刻マッピング構築失敗: テーブル行から時刻を検出できず")

    except Exception as e:
        logger.error(f"行-時刻マッピング構築エラー: {e}")

    return row_map


async def parse_schedule_from_iframe(
    frame: Frame,
    headers: Dict[int, str],
    slot_interval: int = 5,
    start_hour: int = 8,
    start_minute: int = 30
) -> Dict[str, List[int]]:
    """
    iframe内の予約表を解析し、各カラムの「新」スロットを収集

    Args:
        frame: iframe の Frame オブジェクト
        headers: {カラムインデックス: 先生名} の辞書
        slot_interval: スロット間隔（分）
        start_hour: 開始時刻（フォールバック用）
        start_minute: 開始時刻（フォールバック用）

    Returns:
        {先生名: [スロット時間（分）のリスト]} の辞書
    """
    doctor_slots: Dict[str, List[int]] = {}

    # row_idx → 実時刻のマッピングを構築（昼休みギャップ対応）
    row_time_map = await build_row_time_mapping(frame, slot_interval)

    # フォールバック: マッピングが空なら従来の線形計算を使用
    base_time_minutes = None
    if not row_time_map:
        base_time_minutes = await detect_start_time_from_iframe(frame, start_hour, start_minute)
        logger.warning("行-時刻マッピング構築失敗、線形計算にフォールバック")

    try:
        # 全ての「新」リンクを取得
        new_links = await frame.locator('a.new').all()
        logger.info(f"「新」リンク数: {len(new_links)}")

        if not new_links:
            new_links = await frame.locator('a:has-text("新")').all()
            logger.info(f"テキスト検索「新」リンク数: {len(new_links)}")

        unmapped_cols = set()
        unmapped_rows = set()

        for link in new_links:
            try:
                href = await link.get_attribute('href')
                if not href:
                    continue

                match = re.search(r'ts_set_new\((\d+),\s*(\d+)\)', href)
                if match:
                    col_idx = int(match.group(1))
                    row_idx = int(match.group(2))

                    # row_idx → 実時刻の変換
                    if row_time_map:
                        if row_idx in row_time_map:
                            time_minutes = row_time_map[row_idx]
                        else:
                            # マッピングに存在しない行 → 最も近い行から補間
                            closest = min(row_time_map.keys(), key=lambda k: abs(k - row_idx))
                            time_minutes = row_time_map[closest] + (row_idx - closest) * slot_interval
                            unmapped_rows.add(row_idx)
                    else:
                        # フォールバック: 線形計算
                        time_minutes = base_time_minutes + (row_idx * slot_interval)

                    if col_idx not in headers:
                        unmapped_cols.add(col_idx)
                        continue

                    doctor_name = headers[col_idx]

                    if doctor_name not in doctor_slots:
                        doctor_slots[doctor_name] = []

                    doctor_slots[doctor_name].append(time_minutes)

            except Exception as e:
                logger.debug(f"リンク解析エラー: {e}")
                continue

        if unmapped_cols:
            logger.warning(f"マッピングされなかったcol値: {sorted(unmapped_cols)}（対象ヘッダーcol: {sorted(headers.keys())}）")
        if unmapped_rows:
            logger.warning(f"マッピングされなかったrow値（補間使用）: {sorted(unmapped_rows)}")

        # 結果をログ出力（時間範囲付き）
        from .slot_analyzer import minutes_to_time_str
        for doctor, slots in doctor_slots.items():
            sorted_slots = sorted(slots)
            time_range = f"{minutes_to_time_str(sorted_slots[0])}-{minutes_to_time_str(sorted_slots[-1])}" if sorted_slots else "なし"
            logger.info(f"  {doctor}: {len(slots)}スロット ({time_range})")

    except Exception as e:
        logger.error(f"iframe解析エラー: {e}")

    return doctor_slots


async def parse_table_by_rows(
    page: Page,
    headers: Dict[int, str],
    slot_interval: int = 5
) -> Dict[str, List[int]]:
    """
    テーブルを行ごとに解析（代替方法）

    Args:
        page: Page オブジェクト
        headers: {カラムインデックス: 先生名} の辞書
        slot_interval: スロット間隔（分）

    Returns:
        {先生名: [スロット時間（分）のリスト]} の辞書
    """
    doctor_slots: Dict[str, List[int]] = {}

    try:
        rows = await page.locator('table tr').all()

        for row in rows:
            cells = await row.locator('td, th').all()
            if not cells:
                continue

            # 最初のセルから時間を取得（例: "9", "25" など）
            first_cell_text = await cells[0].inner_text()
            first_cell_text = first_cell_text.strip()

            # 時間の解析を試みる
            time_minutes = parse_time_from_cell(first_cell_text)
            if time_minutes is None:
                continue

            # 各カラムをチェック
            for col_idx, doctor_name in headers.items():
                if col_idx >= len(cells):
                    continue

                cell = cells[col_idx]
                cell_html = await cell.inner_html()

                # 「新」リンクがあるかチェック
                if 'class="new"' in cell_html or '>新<' in cell_html:
                    if doctor_name not in doctor_slots:
                        doctor_slots[doctor_name] = []
                    doctor_slots[doctor_name].append(time_minutes)

    except Exception as e:
        logger.error(f"行解析エラー: {e}")

    return doctor_slots


def parse_time_from_cell(text: str) -> Optional[int]:
    """
    セルのテキストから時間（分）を解析

    Args:
        text: セルのテキスト（例: "9:25", "25", "9" など）

    Returns:
        分単位の時間（例: 9:25 → 565）、解析失敗時はNone
    """
    text = text.strip()
    if not text:
        return None

    # "9:25" 形式
    if ':' in text:
        try:
            parts = text.split(':')
            hours = int(parts[0])
            mins = int(parts[1])
            return hours * 60 + mins
        except (ValueError, IndexError):
            pass

    # "25" 形式（分のみ）- 前の行から時間を推測する必要がある
    try:
        mins = int(text)
        if 0 <= mins < 60:
            # 分だけの場合、9時台と仮定（実際は前後の行から推測が必要）
            return mins
    except ValueError:
        pass

    return None


async def scrape_clinic(
    browser: Browser,
    clinic: Dict[str, str],
    exclude_patterns: List[str],
    slot_interval: int = 5,
    disabled_staff: List[str] = None
) -> Optional[Dict[str, List[int]]]:
    """
    1つの分院をスクレイピング

    Args:
        browser: Browser オブジェクト
        clinic: 分院設定
        exclude_patterns: 除外パターン
        slot_interval: スロット間隔（分）
        disabled_staff: 無効化されたスタッフ名リスト

    Returns:
        {先生名: [スロット時間（分）のリスト]} または失敗時 None
    """
    page = await browser.new_page()
    disabled_staff = disabled_staff or []

    try:
        # ログイン
        if not await login(page, clinic):
            return None

        # 翌日に移動
        if not await navigate_to_tomorrow(page):
            logger.warning(f"{clinic['name']}: 翌日への移動に失敗、現在の日付で続行")

        # メインページからヘッダー（先生名）を取得
        headers = await get_column_headers_from_main_page(page, exclude_patterns, disabled_staff)
        if not headers:
            logger.warning(f"{clinic['name']}: ヘッダー取得失敗")
            return {}

        logger.info(f"対象カラム: {list(headers.values())}")

        # iframe を取得
        frame = await get_schedule_iframe(page)
        if not frame:
            logger.warning(f"{clinic['name']}: iframe取得失敗")
            return {}

        # iframe内のスケジュールを解析
        doctor_slots = await parse_schedule_from_iframe(frame, headers, slot_interval)

        return doctor_slots

    except Exception as e:
        logger.error(f"スクレイピングエラー: {clinic['name']} - {e}")
        return None

    finally:
        await page.close()


async def scrape_all_clinics(
    clinics: List[Dict[str, str]],
    exclude_patterns: List[str],
    slot_interval: int = 5,
    headless: bool = True,
    config_path: str = None
) -> Dict[str, Dict[str, List[int]]]:
    """
    全ての分院をスクレイピング

    Args:
        clinics: 分院設定リスト
        exclude_patterns: 除外パターン
        slot_interval: スロット間隔（分）
        headless: ヘッドレスモードで実行するか
        config_path: 設定ファイルのパス

    Returns:
        {分院名: {先生名: [スロット時間のリスト]}} の辞書
    """
    results = {}

    # スタッフルールを読み込む
    staff_rules = {}
    if config_path:
        staff_rules = load_staff_rules(config_path)

    staff_by_clinic = staff_rules.get('staff_by_clinic', {})

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)

        for clinic in clinics:
            logger.info(f"スクレイピング開始: {clinic['name']}")

            # この分院の無効化スタッフリストを取得
            clinic_staff_config = staff_by_clinic.get(clinic['name'], {})
            disabled_staff = clinic_staff_config.get('disabled', [])

            doctor_slots = await scrape_clinic(
                browser, clinic, exclude_patterns, slot_interval, disabled_staff
            )

            if doctor_slots is not None:
                results[clinic['name']] = doctor_slots
            else:
                results[clinic['name']] = {}

        await browser.close()

    return results


async def get_all_headers_from_page(page: Page) -> List[str]:
    """
    メインページから全てのヘッダー（スタッフ名）を取得（除外なし）

    Args:
        page: Page オブジェクト

    Returns:
        全スタッフ名のリスト
    """
    headers = []

    try:
        header_cells = await page.locator('tr.d_info th a').all()

        for cell in header_cells:
            text = await cell.inner_text()
            text = text.strip()
            if text:
                headers.append(text)

        logger.info(f"全ヘッダー取得: {len(headers)}件")

    except Exception as e:
        logger.error(f"ヘッダー取得エラー: {e}")

    return headers


async def sync_all_staff(
    clinics: List[Dict[str, str]],
    headless: bool = True
) -> Dict[str, List[str]]:
    """
    全分院のスタッフ名を同期取得

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
            logger.info(f"スタッフ同期中: {clinic['name']}")
            page = await browser.new_page()

            try:
                # ログイン
                if not await login(page, clinic):
                    logger.warning(f"{clinic['name']}: ログイン失敗")
                    results[clinic['name']] = []
                    continue

                # 全ヘッダーを取得
                all_headers = await get_all_headers_from_page(page)
                results[clinic['name']] = all_headers

                logger.info(f"{clinic['name']}: {len(all_headers)}名取得")

            except Exception as e:
                logger.error(f"同期エラー: {clinic['name']} - {e}")
                results[clinic['name']] = []

            finally:
                await page.close()

        await browser.close()

    return results
