/**
 * app.js
 * ------
 * Frontend for the Ticket Bridge simulator (no framework, no build step -
 * same approach as the bridge's own admin panel). Talks only to the
 * simulator's own API (api/...); the simulator backend is what calls the
 * bridge.
 *
 * Routes: #/tickets (list), #/tickets/<id> (a conversation), #/settings.
 * The list and the open conversation refresh every few seconds while
 * "Check for new messages" is on; a half-typed reply is never touched.
 *
 * Every value from the API is rendered with textContent or escapeHtml():
 * ticket subjects, notes and metadata come from other systems via the
 * bridge.
 */

const STATUS_LABELS = {
    new: "New",
    in_progress: "In progress",
    waiting_third_party: "Waiting on third party",
    resolved: "Resolved",
    closed: "Closed",
};
const POLL_MS = 3000;

let config = {};
let tickets = [];
let selectedId = null;
let lastThreadKey = "";          // what the rendered thread shows, to skip needless redraws
const lastSeen = {};             // ticket id -> updated_at when last viewed (drives the unread dot)
let firstLoad = true;
let originFilter = "";

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function icon(name) {
    return `<svg class="icon" aria-hidden="true"><use href="#${name}"/></svg>`;
}

function statusPill(status) {
    return `<span class="pill pill-${escapeHtml(status)}">${escapeHtml(STATUS_LABELS[status] ?? status)}</span>`;
}

// "14:05" today, "Yesterday", weekday within a week, else a short date.
function formatRelative(iso) {
    const date = new Date(iso);
    const now = new Date();
    const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const days = Math.round((day(now) - day(date)) / 86400000);
    if (days === 0) return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    if (days === 1) return "Yesterday";
    if (days < 7) return date.toLocaleDateString(undefined, { weekday: "long" });
    return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function formatDay(iso) {
    const label = formatRelative(iso);
    return /^\d/.test(label) ? "Today" : label;
}

function initials(text) {
    const words = String(text || "?").replace(/[^\p{L}\p{N}]+/gu, " ").trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) return "?";
    return (words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2)).toUpperCase();
}

