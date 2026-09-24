/**
 * app.js
 * ------
 * Frontend for the Ticket Bridge simulator (no framework, no build step -
 * same approach as the bridge's own admin panel). Talks only to the
 * simulator's own API (api/...); the simulator backend is what calls the
 * bridge.
 *
 * Every value from the API is rendered with textContent or escapeHtml():
 * ticket subjects, notes and metadata come from other systems via the
 * bridge.
 */

const STATUSES = ["new", "in_progress", "waiting_third_party", "resolved", "closed"];
const POLL_MS = 3000;

let selectedId = null;
let renderedId = null;          // ticket whose reply form is currently in the DOM
const lastSeen = {};            // ticket id -> updated_at when last viewed (drives the "new activity" dot)
let firstLoad = true;
let config = {};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function formatDate(iso) {
    return new Date(iso).toLocaleString("en-GB");
}

async function api(path, options = {}) {
    const resp = await fetch(`api/${path}`, {
        headers: { "Content-Type": "application/json" },
        ...options,
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
        throw new Error(detail || `HTTP ${resp.status}`);
    }
    return body;
}

/** Parses an optional JSON-object textarea; throws a readable error. */
function parseMetadata(text) {
    if (!text || !text.trim()) return {};
    let value;
    try {
        value = JSON.parse(text);
    } catch {
        throw new Error("Extra metadata must be valid JSON.");
    }
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
        throw new Error("Extra metadata must be a JSON object, e.g. {\"key\": \"value\"}.");
    }
    return value;
}

function fillStatusSelect(select, selected) {
    select.innerHTML = STATUSES.map((s) => `<option value="${s}">${s}</option>`).join("");
    if (selected) select.value = selected;
}

