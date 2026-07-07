/**
 * app.js
 * ------
 * Vanilla JavaScript (no framework/build step) that consumes the Ticket
 * Bridge REST API and populates the four tabs: Systems, Topics,
 * Conversations, Audit.
 *
 * Note: these endpoints (/api/v1/systems, /api/v1/topics,
 * /api/v1/conversations, /api/v1/audit) are assumed to be protected by
 * Cloud Run IAM or by an authentication proxy in front of the service -
 * this file does not implement login. See README.md, "Configuration
 * frontend security" section.
 */

const API_BASE = "/api/v1";

let allTopics = [];

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------
document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    });
});

// ---------------------------------------------------------------------------
// Topics
// ---------------------------------------------------------------------------
async function loadTopics() {
    const resp = await fetch(`${API_BASE}/topics`);
    allTopics = await resp.json();

    const tbody = document.querySelector("#table-topics tbody");
    tbody.innerHTML = "";
    for (const t of allTopics) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><code>${t.code}</code></td>
            <td>${t.name}</td>
            <td>${t.description ?? "—"}</td>
            <td><span class="badge ${t.active ? "active" : "inactive"}">${t.active ? "Active" : "Inactive"}</span></td>
            <td>${formatDate(t.updated_at)}</td>
        `;
        tr.style.cursor = "pointer";
        tr.addEventListener("click", () => openTopicDialog(t));
        tbody.appendChild(tr);
    }

    populateTopicFilter();
}

const topicDialog = document.getElementById("dialog-topic");
const topicForm = document.getElementById("form-topic");
let topicCodeBeingEdited = null;

document.getElementById("btn-new-topic").addEventListener("click", () => openTopicDialog(null));
document.getElementById("btn-cancel-topic").addEventListener("click", () => topicDialog.close());

function openTopicDialog(topic) {
    topicForm.reset();
    topicCodeBeingEdited = topic ? topic.code : null;
    topicForm.code.disabled = !!topic;

    if (topic) {
        topicForm.code.value = topic.code;
        topicForm.name.value = topic.name;
        topicForm.description.value = topic.description ?? "";
        topicForm.active.checked = topic.active;
    }
    topicDialog.showModal();
}

topicForm.addEventListener("submit", async () => {
    const data = new FormData(topicForm);
    const body = {
        name: data.get("name"),
        description: data.get("description") || null,
        active: topicForm.active.checked,
    };

    if (topicCodeBeingEdited) {
        await fetch(`${API_BASE}/topics/${topicCodeBeingEdited}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
    } else {
        body.code = data.get("code");
        await fetch(`${API_BASE}/topics`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
    }
    await loadTopics();
});

function populateTopicFilter() {
    const sel = document.getElementById("filter-conversations-topic");
    while (sel.options.length > 1) sel.remove(1);
    for (const t of allTopics) {
        const opt = document.createElement("option");
        opt.value = t.code;
        opt.textContent = `${t.name} (${t.code})`;
        sel.appendChild(opt);
    }
}

function renderSystemTopicsCheckboxes(checkedCodes = []) {
    const container = document.getElementById("system-topics-checkboxes");
    container.innerHTML = "";
    for (const t of allTopics) {
        const label = document.createElement("label");
        label.className = "checkbox";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = t.code;
        input.checked = checkedCodes.includes(t.code);
        label.appendChild(input);
        label.append(` ${t.name} (${t.code})`);
        container.appendChild(label);
    }
}