function tintFor(code) {
    let hash = 0;
    for (const ch of String(code)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
    return `tint-${hash % 8}`;
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

/** Parses an optional JSON-object field; throws a readable error. */
function parseMetadata(text) {
    if (!text || !text.trim()) return {};
    let value;
    try {
        value = JSON.parse(text);
    } catch {
        throw new Error("Extra fields must be valid JSON.");
    }
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
        throw new Error("Extra fields must be a JSON object, e.g. {\"key\": \"value\"}.");
    }
    return value;
}

function fillStatusSelect(select, selected) {
    select.innerHTML = Object.entries(STATUS_LABELS)
        .map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
    if (selected) select.value = selected;
}

function showError(el, message) {
    el.textContent = message ?? "";
    el.hidden = !message;
}

function toast(message, kind = "success") {
    const el = document.createElement("div");
    el.className = `toast is-${kind}`;
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    el.innerHTML = `${icon(kind === "error" ? "i-alert" : "i-check-circle")}<span></span>`;
    el.querySelector("span").textContent = message;
    $("toast-region").appendChild(el);
    setTimeout(() => {
        el.classList.add("is-leaving");
        setTimeout(() => el.remove(), 300);
    }, kind === "error" ? 5000 : 3000);
}

document.addEventListener("click", (e) => {
    const closer = e.target.closest("[data-close]");
    if (closer) closer.closest("dialog")?.close();
});

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------
function router() {
    const [section, id] = location.hash.replace(/^#\/?/, "").split("/");
    if (section === "settings") {
        openSettings();
        return;
    }
    const ticketId = section === "tickets" && id ? Number(id) : null;
    selectTicket(ticketId);
}

window.addEventListener("hashchange", router);

// Closing Settings goes back to wherever you were.
$("sheet-settings").addEventListener("close", () => {
    if (location.hash === "#/settings") {
        history.replaceState(null, "", selectedId ? `#/tickets/${selectedId}` : "#/tickets");
    }
});

// ---------------------------------------------------------------------------
// Ticket list
// ---------------------------------------------------------------------------
async function loadTickets() {
    tickets = await api("tickets");
    if (firstLoad) {
        for (const t of tickets) lastSeen[t.id] = t.updated_at;
        firstLoad = false;
    }
    drawTickets();
}

function drawTickets() {
    const list = $("ticket-list");
    const query = $("search").value.trim().toLowerCase();
    const items = tickets.filter((t) => (!originFilter || t.origin === originFilter)
        && (!query || (t.subject ?? "").toLowerCase().includes(query) || (t.ref ?? "").toLowerCase().includes(query)
            || (t.counterpart ?? "").toLowerCase().includes(query)));

    if (items.length === 0) {
        list.innerHTML = tickets.length === 0
            ? `<div class="list-empty"><p class="empty-title">No tickets yet</p>
                 <p>Start one with the compose button, or wait for another system to send one on a topic this system subscribes to.</p></div>`
            : `<div class="list-empty"><p class="empty-title">No matches</p><p>Try another search or filter.</p></div>`;
        return;
    }

    list.innerHTML = items.map((t) => {
        const unseen = t.id !== selectedId && lastSeen[t.id] !== t.updated_at;
        const who = t.origin === "sent" ? "Opened here" : `From ${t.counterpart ?? "another system"}`;
        return `
            <button type="button" class="ticket-row" role="listitem" data-ticket="${t.id}" aria-current="${t.id === selectedId}">
                ${unseen ? `<span class="unread" aria-label="New activity"></span>` : ""}
                <span class="avatar ${tintFor(t.counterpart ?? t.ref)}" aria-hidden="true">${escapeHtml(initials(t.counterpart ?? config.system_code))}</span>
                <span class="ticket-main">
                    <span class="ticket-top">
                        <span class="ticket-subject">${escapeHtml(t.subject || "No subject")}</span>
                        <span class="ticket-time">${escapeHtml(formatRelative(t.updated_at))}</span>
                    </span>
                    <span class="ticket-sub"><span>${escapeHtml(t.ref)} – ${escapeHtml(who)}</span></span>
                    <span class="ticket-sub">${statusPill(t.status)}</span>
                </span>
            </button>`;
    }).join("");
    list.querySelectorAll("[data-ticket]").forEach((row) => {
        row.addEventListener("click", () => { location.hash = `#/tickets/${row.dataset.ticket}`; });
    });
}

$("search").addEventListener("input", drawTickets);
document.querySelectorAll("#origin-filter > button").forEach((b) => {
    b.addEventListener("click", () => {
        originFilter = b.dataset.value;
        document.querySelectorAll("#origin-filter > button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
        drawTickets();
    });
});

// ---------------------------------------------------------------------------
// Conversation
// ---------------------------------------------------------------------------
const composer = $("composer");
fillStatusSelect(composer.status, "in_progress");

async function selectTicket(id) {
    const changed = id !== selectedId;
    selectedId = id;
    $("app").classList.toggle("is-showing-detail", id !== null);
    $("detail-empty").hidden = id !== null;
    $("conversation").hidden = id === null;
    if (changed) {
        lastThreadKey = "";
        composer.reset();
        showError(composer.querySelector(".composer-error"), null);
        setExtraOpen(false);
    }
    if (id !== null) await loadConversation({ scrollToEnd: true, setStatus: changed });
    drawTickets();
}

async function loadConversation({ scrollToEnd = false, setStatus = false } = {}) {
    if (selectedId === null) return;
    let ticket;
    try {
        ticket = await api(`tickets/${selectedId}`);
    } catch {
        location.hash = "#/tickets";
        return;
    }
    lastSeen[ticket.id] = ticket.updated_at;

    $("conv-title").textContent = ticket.subject || "No subject";
    $("conv-meta").textContent = [
        ticket.ref,
        ticket.origin === "sent" ? "opened here" : `from ${ticket.counterpart ?? "another system"}`,
        ticket.topic_code,
    ].filter(Boolean).join(", ");
    $("conv-status").innerHTML = statusPill(ticket.status);
    $("conv-unlinked").hidden = ticket.linked;
    document.title = `${ticket.subject || ticket.ref} – Simulator`;
    if (setStatus) fillStatusSelect(composer.status, ticket.status === "new" ? "in_progress" : ticket.status);

    const key = `${ticket.id}:${ticket.messages.length}:${ticket.updated_at}`;
    if (key === lastThreadKey) return;
    lastThreadKey = key;

    const scroller = $("thread-scroll");
    const atEnd = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 40;
    const thread = $("thread");
    thread.innerHTML = "";
    let lastDay = "";
    for (const m of ticket.messages) {
        const day = formatDay(m.created_at);
        if (day !== lastDay) {
            const d = document.createElement("li");
            d.className = "thread-day";
            d.textContent = day;
            thread.appendChild(d);
            lastDay = day;
        }
        thread.appendChild(messageElement(m));
    }
    if (scrollToEnd || atEnd) scroller.scrollTop = scroller.scrollHeight;
}

function messageElement(m) {
    const { note, ...fields } = m.metadata ?? {};
    const li = document.createElement("li");
    li.className = `msg ${m.direction}`;
    const sender = m.direction === "out" ? "You" : (m.system ?? "Bridge");
    li.innerHTML = `
        <span class="msg-meta"><strong></strong> ${escapeHtml(formatRelative(m.created_at))}</span>
        <div class="bubble">
            <div class="bubble-status">Status ${statusPill(m.status)}</div>
        </div>
        ${m.event ? `<span class="msg-event">${m.event === "ticket.created" ? "Delivered as a new ticket" : "Delivered as an update"}</span>` : ""}`;
    li.querySelector(".msg-meta strong").textContent = sender;
    const bubble = li.querySelector(".bubble");
    if (note !== undefined) {
        const p = document.createElement("p");
        p.className = "bubble-note";
        p.textContent = String(note);
        bubble.appendChild(p);
    }
    const entries = Object.entries(fields);
    if (entries.length) {
        const dl = document.createElement("dl");
        dl.className = "bubble-fields";
        for (const [k, v] of entries) {
            const dt = document.createElement("dt");
            dt.textContent = k.replaceAll("_", " ");
            const dd = document.createElement("dd");
            dd.textContent = typeof v === "string" ? v : JSON.stringify(v);
            dl.append(dt, dd);
        }
        bubble.appendChild(dl);
    }
    return li;
}

// Composer: Enter sends, Shift+Enter adds a line, the note grows as you type.
function setExtraOpen(open) {
    $("composer-extra").hidden = !open;
    $("btn-composer-extra").setAttribute("aria-expanded", String(open));
    if (open) $("composer-metadata").focus();
}
$("btn-composer-extra").addEventListener("click", () => setExtraOpen($("composer-extra").hidden));
composer.note.addEventListener("input", () => {
    composer.note.style.height = "auto";
    composer.note.style.height = `${composer.note.scrollHeight}px`;
});
composer.note.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        composer.requestSubmit();
    }
});

