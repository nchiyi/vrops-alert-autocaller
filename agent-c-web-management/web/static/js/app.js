/**
 * app.js — vROps Alert Manager 前端邏輯
 */

// ============================
// SIP 狀態即時更新
// ============================
function setSipStatus(isOnline, label) {
    const el = document.getElementById('sip-status');
    if (!el) return;
    // 清除舊內容
    while (el.firstChild) el.removeChild(el.firstChild);
    // 建立 dot
    const dot = document.createElement('span');
    dot.className = 'dot ' + (isOnline === true ? 'online' : isOnline === false ? 'offline' : '');
    el.appendChild(dot);
    el.appendChild(document.createTextNode(' ' + label));
}

async function updateSipStatus() {
    try {
        const res = await fetch('/health');
        const data = await res.json();
        if (data.sip_registered) {
            setSipStatus(true, 'SIP 已連線');
        } else {
            setSipStatus(false, 'SIP 離線');
        }
    } catch (e) {
        setSipStatus(false, '服務無回應');
    }
}
setInterval(updateSipStatus, 10000);
document.addEventListener('DOMContentLoaded', updateSipStatus);


// ============================
// 通用 API 呼叫
// ============================
async function apiCall(method, url, body = null) {
    const opts = {
        method,
        headers: { 'Content-Type': 'application/json' }
    };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    return res.json();
}


// ============================
// 聯絡人 CRUD
// ============================
async function addContact() {
    const name = document.getElementById('contact-name').value.trim();
    const number = document.getElementById('contact-number').value.trim();
    const groupId = document.getElementById('contact-group').value;
    const priority = document.getElementById('contact-priority').value || 1;
    const noteEl = document.getElementById('contact-note');
    const note = noteEl ? noteEl.value.trim() : '';

    if (!name || !number) { alert('請填寫姓名和號碼'); return; }

    await apiCall('POST', '/api/contacts', {
        name, number,
        group_id: parseInt(groupId),
        priority: parseInt(priority),
        note
    });

    closeModal();
    location.reload();
}

async function deleteContact(id) {
    if (!confirm('確定刪除此聯絡人？')) return;
    await apiCall('DELETE', '/api/contacts/' + id);
    location.reload();
}

async function editContact(id) {
    const row = document.querySelector('tr[data-id="' + id + '"]');
    const name = prompt('修改姓名:', row.dataset.name);
    if (name === null) return;
    const number = prompt('修改號碼:', row.dataset.number);
    if (number === null) return;
    if (name && number) {
        await apiCall('PUT', '/api/contacts/' + id, { name, number });
        location.reload();
    }
}


// ============================
// 群組管理
// ============================
async function addGroup() {
    const name = prompt('群組名稱:');
    if (!name) return;
    const desc = prompt('群組描述 (可空):') || '';
    await apiCall('POST', '/api/groups', { name, description: desc });
    location.reload();
}

async function deleteGroup(id) {
    if (!confirm('刪除群組會同時刪除相關路由規則，確定嗎？')) return;
    await apiCall('DELETE', '/api/groups/' + id);
    location.reload();
}


// ============================
// 路由規則
// ============================
async function addRule() {
    const name = document.getElementById('rule-name').value.trim();
    const matchField = document.getElementById('rule-match-field').value;
    const matchPattern = document.getElementById('rule-match-pattern').value.trim();
    const targetGroupId = document.getElementById('rule-target-group').value;
    const priorityEl = document.getElementById('rule-priority');
    const priority = priorityEl ? parseInt(priorityEl.value) : 1;
    const descEl = document.getElementById('rule-description');
    const description = descEl ? descEl.value.trim() : '';

    if (!name || !matchPattern) { alert('請填寫規則名稱和匹配模式'); return; }

    await apiCall('POST', '/api/rules', {
        name,
        match_field: matchField,
        match_pattern: matchPattern,
        target_group_id: parseInt(targetGroupId),
        priority,
        description
    });

    closeModal();
    location.reload();
}

async function deleteRule(id) {
    if (!confirm('確定刪除此路由規則？')) return;
    await apiCall('DELETE', '/api/rules/' + id);
    location.reload();
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

// 點擊遮罩關閉
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.modal-overlay').forEach(function(overlay) {
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) closeModal();
        });
    });
});