function showError(form, message) {
    const el = form.querySelector(".form-error");
    el.textContent = message ?? "";
    el.hidden = !message;
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${btn.dataset.tab}`));
        if (btn.dataset.tab === "config") loadConfig();
    });
});

// ---------------------------------------------------------------------------
// Tickets
// ---------------------------------------------------------------------------
async function loadTickets() {
    const tickets = await api("tickets");
    const tbody = document.querySelector("#table-tickets tbody");
    tbody.innerHTML = "";
    document.getElementById("tickets-empty").hidden = tickets.length > 0;

    for (const t of tickets) {
        if (firstLoad) lastSeen[t.id] = t.updated_at;
        const unseen = t.id !== selectedId && lastSeen[t.id] !== t.updated_at;
        const tr = document.createElement("tr");
        tr.classList.toggle("selected", t.id === selectedId);
        tr.innerHTML = `
            <td><code>${escapeHtml(t.ref)}</code>${unseen ? '<span class="dot" title="New activity"></span>' : ""}</td>
            <td>${escapeHtml(t.subject ?? "—")}</td>
            <td><span class="badge ${escapeHtml(t.status)}">${escapeHtml(t.status)}</span></td>
            <td>${formatDate(t.updated_at)}</td>
        `;
        tr.addEventListener("click", () => selectTicket(t.id));
        tbody.appendChild(tr);
    }
    firstLoad = false;
}

async function selectTicket(id) {
    selectedId = id;
    await loadDetail();
    await loadTickets();
}

async function loadDetail() {
    const container = document.getElementById("ticket-detail");
    if (selectedId === null) return;

    const ticket = await api(`tickets/${selectedId}`);
    lastSeen[ticket.id] = ticket.updated_at;

    // Build the detail (and its reply form) once per selected ticket; later
    // refreshes only update header + timeline, keeping any half-typed reply.
    if (renderedId !== ticket.id) {
        container.replaceChildren(document.getElementById("tpl-detail").content.cloneNode(true));
        const form = container.querySelector(".reply-form");
        fillStatusSelect(form.status, ticket.status);
        form.addEventListener("submit", (e) => sendReply(e, ticket.id));
        renderedId = ticket.id;
    }

    container.querySelector(".detail-title").textContent = `${ticket.ref} · ${ticket.subject ?? "(no subject)"}`;
    const meta = [
        ticket.origin === "sent" ? "Opened here" : `Received from ${ticket.counterpart ?? "?"}`,
        ticket.topic_code ? `topic ${ticket.topic_code}` : null,
        ticket.conversation_id ? `conversation ${ticket.conversation_id}` : null,
    ].filter(Boolean);
    container.querySelector(".detail-meta").textContent = meta.join(" · ");
    const badge = container.querySelector(".detail-status");
    badge.className = `badge detail-status ${ticket.status}`;
    badge.textContent = ticket.status;
    container.querySelector(".detail-unlinked").hidden = ticket.linked;

    const timeline = container.querySelector(".timeline");
    timeline.innerHTML = "";
    for (const m of ticket.messages) {
        const { note, ...rest } = m.metadata;
        const li = document.createElement("li");
        li.className = m.direction;
        li.innerHTML = `
            <div class="msg-head">
                <strong>${m.direction === "in" ? "←" : "→"} ${escapeHtml(m.system ?? "")}</strong>
                <span class="badge ${escapeHtml(m.status)}">${escapeHtml(m.status)}</span>
                ${m.event ? `<code>${escapeHtml(m.event)}</code>` : ""}
                <span>${formatDate(m.created_at)}</span>
            </div>
        `;
        if (note !== undefined) {
            const p = document.createElement("p");
            p.className = "msg-note";
            p.textContent = String(note);
            li.appendChild(p);
        }
        if (Object.keys(rest).length) {
            const pre = document.createElement("pre");
            pre.textContent = JSON.stringify(rest, null, 2);
            li.appendChild(pre);
        }
        timeline.appendChild(li);
    }
}

async function sendReply(event, ticketId) {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector("button[type=submit]");
    showError(form, null);
    try {
        const body = {
            status: form.status.value,
            note: form.note.value || null,
            metadata: parseMetadata(form.metadata.value),
        };
        button.disabled = true;
        await api(`tickets/${ticketId}/reply`, { method: "POST", body: JSON.stringify(body) });
        form.note.value = "";
        form.metadata.value = "";
        await loadDetail();
        await loadTickets();
    } catch (err) {
        showError(form, err.message);
    } finally {
        button.disabled = false;
    }
}

// New ticket dialog
const newDialog = document.getElementById("dialog-new-ticket");
const newForm = document.getElementById("form-new-ticket");
fillStatusSelect(newForm.status, "new");

document.getElementById("btn-new-ticket").addEventListener("click", () => {
    newForm.reset();
    fillStatusSelect(newForm.status, "new");
    newForm.topic_code.value = config.default_topic ?? "";
    showError(newForm, null);
    newDialog.showModal();
});
document.getElementById("btn-cancel-new-ticket").addEventListener("click", () => newDialog.close());

newForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = newForm.querySelector("button[type=submit]");
    showError(newForm, null);
    try {
        const body = {
            topic_code: newForm.topic_code.value.trim(),
            subject: newForm.subject.value.trim(),
            status: newForm.status.value,
            note: newForm.note.value || null,
            metadata: parseMetadata(newForm.metadata.value),
        };
        button.disabled = true;
        const ticket = await api("tickets", { method: "POST", body: JSON.stringify(body) });
        newDialog.close();
        await selectTicket(ticket.id);
    } catch (err) {
        showError(newForm, err.message);
    } finally {
        button.disabled = false;
    }
});

document.getElementById("btn-refresh").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
        await refreshTickets();
    } finally {
        e.target.disabled = false;
    }
});

async function refreshTickets() {
    await loadTickets();
    await loadDetail();
}

setInterval(() => {
    const onTickets = document.getElementById("tab-tickets").classList.contains("active");
    if (onTickets && document.getElementById("auto-refresh").checked && !document.hidden) {
        refreshTickets().catch(() => {});
    }
}, POLL_MS);

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const configForm = document.getElementById("form-config");

async function loadConfig() {
    config = await api("config");
    for (const name of ["bridge_url", "system_code", "ref_prefix", "default_topic", "inbound_header"]) {
        configForm[name].value = config[name] ?? "";
    }
    // Secrets are never returned - only whether one is stored.
    configForm.api_key.value = "";
    configForm.api_key.placeholder = config.api_key_set ? "•••••••• (set - leave blank to keep)" : "not set";
    configForm.inbound_secret.value = "";
    configForm.inbound_secret.placeholder = config.inbound_secret_set
        ? "•••••••• (set - leave blank to keep)"
        : "not set - deliveries are accepted without auth";
    configForm.clear_inbound_secret.checked = false;
    document.getElementById("clear-inbound-secret-field").hidden = !config.inbound_secret_set;
    document.getElementById("webhook-url").textContent = new URL("webhook", location.href).href;
    renderHeader();
}

function renderHeader() {
    document.getElementById("header-system").textContent = config.system_code || "(no system code)";
    document.getElementById("header-bridge").textContent = config.bridge_url;
}

configForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const saved = document.getElementById("config-saved");
    saved.className = "hint";
    try {
        config = await api("config", {
            method: "PUT",
            body: JSON.stringify({
                bridge_url: configForm.bridge_url.value.trim(),
                system_code: configForm.system_code.value.trim(),
                ref_prefix: configForm.ref_prefix.value.trim(),
                default_topic: configForm.default_topic.value.trim(),
                inbound_header: configForm.inbound_header.value.trim() || "X-API-Key",
                api_key: configForm.api_key.value || null,
                inbound_secret: configForm.inbound_secret.value || null,
                clear_inbound_secret: configForm.clear_inbound_secret.checked,
            }),
        });
        await loadConfig();
        saved.textContent = "Saved.";
        saved.classList.add("ok");
    } catch (err) {
        saved.textContent = err.message;
        saved.classList.add("warning");
    }
});

document.getElementById("btn-test").addEventListener("click", async () => {
    const out = document.getElementById("test-result");
    out.className = "hint";
    out.textContent = "Testing…";
    try {
        await api("config/test", { method: "POST" });
        out.textContent = "Bridge reachable. (The API key is only checked when you send a ticket.)";
        out.classList.add("ok");
    } catch (err) {
        out.textContent = err.message;
        out.classList.add("warning");
    }
});

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------
(async function start() {
    await loadConfig();
    await loadTickets();
})();