composer.addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorEl = composer.querySelector(".composer-error");
    const button = composer.querySelector(".send-btn");
    showError(errorEl, null);
    try {
        const body = {
            status: composer.status.value,
            note: composer.note.value.trim() || null,
            metadata: parseMetadata($("composer-metadata").value),
        };
        button.disabled = true;
        await api(`tickets/${selectedId}/reply`, { method: "POST", body: JSON.stringify(body) });
        composer.note.value = "";
        composer.note.style.height = "";
        $("composer-metadata").value = "";
        setExtraOpen(false);
        await loadConversation({ scrollToEnd: true });
        await loadTickets();
    } catch (err) {
        showError(errorEl, err.message);
    } finally {
        button.disabled = false;
    }
});

// ---------------------------------------------------------------------------
// New ticket
// ---------------------------------------------------------------------------
const newSheet = $("sheet-new-ticket");
const newForm = $("form-new-ticket");

function openNewTicket() {
    newForm.reset();
    fillStatusSelect(newForm.status, "new");
    newForm.topic_code.value = config.default_topic ?? "";
    showError(newForm.querySelector(".form-error"), null);
    newSheet.showModal();
    (newForm.topic_code.value ? newForm.subject : newForm.topic_code).focus();
}
$("btn-new-ticket").addEventListener("click", openNewTicket);
$("btn-empty-new").addEventListener("click", openNewTicket);

newForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorEl = newForm.querySelector(".form-error");
    const button = newForm.querySelector("[type=submit]");
    showError(errorEl, null);
    try {
        if (!newForm.topic_code.value.trim() || !newForm.subject.value.trim()) {
            throw new Error("Enter a topic and a subject.");
        }
        const body = {
            topic_code: newForm.topic_code.value.trim(),
            subject: newForm.subject.value.trim(),
            status: newForm.status.value,
            note: newForm.note.value.trim() || null,
            metadata: parseMetadata(newForm.metadata.value),
        };
        button.disabled = true;
        const ticket = await api("tickets", { method: "POST", body: JSON.stringify(body) });
        newSheet.close();
        toast(`Sent ${ticket.ref} to the bridge`);
        await loadTickets();
        location.hash = `#/tickets/${ticket.id}`;
    } catch (err) {
        showError(errorEl, err.message);
    } finally {
        button.disabled = false;
    }
});

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
const configForm = $("form-config");
let deliveryMode = "push";

function setDeliveryMode(mode) {
    deliveryMode = mode;
    document.querySelectorAll("#delivery-mode > button").forEach((b) => {
        b.setAttribute("aria-checked", String(b.dataset.value === mode));
    });
    $("push-settings").hidden = mode !== "push";
    $("pull-settings").hidden = mode !== "pull";
}
document.querySelectorAll("#delivery-mode > button").forEach((b) => {
    b.addEventListener("click", () => setDeliveryMode(b.dataset.value));
});

