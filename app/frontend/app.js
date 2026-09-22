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

const API_BASE = "api/v1";

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
        systemForm.active.checked = system.active;
        systemForm.auth_header.value = system.auth_header ?? "";
        systemForm.auth_value_prefix.value = system.auth_value_prefix ?? "";
        // The secret_ref value itself is never returned by the API (see
        // SystemOut), so this field always starts blank - only has_secret
        // (whether one is configured at all) is known. Leaving it blank on
        // Save keeps whatever secret_ref is already stored; type a new one
        // to replace it (there is currently no way to clear it back to
        // "no secret" from this dialog - use the API directly for that).
        systemForm.secret_ref.placeholder = system.has_secret
            ? "•••••••• (a secret is configured - leave blank to keep it)"
            : "e.g. system_c_outbound_key";
    } else {
        systemForm.secret_ref.placeholder = "e.g. system_c_outbound_key";
    }
    renderSystemTopicsCheckboxes(system ? system.topics : []);

    // Inbound API keys are scoped to an existing system_code - nothing to
    // attach them to until the system itself has been saved once.
    currentApiKeysSystemCode = system ? system.code : null;
    const apiKeysSection = document.getElementById("system-api-keys-section");
    document.getElementById("new-api-key-description").value = "";
    if (system) {
        apiKeysSection.hidden = false;
        loadApiKeys(system.code);
    } else {
        apiKeysSection.hidden = true;
        document.querySelector("#table-api-keys tbody").innerHTML = "";
    }

    systemDialog.showModal();
}

systemForm.addEventListener("submit", async () => {
    const data = new FormData(systemForm);

    const topics = Array.from(
        document.querySelectorAll("#system-topics-checkboxes input[type=checkbox]:checked")
    ).map((el) => el.value);

    // Only include auth_config keys the admin actually typed a value for.
    // The backend merges (not replaces) auth_config on update, so omitting
    // a key here leaves whatever is already stored untouched - critical for
    // secret_ref, whose current value this form never sees (see SystemOut).
    const authConfig = {};
    const authHeader = data.get("auth_header");
    if (authHeader) authConfig.header = authHeader;
    const authValuePrefix = data.get("auth_value_prefix");
    if (authValuePrefix) authConfig.value_prefix = authValuePrefix;
    const secretRef = data.get("secret_ref");
    if (secretRef) authConfig.secret_ref = secretRef;

    const body = {
        name: data.get("name"),
        base_url: data.get("base_url"),
        active: systemForm.active.checked,
        topics: topics,
    };
    if (Object.keys(authConfig).length > 0) {
        body.auth_config = authConfig;
    }

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
// Inbound API keys (per-system, nested in the system edit dialog)
// ---------------------------------------------------------------------------
let currentApiKeysSystemCode = null;

async function loadApiKeys(systemCode) {
    const resp = await fetch(`${API_BASE}/systems/${systemCode}/api-keys`);
    const keys = await resp.json();

    const tbody = document.querySelector("#table-api-keys tbody");
    tbody.innerHTML = "";

    if (keys.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" class="hint">No API keys yet.</td></tr>`;
        return;
    }

    for (const k of keys) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${k.description ?? "—"}</td>
            <td><span class="badge ${k.active ? "active" : "inactive"}">${k.active ? "Active" : "Revoked"}</span></td>
            <td>${formatDate(k.created_at)}</td>
            <td>${k.active ? `<button type="button" class="btn-revoke">Revoke</button>` : "—"}</td>
        `;
        if (k.active) {
            tr.querySelector(".btn-revoke").addEventListener("click", async () => {
                if (!confirm("Revoke this API key? The system using it will immediately lose access.")) return;
                await fetch(`${API_BASE}/systems/${systemCode}/api-keys/${k.id}`, { method: "DELETE" });
                await loadApiKeys(systemCode);
            });
        }
        tbody.appendChild(tr);
    }
}

document.getElementById("form-api-key-generate").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!currentApiKeysSystemCode) return;

    const input = document.getElementById("new-api-key-description");
    const resp = await fetch(`${API_BASE}/systems/${currentApiKeysSystemCode}/api-keys`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: input.value.trim() || null }),
    });
    if (!resp.ok) {
        alert("Could not generate the API key.");
        return;
    }
    const created = await resp.json();
    input.value = "";
    await loadApiKeys(currentApiKeysSystemCode);
    openApiKeyReveal(created.api_key);
});

// One-time reveal dialog, Kibana/Elasticsearch-style: the plaintext key is
// only ever available in this response - shown once, then discarded. The
// field is cleared on close so it doesn't linger in the DOM afterward.
const apiKeyRevealDialog = document.getElementById("dialog-api-key-reveal");

function openApiKeyReveal(plaintextKey) {
    const field = document.getElementById("api-key-reveal-value");
    field.value = plaintextKey;
    document.getElementById("api-key-copy-feedback").textContent = "";
    apiKeyRevealDialog.showModal();
    field.focus();
    field.select();
}

document.getElementById("btn-copy-api-key").addEventListener("click", async () => {
    const field = document.getElementById("api-key-reveal-value");
    field.select();
    const feedback = document.getElementById("api-key-copy-feedback");
    try {
        await navigator.clipboard.writeText(field.value);
        feedback.textContent = "Copied to clipboard.";
    } catch {
        feedback.textContent = "Could not copy automatically - copy the value manually.";
    }
});

document.getElementById("btn-close-api-key-reveal").addEventListener("click", () => {
    document.getElementById("api-key-reveal-value").value = "";
    apiKeyRevealDialog.close();
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
const AUDIT_PAGE_SIZE = 50;
let auditOffset = 0;

async function loadAudit() {
    const systemCode = document.getElementById("filter-audit-system").value;
    const params = new URLSearchParams({ limit: AUDIT_PAGE_SIZE, offset: auditOffset });
    if (systemCode) params.set("system_code", systemCode);
    const resp = await fetch(`${API_BASE}/audit?${params.toString()}`);
    const page = await resp.json();

    const tbody = document.querySelector("#table-audit tbody");
    tbody.innerHTML = "";
    for (const r of page.items) {
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

    const pageNumber = Math.floor(auditOffset / AUDIT_PAGE_SIZE) + 1;
    document.getElementById("audit-page-label").textContent =
        page.items.length ? `Page ${pageNumber}` : "No entries";
    document.getElementById("btn-audit-prev").disabled = auditOffset === 0;
    document.getElementById("btn-audit-next").disabled = !page.has_more;
}

document.getElementById("filter-audit-system").addEventListener("change", () => {
    auditOffset = 0;
    loadAudit();
});
document.getElementById("btn-audit-prev").addEventListener("click", () => {
    auditOffset = Math.max(0, auditOffset - AUDIT_PAGE_SIZE);
    loadAudit();
});
document.getElementById("btn-audit-next").addEventListener("click", () => {
    auditOffset += AUDIT_PAGE_SIZE;
    loadAudit();
});

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
        await fetch("health").then((r) => r.json());
        statusEl.textContent = "Connected to the service.";
        await loadTopics();
        await loadSystems();
        await loadConversations();
        await loadAudit();
    } catch (e) {
        statusEl.textContent = "Could not reach the service.";
    }
})();