// ---------------------------------------------------------------------------
// Systems
// ---------------------------------------------------------------------------
async function loadSystems() {
    const resp = await fetch(`${API_BASE}/systems`);
    const systems = await resp.json();

    const tbody = document.querySelector("#table-systems tbody");
    tbody.innerHTML = "";
    const selects = [
        document.getElementById("filter-conversations-system"),
        document.getElementById("filter-audit-system"),
    ];
    selects.forEach((sel) => {
        while (sel.options.length > 1) sel.remove(1);
    });

    for (const s of systems) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><code>${s.code}</code></td>
            <td>${s.name}</td>
            <td>${s.base_url}</td>
            <td>${s.auth_type}</td>
            <td>${(s.topics || []).join(", ") || "—"}</td>
            <td><span class="badge ${s.active ? "active" : "inactive"}">${s.active ? "Active" : "Inactive"}</span></td>
            <td>${formatDate(s.updated_at)}</td>
        `;
        tr.style.cursor = "pointer";
        tr.addEventListener("click", () => openSystemDialog(s));
        tbody.appendChild(tr);

        selects.forEach((sel) => {
            const opt = document.createElement("option");
            opt.value = s.code;
            opt.textContent = s.name;
            sel.appendChild(opt);
        });
    }
}

const systemDialog = document.getElementById("dialog-system");
const systemForm = document.getElementById("form-system");
let codeBeingEdited = null;

document.getElementById("btn-new-system").addEventListener("click", () => openSystemDialog(null));
document.getElementById("btn-cancel-system").addEventListener("click", () => systemDialog.close());

function openSystemDialog(system) {
    systemForm.reset();
    codeBeingEdited = system ? system.code : null;
    systemForm.code.disabled = !!system;

    if (system) {
        systemForm.code.value = system.code;
        systemForm.name.value = system.name;
        systemForm.base_url.value = system.base_url;
        systemForm.auth_type.value = system.auth_type;
        systemForm.active.checked = system.active;
    }
    renderSystemTopicsCheckboxes(system ? system.topics : []);
    systemDialog.showModal();
}

systemForm.addEventListener("submit", async () => {
    const data = new FormData(systemForm);

    const topics = Array.from(
        document.querySelectorAll("#system-topics-checkboxes input[type=checkbox]:checked")
    ).map((el) => el.value);

    const body = {
        name: data.get("name"),
        base_url: data.get("base_url"),
        auth_type: data.get("auth_type"),
        auth_config: { secret_ref: data.get("secret_ref") || null },
        active: systemForm.active.checked,
        topics: topics,
    };

    if (codeBeingEdited) {
        await fetch(`${API_BASE}/systems/${codeBeingEdited}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
    } else {
        body.code = data.get("code");
        await fetch(`${API_BASE}/systems`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
    }
    await loadSystems();
});

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------
async function loadConversations() {
    const systemCode = document.getElementById("filter-conversations-system").value;
    const topicCode = document.getElementById("filter-conversations-topic").value;
    const params = new URLSearchParams();
    if (systemCode) params.set("system_code", systemCode);
    if (topicCode) params.set("topic_code", topicCode);
    const qs = params.toString() ? `?${params.toString()}` : "";
    const resp = await fetch(`${API_BASE}/conversations${qs}`);
    const conversations = await resp.json();

    const tbody = document.querySelector("#table-conversations tbody");
    tbody.innerHTML = "";
    for (const c of conversations) {
        const participants = c.participants
            .map((p) => `${p.system_code}: <code>${p.external_ref}</code> (${p.local_status ?? "—"})`)
            .join("<br>");
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><code>${c.conversation_id.slice(0, 8)}…</code></td>
            <td>${c.subject ?? "—"}</td>
            <td><code>${c.topic_code}</code></td>
            <td>${c.overall_status}</td>
            <td>${participants}</td>
            <td>${formatDate(c.updated_at)}</td>
        `;
        tbody.appendChild(tr);
    }
}

document.getElementById("filter-conversations-system").addEventListener("change", loadConversations);
document.getElementById("filter-conversations-topic").addEventListener("change", loadConversations);

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------
async function loadAudit() {
    const systemCode = document.getElementById("filter-audit-system").value;
    const qs = systemCode ? `?system_code=${encodeURIComponent(systemCode)}` : "";
    const resp = await fetch(`${API_BASE}/audit${qs}`);
    const entries = await resp.json();

    const tbody = document.querySelector("#table-audit tbody");
    tbody.innerHTML = "";
    for (const r of entries) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${formatDate(r.created_at)}</td>
            <td>${r.event_type}</td>
            <td>${r.system_code ?? "—"}</td>
            <td>${r.conversation_id ? r.conversation_id.slice(0, 8) + "…" : "—"}</td>
            <td><code>${JSON.stringify(r.detail)}</code></td>
        `;
        tbody.appendChild(tr);
    }
}

document.getElementById("filter-audit-system").addEventListener("change", loadAudit);

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function formatDate(iso) {
    return new Date(iso).toLocaleString("en-GB");
}

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------
(async function start() {
    const statusEl = document.getElementById("connection-status");
    try {
        await fetch("/health").then((r) => r.json());
        statusEl.textContent = "Connected to the service.";
        await loadTopics();
        await loadSystems();
        await loadConversations();
        await loadAudit();
    } catch (e) {
        statusEl.textContent = "Could not reach the service.";
    }
})();
