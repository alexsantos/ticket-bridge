/**
 * app.js
 * ------
 * Vanilla JavaScript (no framework/build step) that consumes the Ticket
 * Bridge REST API and populates the three tabs: Systems, Conversations,
 * Audit.
 *
 * Note: these endpoints (/api/v1/systems, /api/v1/conversations,
 * /api/v1/audit) are assumed to be protected by Cloud Run IAM or by an
 * authentication proxy in front of the service - this file does not
 * implement login. See README.md, "Configuration frontend security"
 * section.
 */

const API_BASE = "/api/v1";

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
        systemForm.status_mapping.value = JSON.stringify(system.status_mapping || {}, null, 2);
        systemForm.active.checked = system.active;
    }
    systemDialog.showModal();
}

systemForm.addEventListener("submit", async (ev) => {
    const data = new FormData(systemForm);
    let statusMapping = {};
    let payloadTemplate = {};
    try {
        statusMapping = data.get("status_mapping") ? JSON.parse(data.get("status_mapping")) : {};
        payloadTemplate = data.get("payload_template") ? JSON.parse(data.get("payload_template")) : {};
    } catch (e) {
        alert("Invalid JSON in status mapping or payload template.");
        ev.preventDefault();
        return;
    }

    const body = {
        name: data.get("name"),
        base_url: data.get("base_url"),
        auth_type: data.get("auth_type"),
        auth_config: { secret_ref: data.get("secret_ref") || null },
        status_mapping: statusMapping,
        payload_template: payloadTemplate,
        active: systemForm.active.checked,
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
    const qs = systemCode ? `?system_code=${encodeURIComponent(systemCode)}` : "";
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
            <td>${c.overall_status}</td>
            <td>${participants}</td>
            <td>${formatDate(c.updated_at)}</td>
        `;
        tbody.appendChild(tr);
    }
}

document.getElementById("filter-conversations-system").addEventListener("change", loadConversations);

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
        await loadSystems();
        await loadConversations();
        await loadAudit();
    } catch (e) {
        statusEl.textContent = "Could not reach the service.";
    }
})();