function renderInboxStatus() {
    const inbox = config.inbox ?? {};
    const last = $("inbox-last");
    if (inbox.last_error) {
        last.textContent = "Failed";
        $("inbox-hint").textContent = inbox.last_error;
        $("inbox-hint").className = "group-footer is-error";
        return;
    }
    last.textContent = inbox.last_collected_at
        ? `${formatRelative(inbox.last_collected_at)}, ${inbox.last_count} new`
        : "Not yet";
    $("inbox-hint").innerHTML = `Like a system the bridge can't reach: every ${escapeHtml(inbox.poll_seconds ?? 5)} s it reads its messages from the bridge's inbox (<code>GET /api/v1/inbox</code>) with its API key and confirms each one. In the bridge, set this system to "It collects them".`;
    $("inbox-hint").className = "group-footer";
}

async function loadConfig() {
    config = await api("config");
    renderIdentity();
}

function renderIdentity() {
    $("identity-name").textContent = config.system_code || "No system code";
    $("identity-bridge").textContent = config.bridge_url
        ? `${config.delivery_mode === "pull" ? "Collects from" : "Bridge at"} ${config.bridge_url}`
        : "No bridge set";
    $("identity-avatar").textContent = initials(config.system_code || "S");
}

async function openSettings() {
    await loadConfig();
    for (const name of ["bridge_url", "system_code", "ref_prefix", "default_topic", "inbound_header"]) {
        configForm[name].value = config[name] ?? "";
    }
    // Secrets are never returned - only whether one is stored.
    configForm.api_key.value = "";
    configForm.api_key.placeholder = config.api_key_set ? "Stored. Leave empty to keep it" : "Not set";
    configForm.inbound_secret.value = "";
    configForm.inbound_secret.placeholder = config.inbound_secret_set
        ? "Stored. Leave empty to keep it"
        : "None, every message is accepted";
    configForm.clear_inbound_secret.checked = false;
    $("clear-inbound-secret-field").hidden = !config.inbound_secret_set;
    setDeliveryMode(config.delivery_mode || "push");
    renderInboxStatus();
    $("webhook-url").textContent = new URL("webhook", location.href.split("#")[0]).href;
    $("ref-example").textContent = `${config.ref_prefix || "SIM"}-0001`;
    resetTestResult();
    showError(configForm.querySelector(".form-error"), null);
    if (!$("sheet-settings").open) $("sheet-settings").showModal();
}

$("btn-identity").addEventListener("click", () => { location.hash = "#/settings"; });
configForm.ref_prefix.addEventListener("input", () => {
    $("ref-example").textContent = `${configForm.ref_prefix.value.trim() || "SIM"}-0001`;
});

configForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorEl = configForm.querySelector(".form-error");
    showError(errorEl, null);
    if (!configForm.bridge_url.value.trim() || !configForm.ref_prefix.value.trim()) {
        showError(errorEl, "Enter the bridge URL and a ticket number prefix.");
        return;
    }
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
                delivery_mode: deliveryMode,
            }),
        });
        renderIdentity();
        $("sheet-settings").close();
        toast("Settings saved");
    } catch (err) {
        showError(errorEl, err.message);
    }
});

const TEST_HINT = $("test-result").textContent;
function resetTestResult() {
    $("test-result").textContent = TEST_HINT;
    $("test-result").className = "group-footer";
}

$("btn-test").addEventListener("click", async (e) => {
    const out = $("test-result");
    const button = e.currentTarget;
    button.disabled = true;
    out.className = "group-footer";
    out.textContent = "Testing…";
    try {
        await api("config/test", { method: "POST" });
        out.textContent = "The bridge is reachable. The API key is checked when you send a ticket.";
        out.classList.add("is-ok");
    } catch (err) {
        out.textContent = err.message;
        out.classList.add("is-error");
    } finally {
        button.disabled = false;
    }
});

$("btn-collect").addEventListener("click", async (e) => {
    const button = e.currentTarget;
    button.disabled = true;
    try {
        const result = await api("inbox/collect", { method: "POST" });
        toast(result.collected ? `Collected ${result.collected} new ${result.collected === 1 ? "message" : "messages"}` : "No new messages");
        await loadTickets();
    } catch (err) {
        toast(err.message, "error");
    } finally {
        config = await api("config");
        renderInboxStatus();
        button.disabled = false;
    }
});

$("btn-copy-webhook").addEventListener("click", async () => {
    try {
        await navigator.clipboard.writeText($("webhook-url").textContent);
        toast("Webhook URL copied");
    } catch {
        toast("Couldn't copy. Select the URL and copy it manually.", "error");
    }
});

// ---------------------------------------------------------------------------
// Live updates and startup
// ---------------------------------------------------------------------------
setInterval(() => {
    if ($("auto-refresh").checked && !document.hidden) {
        loadTickets().catch(() => {});
        loadConversation().catch(() => {});
    }
}, POLL_MS);

(async function start() {
    await loadConfig();
    await loadTickets();
    router();
})();
