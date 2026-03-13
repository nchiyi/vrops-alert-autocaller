/**
 * app.js — vROps Alert Manager 前端邏輯
 */

// ============================
// SIP 狀態即時更新
// ============================
function setSipStatus(online) {
    const el = document.getElementById('sip-status');
    if (!el) return;
    el.textContent = '';
    const dot = document.createElement('span');
    dot.className = 'dot ' + (online ? 'online' : 'offline');
    const text = document.createTextNode(online ? ' SIP 已連線' : ' SIP 離線');
    el.appendChild(dot);
    el.appendChild(text);
}

async function updateSipStatus() {
    try {
        const res = await fetch('/health');
        const data = await res.json();
        setSipStatus(!!data.sip_registered);
    } catch (e) {
        const el = document.getElementById('sip-status');
        if (el) {
            el.textContent = '';
            const dot = document.createElement('span');
            dot.className = 'dot offline';
            el.appendChild(dot);
            el.appendChild(document.createTextNode(' 服務無回應'));
        }
    }
}
setInterval(updateSipStatus, 10000);
document.addEventListener('DOMContentLoaded', updateSipStatus);


// ============================
// 通用 API 呼叫（含錯誤處理）
// ============================
async function apiCall(method, url, body) {
    const opts = {
        method,
        headers: { 'Content-Type': 'application/json' }
    };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(url, opts);

    // session 過期 → 重導回登入頁
    if (res.status === 401) {
        window.location.href = '/login';
        return null;
    }

    // 嘗試解析 JSON，失敗時回傳錯誤物件
    try {
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || `HTTP ${res.status}`);
        }
        return data;
    } catch (e) {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        throw e;
    }
}

// 顯示操作結果訊息
function showToast(msg, isError) {
    let toast = document.getElementById('toast-msg');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'toast-msg';
        toast.style.cssText = [
            'position:fixed', 'bottom:24px', 'right:24px',
            'padding:12px 24px', 'border-radius:8px',
            'font-size:0.95em', 'z-index:9999',
            'transition:opacity 0.3s'
        ].join(';');
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.style.background = isError ? '#e74c3c' : '#27ae60';
    toast.style.color = '#fff';
    toast.style.opacity = '1';
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { toast.style.opacity = '0'; }, 3000);
}


// ============================
// 聯絡人 CRUD
// ============================
async function addContact() {
    const name     = document.getElementById('contact-name').value.trim();
    const number   = document.getElementById('contact-number').value.trim();
    const groupId  = document.getElementById('contact-group').value;
    const priority = document.getElementById('contact-priority').value || 1;

    if (!name || !number) {
        showToast('請填寫姓名和號碼', true);
        return;
    }

    try {
        await apiCall('POST', '/api/contacts', {
            name,
            number,
            group_id: parseInt(groupId),
            priority: parseInt(priority)
        });
        showToast('聯絡人已新增');
        closeModal();
        location.reload();
    } catch (e) {
        showToast('新增失敗：' + e.message, true);
    }
}

async function deleteContact(id) {
    if (!confirm('確定刪除此聯絡人？')) return;
    try {
        await apiCall('DELETE', '/api/contacts/' + id);
        showToast('已刪除');
        location.reload();
    } catch (e) {
        showToast('刪除失敗：' + e.message, true);
    }
}

async function editContact(id) {
    const row    = document.querySelector('tr[data-id="' + id + '"]');
    const name   = prompt('修改姓名:', row.dataset.name);
    const number = prompt('修改號碼:', row.dataset.number);
    if (name && number) {
        try {
            await apiCall('PUT', '/api/contacts/' + id, { name, number });
            showToast('已更新');
            location.reload();
        } catch (e) {
            showToast('更新失敗：' + e.message, true);
        }
    }
}

async function toggleContact(id, enabled) {
    try {
        await apiCall('PUT', '/api/contacts/' + id, { enabled: enabled ? 1 : 0 });
        location.reload();
    } catch (e) {
        showToast('更新失敗：' + e.message, true);
    }
}


// ============================
// 群組管理
// ============================
async function addGroup() {
    const name = document.getElementById('group-name').value.trim();
    const desc = document.getElementById('group-desc').value.trim();
    if (!name) {
        showToast('請填寫群組名稱', true);
        return;
    }
    try {
        await apiCall('POST', '/api/groups', { name, description: desc });
        showToast('群組已新增');
        closeModal();
        location.reload();
    } catch (e) {
        showToast('新增失敗：' + e.message, true);
    }
}

async function deleteGroup(id) {
    if (!confirm('刪除群組會同時刪除相關路由規則，確定嗎？')) return;
    try {
        await apiCall('DELETE', '/api/groups/' + id);
        showToast('群組已刪除');
        location.reload();
    } catch (e) {
        showToast('刪除失敗：' + e.message, true);
    }
}


// ============================
// 路由規則
// ============================
async function addRule() {
    const name          = document.getElementById('rule-name').value.trim();
    const matchField    = document.getElementById('rule-match-field').value;
    const matchPattern  = document.getElementById('rule-match-pattern').value.trim();
    const targetGroupId = document.getElementById('rule-target-group').value;
    const priority      = document.getElementById('rule-priority').value || 1;

    if (!name || !matchPattern) {
        showToast('請填寫規則名稱和匹配模式', true);
        return;
    }

    try {
        await apiCall('POST', '/api/rules', {
            name,
            match_field: matchField,
            match_pattern: matchPattern,
            target_group_id: parseInt(targetGroupId),
            priority: parseInt(priority)
        });
        showToast('路由規則已新增');
        closeModal();
        location.reload();
    } catch (e) {
        showToast('新增失敗：' + e.message, true);
    }
}

async function deleteRule(id) {
    if (!confirm('確定刪除此路由規則？')) return;
    try {
        await apiCall('DELETE', '/api/rules/' + id);
        showToast('已刪除');
        location.reload();
    } catch (e) {
        showToast('刪除失敗：' + e.message, true);
    }
}


// ============================
// Modal 控制
// ============================
function openModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('active');
}

function closeModal() {
    document.querySelectorAll('.modal-overlay').forEach(function(m) {
        m.classList.remove('active');
    });
}

document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-overlay')) {
        closeModal();
    }
});
