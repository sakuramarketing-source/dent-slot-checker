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
