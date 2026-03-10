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
                await page.wait_for_timeout(5000)
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
                        await page.wait_for_timeout(5000)
                        break
            except Exception:
                continue

        date_text = await page.locator('p.MuiTypography-root').first.inner_text()
        logger.info(f"[{clinic_name}] 最終日付: {date_text}")
        return tomorrow_str in date_text

    except Exception as e:
        logger.error(f"[{clinic_name}] 翌日移動エラー: {e}")
        return False


async def get_plum_empty_slots(page, clinic_name: str,
                               clinic: dict = None) -> Dict[str, List[int]]:
    """
    Plumカレンダーから空きスロットを検出

    アプローチ:
    1. 予約ブロックの描画完了を待機（React SPAの非同期レンダリング対応）
    2. ヘッダー行からスタッフ列（名前 + x座標）を取得
    3. カレンダーグリッド内の予約ブロック（色付きDIV）を全取得
    4. 各スタッフ列で予約ブロックが無い時間帯 = 空き枠
    """
    try:
        # React SPAの非同期レンダリング完了を待機
        # Cloud Runでは構造要素が先にレンダリングされ、予約データのAPI応答が遅れるため
        # 十分な要素数が安定するまでポーリングする

        # まず初期待機（APIから予約データをフェッチする時間を確保）
        await page.wait_for_timeout(5000)

        # 予約ブロック数が安定するまでポーリング（最大20秒）
        # 最小20要素（構造要素12個では不足、予約ブロック含めて20以上を要求）
        MIN_ELEMENTS = 20
        prev_block_count = 0
        stable_count = 0
        for i in range(20):
            block_count = await page.evaluate('''() => {
                let count = 0;
                for (const div of document.querySelectorAll('div')) {
                    const rect = div.getBoundingClientRect();
                    if (rect.width < 30 || rect.height < 8) continue;
                    if (rect.y < 80) continue;
                    const bg = getComputedStyle(div).backgroundColor;
                    if (!bg || bg === 'rgba(0, 0, 0, 0)' || bg === 'rgb(255, 255, 255)') continue;
                    count++;
                }
                return count;
            }''')
            if block_count >= MIN_ELEMENTS and block_count == prev_block_count:
                stable_count += 1
                if stable_count >= 3:
                    break
            else:
                stable_count = 0
            prev_block_count = block_count
            await page.wait_for_timeout(1000)

        logger.info(f"[{clinic_name}] 描画完了待機: {prev_block_count}要素検出 "
                    f"(ループ{i+1}回, 閾値{MIN_ELEMENTS})")

        result = await page.evaluate('''() => {
            // === ステップ1: スタッフ列ヘッダーを取得 ===
            // ヘッダー行: 色付きDIVでスタッフ名が表示（y=70-95付近）
            // 列数が多い場合に幅が狭くなるため、幅の下限を緩めに設定
            const headerDivs = [];
            const allDivs = document.querySelectorAll('div');
            for (const div of allDivs) {
                const rect = div.getBoundingClientRect();
                const style = getComputedStyle(div);
                const bg = style.backgroundColor;
                if (rect.y > 60 && rect.y < 100 &&
                    rect.width > 60 && rect.width < 300 &&
                    rect.height > 5 && rect.height < 30 &&
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
            const seenTimes = new Set();
            for (const div of allDivs) {
                const text = div.textContent.trim();
                if (text.match(/^\\d{2}:\\d{2}$/) && div.children.length === 0) {
                    const style = getComputedStyle(div);
                    if (style.position === 'absolute') {
                        const rect = div.getBoundingClientRect();
                        // 左側のラベルのみ取得（右側に重複ラベルがある）
                        if (rect.x < 500 && !seenTimes.has(text)) {
                            seenTimes.add(text);
                            const [h, m] = text.split(':').map(Number);
                            timeLabels.push({
                                time: text,
                                minutes: h * 60 + m,
                                y: rect.y
                            });
                        }
                    }
                }
            }
            timeLabels.sort((a, b) => a.y - b.y);

            // === ステップ3: 予約ブロックを全取得 ===
            const calendarTop = timeLabels.length > 0 ? timeLabels[0].y - 20 : 90;
            // サイドメニュー右端 = 最も左にあるヘッダーのx座標 - 5px
            const sideMenuRight = headerDivs.length > 0
                ? Math.min(...headerDivs.map(h => h.x)) - 5
                : 360;
            const blocks = [];
            for (const div of allDivs) {
                const rect = div.getBoundingClientRect();
                if (rect.width < 30 || rect.height < 8) continue;
                if (rect.x < sideMenuRight) continue;
                if (rect.y < calendarTop) continue;

                const style = getComputedStyle(div);
                const bg = style.backgroundColor;
                if (!bg || bg === 'rgba(0, 0, 0, 0)' || bg === 'rgb(255, 255, 255)') continue;

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
            logger.warning(f"[{clinic_name}] ヘッダーまたは時間ラベルが取得できません "
                          f"(headers={len(headers)}, timeLabels={len(time_labels)})")
            return {}

        for h in headers:
            logger.info(f"  スタッフ: {h['name']} (x={h['x']}, w={h['width']})")

        # === ステップ4: 各スタッフ列の空き枠を計算 ===
        staff_slots = {}

        # 時間ラベルからy座標→時間(分)のマッピングを作成
        # 15分間隔でチェック（30分ラベル間を2分割）
        check_interval = 15  # 分
        time_points = []
        for i in range(len(time_labels) - 1):
            t1 = time_labels[i]
            t2 = time_labels[i + 1]
            minutes = t1['minutes']
            while minutes < t2['minutes']:
                ratio = (minutes - t1['minutes']) / (t2['minutes'] - t1['minutes'])
                y = t1['y'] + ratio * (t2['y'] - t1['y'])
                time_points.append({'minutes': minutes, 'y': y})
                minutes += check_interval

        # 営業時間フィルタ（営業外の偽空きを排除）
        start_minutes = 10 * 60   # 10:00
        end_minutes = 19 * 60     # 19:00
        time_points = [tp for tp in time_points
                       if start_minutes <= tp['minutes'] < end_minutes]

        # 昼休みフィルタ（カレンダーにブロックがない昼休みを除外）
        clinic = clinic or {}
        lunch = clinic.get('lunch_break', {})
        if lunch:
            def _parse_hm(s):
                h, m = s.split(':')
                return int(h) * 60 + int(m)
            ls = _parse_hm(lunch['start'])
            le = _parse_hm(lunch['end'])
            time_points = [tp for tp in time_points
                           if not (ls <= tp['minutes'] < le)]

        logger.info(f"[{clinic_name}] チェック対象: {len(time_points)}ポイント "
                    f"({start_minutes//60}:00-{end_minutes//60}:00, "
                    f"昼休み除外={'あり' if lunch else 'なし'})")

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
                    # ブロックを上下12pxずつ拡張してピクセルギャップを吸収
                    # 12px ≒ 約8分（15分チェック間隔に対して十分な許容幅）
                    block_bottom = block['y'] + block['height']
                    y_overlap = check_y >= block['y'] - 12 and check_y < block_bottom + 12

                    if x_overlap and y_overlap:
                        is_booked = True
                        break

                if not is_booked:
                    empty_slots.append(check_minutes)

            # 孤立した偽空きスロットを除去
            # 前後30分以内に予約がある場合、間の1-2スロットはピクセルギャップとして除去
            if empty_slots and len(empty_slots) < len(time_points):
                booked_set = set(tp['minutes'] for tp in time_points) - set(empty_slots)
                filtered_empty = []
                for slot in empty_slots:
                    # この空きスロットの前後30分以内に予約があるかチェック
                    has_before = any(b < slot and slot - b <= 30 for b in booked_set)
                    has_after = any(b > slot and b - slot <= 30 for b in booked_set)
                    if has_before and has_after:
                        # 前後に予約あり → ピクセルギャップの偽空き → 除去
                        continue
                    filtered_empty.append(slot)
                empty_slots = filtered_empty

            staff_slots[staff_name] = empty_slots
            total_points = len(time_points)
            booked = total_points - len(empty_slots)
            if empty_slots:
                times_str = [f"{m//60}:{m%60:02d}" for m in empty_slots[:5]]
                logger.info(f"  {staff_name}: 予約済={booked}/{total_points}, "
                           f"空き={len(empty_slots)} "
                           f"(例: {', '.join(times_str)}...)")
            else:
                logger.info(f"  {staff_name}: 予約済={booked}/{total_points}, 空きなし")

        return staff_slots

    except Exception as e:
        logger.error(f"[{clinic_name}] 空き枠検出エラー: {e}")
        import traceback
        traceback.print_exc()
        return {}


async def get_plum_empty_slots_from_api(page, target_date: str,
                                        clinic_name: str,
                                        clinic: dict = None,
                                        auth_headers: dict = None) -> Dict[str, List[int]]:
    """
    Plum APIから直接予約データを取得して空きスロットを計算（DOMフォールバック）

    Cloud RunではReact SPAの予約ブロックがDOMに描画されないため、
    SPAが使用するREST APIを直接呼び出して予約データを取得する。
    """
    try:
        # SPAが使用する認証ヘッダーをそのまま渡す
        auth_headers = auth_headers or {}
        logger.info(f"[{clinic_name}] APIフォールバック: auth_headers={list(auth_headers.keys())}")

        api_data = await page.evaluate('''async ([targetDate, authHeaders]) => {
            try {
                const opts = {headers: authHeaders};
                const [booksResp, shiftsResp, linesResp] = await Promise.all([
                    fetch(`/api/books?date=${targetDate}`, opts),
                    fetch(`/api/shifts?date=${targetDate}`, opts),
                    fetch('/api/lines', opts)
                ]);
                if (!booksResp.ok || !shiftsResp.ok || !linesResp.ok) {
                    return {error: `API error: books=${booksResp.status} shifts=${shiftsResp.status} lines=${linesResp.status}`};
                }
                const books = await booksResp.json();
                const shifts = await shiftsResp.json();
                const lines = await linesResp.json();
                const [books, shifts, lines] = await Promise.all([
                    booksResp.json(), shiftsResp.json(), linesResp.json()
                ]);

                // shifts: line ID → user name
                const lineToUser = {};
                for (const s of shifts) {
                    if (s.user && s.user.name) {
                        lineToUser[s.line] = s.user.name;
                    }
                }

                // lines: line ID → line name (fallback)
                const lineToName = {};
                for (const l of lines) {
                    lineToName[l._id] = l.name;
                }

                // Group bookings by staff, convert to JST minute intervals
                const staffBookings = {};
                for (const book of books) {
                    const userName = lineToUser[book.line];
                    if (!userName) continue;
                    // Parse UTC ISO time → JST minutes from midnight
                    const startDate = new Date(book.start);
                    const endDate = new Date(book.end);
                    const startMin = (startDate.getUTCHours() + 9) % 24 * 60 + startDate.getUTCMinutes();
                    const endMin = (endDate.getUTCHours() + 9) % 24 * 60 + endDate.getUTCMinutes();
                    if (!staffBookings[userName]) staffBookings[userName] = [];
                    staffBookings[userName].push({start: startMin, end: endMin});
                }

                return {
                    staffBookings,
                    totalBooks: books.length,
                    staffCount: Object.keys(staffBookings).length
                };
            } catch (e) {
                return {error: e.message};
            }
        }''', [target_date, auth_headers])

        if not api_data or 'error' in api_data:
            logger.warning(f"[{clinic_name}] APIフォールバック失敗: {api_data}")
            return {}

        staff_bookings = api_data['staffBookings']
        logger.info(f"[{clinic_name}] APIフォールバック: {api_data['totalBooks']}件の予約, "
                    f"{api_data['staffCount']}スタッフ")

        # 営業時間・昼休み設定
        start_minutes = 10 * 60   # 10:00
        end_minutes = 19 * 60     # 19:00
        check_interval = 15

        clinic = clinic or {}
        lunch = clinic.get('lunch_break', {})
        lunch_start = lunch_end = 0
        if lunch:
            def _parse_hm(s):
                h, m = s.split(':')
                return int(h) * 60 + int(m)
            lunch_start = _parse_hm(lunch['start'])
            lunch_end = _parse_hm(lunch['end'])

        # 全チェックポイント生成
        all_check_points = []
        t = start_minutes
        while t < end_minutes:
            if not (lunch_start and lunch_start <= t < lunch_end):
                all_check_points.append(t)
            t += check_interval

        staff_slots = {}
        for staff_name, bookings in staff_bookings.items():
            empty_slots = []
            for check_min in all_check_points:
                is_booked = False
                for bk in bookings:
                    if bk['start'] <= check_min < bk['end']:
                        is_booked = True
                        break
                if not is_booked:
                    empty_slots.append(check_min)
            staff_slots[staff_name] = empty_slots

            total = len(all_check_points)
            booked = total - len(empty_slots)
            if empty_slots:
                times_str = [f"{m//60}:{m%60:02d}" for m in empty_slots[:5]]
                logger.info(f"  {staff_name}: 予約済={booked}/{total}, "
                           f"空き={len(empty_slots)} "
                           f"(例: {', '.join(times_str)}...)")
            else:
                logger.info(f"  {staff_name}: 予約済={booked}/{total}, 空きなし")

        return staff_slots

    except Exception as e:
        logger.error(f"[{clinic_name}] APIフォールバックエラー: {e}")
        import traceback
        traceback.print_exc()
        return {}


async def scrape_plum_clinic(browser, clinic: dict) -> Optional[Dict[str, List[int]]]:
    """Plum分院1つをスクレイピング"""
    import os
    clinic_name = clinic['name']
    page = await browser.new_page(viewport={'width': 1920, 'height': 1080})

    # Cloud Run診断: コンソールエラーとネットワーク失敗を収集
    console_errors = []
    failed_requests = []
    api_responses = []
    captured_auth_headers = {}  # SPAが使用する認証ヘッダーをキャプチャ
    page.on('console', lambda msg: console_errors.append(
        f"{msg.type}: {msg.text}") if msg.type in ('error', 'warning') else None)
    page.on('requestfailed', lambda req: failed_requests.append(
        f"{req.method} {req.url} → {req.failure}"))

    # SPAのAPIリクエストから認証ヘッダーをキャプチャ
    def _capture_request(request):
        if '/api/' in request.url and not captured_auth_headers:
            headers = request.headers
            for key in ('x-access-token', 'authorization', 'x-auth-token'):
                if key in headers:
                    captured_auth_headers[key] = headers[key]
    page.on('request', _capture_request)

    # APIレスポンスインターセプト（JSON応答をキャプチャ）
    async def _capture_response(response):
        try:
            url = response.url
            # 静的アセットをスキップ
            if any(url.endswith(ext) for ext in (
                '.js', '.css', '.png', '.jpg', '.svg', '.woff', '.woff2',
                '.ico', '.map', '.gif', '.webp')):
                return
            content_type = response.headers.get('content-type', '')
            if 'json' not in content_type:
                return
            body = await response.body()
            api_responses.append({
                'url': url,
                'status': response.status,
                'size': len(body),
                'body': body.decode('utf-8', errors='replace')[:100000]
            })
        except Exception:
            pass

    page.on('response', _capture_response)

    try:
        if not await login_plum(
            page, clinic['url'], clinic['id'], clinic['password'],
            clinic.get('device_name', '瀧田セカンドPC'), clinic_name
        ):
            return None

        if not await navigate_to_tomorrow_plum(page, clinic_name):
            logger.warning(f"[{clinic_name}] 翌日移動失敗、当日のデータで続行")

        slots = await get_plum_empty_slots(page, clinic_name, clinic=clinic)

        # DOM検出が疑わしい場合（全スタッフがほぼ全空き）、APIフォールバック
        if slots:
            total_check = sum(len(v) for v in slots.values())
            # 全スタッフの空き枠数の合計がチェックポイント×スタッフ数の80%超 = 疑わしい
            # （ほぼ全スロットが空き = 予約ブロック未描画の可能性大）
            num_staff = len(slots)
            # 営業時間10:00-19:00 = 36スロット、昼休み除外で32程度
            expected_per_staff = 32
            if num_staff > 0 and total_check > expected_per_staff * num_staff * 0.8:
                tomorrow = datetime.now(JST) + timedelta(days=1)
                target_date = tomorrow.strftime('%Y-%m-%d')
                logger.info(f"[{clinic_name}] DOM検出が疑わしい（空き{total_check}/"
                           f"{expected_per_staff * num_staff}）、APIフォールバック実行")
                api_slots = await get_plum_empty_slots_from_api(
                    page, target_date, clinic_name, clinic=clinic,
                    auth_headers=captured_auth_headers)
                if api_slots:
                    slots = api_slots

        # Cloud Run診断: スクリーンショット、APIレスポンス、ログをGCSに保存
        if os.environ.get('K_SERVICE'):
            try:
                import json as _json
                screenshot_path = '/tmp/plum_cloudrun_debug.png'
                await page.screenshot(path=screenshot_path, full_page=False)
                html_size = await page.evaluate(
                    '() => document.documentElement.outerHTML.length')
                logger.info(f"[{clinic_name}] Cloud Run診断: HTML={html_size}bytes, "
                            f"コンソールエラー={len(console_errors)}, "
                            f"ネットワーク失敗={len(failed_requests)}, "
                            f"APIレスポンス={len(api_responses)}件")
                for err in console_errors[:10]:
                    logger.info(f"  コンソール: {err[:200]}")
                for req in failed_requests[:10]:
                    logger.info(f"  リクエスト失敗: {req[:200]}")
                # APIレスポンス一覧をログ出力
                for resp in api_responses:
                    body_preview = (resp.get('body') or '')[:300]
                    logger.info(f"  API: {resp['status']} {resp['url'][:120]} "
                                f"size={resp['size']} body={body_preview}")
                # APIレスポンスをGCSにアップロード
                api_dump_path = '/tmp/plum_api_responses.json'
                with open(api_dump_path, 'w') as f:
                    _json.dump(api_responses, f, ensure_ascii=False, indent=2)
                from src.gcs_helper import upload_to_gcs
                upload_to_gcs(screenshot_path, 'debug/plum_cloudrun_debug.png')
                upload_to_gcs(api_dump_path, 'debug/plum_api_responses.json')
            except Exception as diag_err:
                logger.warning(f"[{clinic_name}] 診断情報保存失敗: {diag_err}")

        # name_mapping適用（カレンダー表示名→スタッフ管理名）
        name_mapping = clinic.get('name_mapping', {})
        if name_mapping and slots:
            mapped_slots = {}
            for name, times in slots.items():
                mapped_name = name_mapping.get(name, name)
                mapped_slots[mapped_name] = times
            slots = mapped_slots

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
