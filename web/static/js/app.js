// 共通JavaScript関数

// APIエラーハンドリング
async function apiRequest(url, options = {}) {
    try {
        const response = await fetch(url, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || 'API request failed');
        }

        return data;
    } catch (error) {
        console.error('API Error:', error);
        throw error;
    }
}

// 日付フォーマット
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('ja-JP');
}

// 日時フォーマット
function formatDateTime(dateTimeString) {
    const date = new Date(dateTimeString);
    return date.toLocaleString('ja-JP');
}

// === タイムライン関連 ===

const TIMELINE_START = 540; // 9:00 = 540分
const TIMELINE_END = 1140;  // 19:00 = 1140分
const TIMELINE_RANGE = TIMELINE_END - TIMELINE_START; // 600分

// "9:25-9:55" → { start: 565, end: 595 }
function parseTimeRange(timeStr) {
    const parts = timeStr.split('-');
    if (parts.length !== 2) return null;
    const start = parseTimeToMinutes(parts[0].trim());
    const end = parseTimeToMinutes(parts[1].trim());
    if (start === null || end === null) return null;
    return { start, end };
}

// "9:25" → 565
function parseTimeToMinutes(timeStr) {
    const parts = timeStr.split(':');
    if (parts.length !== 2) return null;
    const h = parseInt(parts[0], 10);
    const m = parseInt(parts[1], 10);
    if (isNaN(h) || isNaN(m)) return null;
    return h * 60 + m;
}

// 分 → タイムライン上の%位置
function minutesToPercent(minutes) {
    return ((minutes - TIMELINE_START) / TIMELINE_RANGE) * 100;
}

// タイムラインバーHTML生成
// times: ["9:25-9:55", "14:00-14:30"], cssClass: "doctor"|"hygienist"|"unknown"
function buildTimelineSlotsHTML(times, cssClass) {
    if (!times || times.length === 0) return '';
    let html = '';
    for (const t of times) {
        const range = parseTimeRange(t);
        if (!range) continue;
        const left = Math.max(0, minutesToPercent(range.start));
        const right = Math.min(100, minutesToPercent(range.end));
        const width = right - left;
        if (width <= 0) continue;
        html += `<div class="timeline-slot ${cssClass}" style="left:${left}%;width:${width}%" title="${t}"></div>`;
    }
    return html;
}

// 現在時刻インジケーターHTML
function buildNowIndicatorHTML() {
    const now = new Date();
    const minutes = now.getHours() * 60 + now.getMinutes();
    if (minutes < TIMELINE_START || minutes > TIMELINE_END) return '';
    const left = minutesToPercent(minutes);
    return `<div class="timeline-now" style="left:${left}%"></div>`;
}

// 時刻ラベルHTML（9, 10, 11, ... 19）
function buildTimelineLabelsHTML() {
    let html = '<div class="timeline-labels">';
    for (let h = 9; h <= 19; h++) {
        html += `<span>${h}</span>`;
    }
    html += '</div>';
    return html;
}

// クリニック全体の統合タイムラインHTML
// details: [{ doctor, blocks, times, category? }]
function buildClinicTimelineHTML(details) {
    if (!details || details.length === 0) return '';
    let allSlots = '';
    for (const d of details) {
        allSlots += buildTimelineSlotsHTML(d.times, d.category || 'unknown');
    }
    const nowIndicator = buildNowIndicatorHTML();
    return `<div class="clinic-timeline"><div class="timeline-container"><div class="timeline-bar compact">${allSlots}${nowIndicator}</div>${buildTimelineLabelsHTML()}</div></div>`;
}

// スタッフ別タイムラインHTML（詳細展開内）
function buildStaffTimelineHTML(times, cssClass) {
    if (!times || times.length === 0) return '';
    const slots = buildTimelineSlotsHTML(times, cssClass || 'unknown');
    const nowIndicator = buildNowIndicatorHTML();
    return `<div class="timeline-container"><div class="timeline-bar">${slots}${nowIndicator}</div></div>`;
}
