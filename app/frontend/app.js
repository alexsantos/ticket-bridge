/**
 * app.js
 * ------
 * Vanilla JavaScript (no framework/build step - CLAUDE.md Decision 7) for
 * the Ticket Bridge console. Sections are hash routes (see router()), so
 * the browser's Back button, reloads and shared links all land on the
 * same screen:
 *
 *   #/overview                      what needs attention + setup checklist
 *   #/conversations[/<id>]          list, and one conversation as a thread
 *   #/deliveries?status=&to=        outbox entries; payload sheet; retry
 *   #/setup/systems | /setup/topics configuration (sheets for editing)
 *   #/activity?system=&conversation= the audit log in plain language
 *
 * Every admin endpoint needs a session (an httpOnly cookie - see
 * app/api/auth.py). Any 401 - on load (start() calls GET api/v1/auth/me)
 * or mid-session, once the session expires or is revoked - redirects to
 * login.html, via the fetch() wrapper below. See README.md,
 * "Configuration frontend security".
 *
 * Security rule for this file: every value interpolated into an innerHTML
 * template goes through escapeHtml() - subjects, refs, metadata, errors
 * and payloads are sent by external systems.
 */

const API_BASE = "api/v1";

// Wraps window.fetch so every call site gets the session-expiry redirect
// for free. On a 401 the returned promise never settles: the page is
// navigating away, and this stops callers from rendering the error body.
const nativeFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
    const resp = await nativeFetch(...args);
    if (resp.status === 401) {
        window.location.href = "login.html";
        return new Promise(() => {});
    }
    return resp;
};

// ---------------------------------------------------------------------------
// Shared state
// ---------------------------------------------------------------------------
const state = {
    systems: [],
    topics: [],
    systemsByCode: new Map(),
    topicsByCode: new Map(),
    failedCount: 0,
    failedMore: false,
};

const CONVERSATION_STATUS = {
    new: { label: "New", pill: "pill-blue" },
    in_progress: { label: "In progress", pill: "pill-orange" },
    waiting_third_party: { label: "Waiting on third party", pill: "pill-purple" },
    resolved: { label: "Resolved", pill: "pill-green" },
    closed: { label: "Closed", pill: "" },
};

const DELIVERY_STATUS = {
    sent: { label: "Delivered", icon: "i-check-circle", cls: "is-sent", hero: "hero-icon-green" },
    pending: { label: "Waiting to send", icon: "i-clock", cls: "is-pending", hero: "hero-icon-orange" },
    failed: { label: "Couldn't deliver", icon: "i-alert", cls: "is-failed", hero: "hero-icon-red" },
};

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function icon(name, cls = "") {
    return `<svg class="icon ${cls}" aria-hidden="true"><use href="#${name}"/></svg>`;
}
const CHEVRON = icon("i-chevron", "chevron");

function formatDateTime(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// "14:05" today, "Yesterday", weekday within a week, otherwise a short date
// - the way Messages and Mail label rows.
function formatRelative(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    const now = new Date();
    const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const days = Math.round((startOfDay(now) - startOfDay(date)) / 86400000);
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
    const words = String(text ?? "?").replace(/[^\p{L}\p{N}]+/gu, " ").trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) return "?";
    const letters = words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2);
    return letters.toUpperCase();
}

// Stable colour per system/topic code, so the same system always has the
// same avatar colour across lists and threads.
function tintFor(code) {
    let hash = 0;
    for (const ch of String(code)) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
    return `tint-${hash % 8}`;
}

function systemName(code) {
    return state.systemsByCode.get(code)?.name ?? code ?? "Unknown system";
}
function topicName(code) {
    return state.topicsByCode.get(code)?.name ?? code;
}

function avatar(code, label) {
    return `<span class="avatar ${tintFor(code)}" aria-hidden="true">${escapeHtml(initials(label ?? code))}</span>`;
}

function statusPill(status) {
    const s = CONVERSATION_STATUS[status] ?? { label: status, pill: "" };
    return `<span class="pill ${s.pill}">${escapeHtml(s.label)}</span>`;
}

function eventLabel(event) {
    return event === "ticket.created" ? "New ticket" : event === "ticket.updated" ? "Update" : (event ?? "Message");
}

// Pull systems collect their messages from the bridge's inbox instead of a
// webhook (Decision 18); until they do, their messages wait - not "to
// send" but "to be collected".
const STALE_POLL_MINUTES = 15;

function isPull(code) {
    return state.systemsByCode.get(code)?.delivery_mode === "pull";
}

function waitingLabel(d) {
    return isPull(d.destination) ? `Waiting for ${systemName(d.destination)} to collect it` : "Waiting to send";
}

// An active pull system that hasn't called its inbox recently (or ever).
function isStalePuller(s) {
    if (s.delivery_mode !== "pull" || !s.active) return false;
    if (!s.last_polled_at) return true;
    return Date.now() - new Date(s.last_polled_at).getTime() > STALE_POLL_MINUTES * 60000;
}

// Archived systems are kept for history (names in threads, deliveries and
// activity) but left out of lists, filters and the checklist (Decision 19).
function currentSystems() {
    return state.systems.filter((s) => !s.archived_at);
}

function hostOf(url) {
    try { return new URL(url).host; } catch { return url; }
}

function plural(n, one, many = `${one}s`) {
    return `${n} ${n === 1 ? one : many}`;
}

async function api(path, options = {}) {
    const init = { ...options };
    if (options.json !== undefined) {
        init.method = init.method ?? "POST";
        init.headers = { "Content-Type": "application/json", ...(options.headers ?? {}) };
        init.body = JSON.stringify(options.json);
        delete init.json;
    }
    const resp = await fetch(`${API_BASE}/${path}`, init);
    const data = resp.status === 204 ? null : await resp.json().catch(() => null);
    return { ok: resp.ok, status: resp.status, data };
}

// A duplicate code comes back from the database as a constraint error;
// say it the way a person would.
function isDuplicateCode(result) {
    return result.status === 409 && /duplicate key|already exists/i.test(result.data?.detail ?? "");
}

function errorText(result, fallback) {
    const detail = result.data?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail[0]?.msg) {
        const field = detail[0].loc?.slice(-1)[0];
        return field ? `${field}: ${detail[0].msg}` : detail[0].msg;
    }
    return `${fallback} (HTTP ${result.status}).`;
}

// Toast: short confirmation of what just happened, in the same words as
// the action ("Save" -> "Saved").
function toast(message, kind = "success") {
    const region = $("toast-region");
    const el = document.createElement("div");
    el.className = `toast is-${kind}`;
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    el.innerHTML = `${icon(kind === "error" ? "i-alert" : "i-check-circle")}<span></span>`;
    el.querySelector("span").textContent = message;
    region.appendChild(el);
    setTimeout(() => {
        el.classList.add("is-leaving");
        setTimeout(() => el.remove(), 300);
    }, kind === "error" ? 5000 : 3200);
}

// iOS-style alert instead of window.confirm(). Resolves true/false.
function confirmAlert({ title, message, confirmLabel, destructive = false }) {
    const dialog = $("alert-confirm");
    $("alert-confirm-title").textContent = title;
    $("alert-confirm-message").textContent = message;
    const ok = $("alert-confirm-ok");
    ok.textContent = confirmLabel;
    ok.classList.toggle("is-destructive", destructive);
    return new Promise((resolve) => {
        const finish = (value) => {
            dialog.close();
            ok.onclick = null;
            $("alert-confirm-cancel").onclick = null;
            resolve(value);
        };
        ok.onclick = () => finish(true);
        $("alert-confirm-cancel").onclick = () => finish(false);
        dialog.oncancel = (e) => { e.preventDefault(); finish(false); };
        dialog.showModal();
        $("alert-confirm-cancel").focus();
    });
}

// Runs `task` while `button` shows it's busy (disabled; a spinning icon
// for icon buttons), so a slow request never looks like a dead click.
async function busy(button, task) {
    if (!button) return task();
    button.disabled = true;
    button.classList.add("is-busy");
    try {
        return await task();
    } finally {
        button.disabled = false;
        button.classList.remove("is-busy");
    }
}

function openSheet(id) {
    const dialog = $(id);
    if (!dialog.open) dialog.showModal();
    dialog.querySelector(".sheet-body")?.scrollTo(0, 0);
}

document.addEventListener("click", (e) => {
    const closer = e.target.closest("[data-close]");
    if (closer) closer.closest("dialog")?.close();
});

// Read-only sheets also close when the backdrop is clicked; editing
// sheets don't, so a stray click never throws away typed changes.
["sheet-delivery", "sheet-account"].forEach((id) => {
    $(id).addEventListener("click", (e) => {
        if (e.target === e.currentTarget) e.currentTarget.close();
    });
});

function emptyState({ iconName, title, text, actionLabel, actionId }) {
    return `
        <div class="empty">
            ${icon(iconName)}
            <p class="empty-title">${escapeHtml(title)}</p>
            <p class="empty-text">${escapeHtml(text)}</p>
            ${actionLabel ? `<button type="button" class="btn btn-filled" id="${actionId}">${escapeHtml(actionLabel)}</button>` : ""}
        </div>`;
}

function setEmpty(listEl, options, onAction) {
    listEl.innerHTML = "";
    const holder = document.createElement("div");
    holder.className = "empty-holder";
    holder.innerHTML = emptyState(options);
    listEl.after(holder);
    if (onAction && options.actionId) holder.querySelector(`#${options.actionId}`).addEventListener("click", onAction);
}

function clearEmpty(listEl) {
    if (listEl.nextElementSibling?.classList.contains("empty-holder")) listEl.nextElementSibling.remove();
}

// ---------------------------------------------------------------------------
// Reference data (systems, topics) used by every section
// ---------------------------------------------------------------------------
async function loadReference() {
    const [systems, topics] = await Promise.all([api("systems"), api("topics")]);
    state.systems = systems.data ?? [];
    state.topics = topics.data ?? [];
    state.systemsByCode = new Map(state.systems.map((s) => [s.code, s]));
    state.topicsByCode = new Map(state.topics.map((t) => [t.code, t]));
    fillSelect($("filter-conversations-system"), currentSystems(), "All systems");
    fillSelect($("filter-conversations-topic"), state.topics, "All topics");
    fillSelect($("filter-deliveries-system"), currentSystems(), "All destinations");
    fillSelect($("filter-activity-system"), currentSystems(), "All systems");
}

function fillSelect(select, items, allLabel) {
    const selected = select.value;
    select.innerHTML = "";
    select.append(new Option(allLabel, ""));
    for (const item of items) select.append(new Option(item.name, item.code));
    select.value = selected;
    if (select.value !== selected) select.value = "";
}

async function refreshFailedBadge() {
    const result = await api("deliveries?status=failed&limit=100");
    if (!result.ok) return;
    state.failedCount = result.data.items.length;
    state.failedMore = result.data.has_more;
    const badge = $("nav-badge-failed");
    badge.textContent = state.failedMore ? "99+" : String(state.failedCount);
    badge.hidden = state.failedCount === 0;
    badge.title = `${plural(state.failedCount, "failed delivery", "failed deliveries")}`;
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------
function parseHash() {
    const raw = location.hash.replace(/^#\/?/, "");
    const [path, query = ""] = raw.split("?");
    return { parts: path.split("/").filter(Boolean), params: new URLSearchParams(query) };
}

function showView(viewId, section) {
    document.querySelectorAll(".view").forEach((v) => { v.hidden = v.id !== viewId; });
    document.querySelectorAll(".nav-item").forEach((a) => {
        if (a.dataset.section === section) a.setAttribute("aria-current", "page");
        else a.removeAttribute("aria-current");
    });
}

let currentRender = null;

async function router() {
    const { parts, params } = parseHash();
    const [section, sub] = parts;
    window.scrollTo(0, 0);

    if (section === "conversations" && sub) {
        showView("view-conversation", "conversations");
        currentRender = () => renderConversation(sub);
    } else if (section === "conversations") {
        showView("view-conversations", "conversations");
        currentRender = renderConversations;
    } else if (section === "deliveries") {
        showView("view-deliveries", "deliveries");
        currentRender = () => renderDeliveries(params);
    } else if (section === "setup") {
        showView("view-setup", "setup");
        currentRender = () => renderSetup(sub === "topics" ? "topics" : "systems");
    } else if (section === "activity") {
        showView("view-activity", "activity");
        currentRender = () => renderActivity(params);
    } else {
        if (section !== "overview") history.replaceState(null, "", "#/overview");
        showView("view-overview", "overview");
        currentRender = renderOverview;
    }
    document.title = `${document.querySelector(".view:not([hidden]) .large-title")?.textContent ?? "Conversation"} – Ticket Bridge`;
    await currentRender();
}

window.addEventListener("hashchange", router);

// Toolbar actions shared by several views
document.addEventListener("click", async (e) => {
    const button = e.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (action === "refresh") {
        await busy(button, async () => {
            await loadReference();
            await refreshFailedBadge();
            await currentRender?.();
        });
    } else if (action === "sync") {
        await busy(button, sendPendingNow);
    } else if (action === "account") {
        openAccount();
    }
});

// "Send pending now": POST /api/v1/sync (accepts the admin session).
async function sendPendingNow() {
    const result = await api("sync", { method: "POST" });
    if (!result.ok) {
        toast(errorText(result, "Couldn't send"), "error");
        return;
    }
    const { processed, success, failures } = result.data;
    if (processed === 0) toast("Nothing was waiting to send");
    else if (failures === 0) toast(`Sent ${plural(success, "message")}`);
    else toast(`${success} delivered, ${failures} failed`, "error");
    await refreshFailedBadge();
    await currentRender?.();
}

// Retry = put the chosen failed deliveries back in the queue and send
// them right away, then say what actually happened to them.
async function retryDeliveries(ids) {
    const requeued = await api("deliveries/retry", { json: { outbox_ids: ids } });
    if (!requeued.ok) {
        toast(errorText(requeued, "Couldn't retry"), "error");
        return;
    }
    const retried = new Set(requeued.data.retried);
    if (retried.size === 0) {
        toast("Those deliveries had already been retried", "error");
    } else {
        const sync = await api("sync", { method: "POST" });
        const mine = (sync.data?.detail ?? []).filter((d) => retried.has(d.outbox_id));
        const delivered = mine.filter((d) => d.result === "success").length;
        const failedAgain = mine.length - delivered;
        const waiting = retried.size - mine.length;
        if (failedAgain === 0 && waiting === 0) toast(`Delivered ${plural(delivered, "message")}`);
        else if (failedAgain > 0) toast(`${delivered} delivered, ${failedAgain} failed again`, "error");
        else toast(`${delivered} delivered, ${waiting} more waiting to send`);
    }
    await refreshFailedBadge();
    await currentRender?.();
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------
async function renderOverview() {
    const [pending, recent] = await Promise.all([
        api("deliveries?status=pending&limit=100"),
        api("conversations?limit=5"),
    ]);

    // What needs attention
    const attention = $("overview-attention");
    attention.innerHTML = "";
    if (state.failedCount > 0) {
        const count = state.failedMore ? "More than 100" : String(state.failedCount);
        attention.insertAdjacentHTML("beforeend", `
            <a class="attention attention-red" href="#/deliveries?status=failed">
                <span class="tile tile-red">${icon("i-alert")}</span>
                <span class="row-main">
                    <span class="attention-title">${escapeHtml(count)} ${state.failedCount === 1 ? "message couldn't" : "messages couldn't"} be delivered</span>
                    <span class="row-subtitle wrap">The bridge gave up after several attempts. Fix the cause, then retry them.</span>
                </span>
                <span class="row-trailing"><span>Review</span>${CHEVRON}</span>
            </a>`);
    }
    for (const s of state.systems.filter(isStalePuller)) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "attention";
        btn.innerHTML = `
            <span class="tile tile-orange">${icon("i-clock")}</span>
            <span class="row-main">
                <span class="attention-title"></span>
                <span class="row-subtitle wrap"></span>
            </span>
            <span class="row-trailing"><span>Open</span>${CHEVRON}</span>`;
        btn.querySelector(".attention-title").textContent = s.last_polled_at
            ? `${s.name} stopped collecting its messages`
            : `${s.name} hasn't collected any messages yet`;
        btn.querySelector(".row-subtitle").textContent = s.last_polled_at
            ? `Last collected ${formatDateTime(s.last_polled_at)}. Its messages wait in the bridge until it does.`
            : "It collects them from the bridge's inbox with its own key. Check that its integration is running.";
        btn.addEventListener("click", () => openSystemSheet(s));
        attention.appendChild(btn);
    }
    for (const s of state.systems.filter((x) => x.has_legacy_secret_ref)) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "attention";
        btn.innerHTML = `
            <span class="tile tile-orange">${icon("i-lock")}</span>
            <span class="row-main">
                <span class="attention-title"></span>
                <span class="row-subtitle wrap">It still points to a secret from an older version, so messages to it fail.</span>
            </span>
            <span class="row-trailing"><span>Fix</span>${CHEVRON}</span>`;
        btn.querySelector(".attention-title").textContent = `${s.name} needs its secret`;
        btn.addEventListener("click", () => openSystemSheet(s));
        attention.appendChild(btn);
    }

    await renderSetupChecklist();

    // Deliveries summary
    const pendingCount = pending.data?.items.length ?? 0;
    const pendingLabel = pending.data?.has_more ? "99+" : String(pendingCount);
    $("overview-deliveries").innerHTML = `
        <a class="row has-leading" href="#/deliveries?status=failed">
            <span class="tile tile-red">${icon("i-alert")}</span>
            <span class="row-main"><span class="row-title">Couldn't deliver</span></span>
            <span class="row-trailing"><span class="row-count ${state.failedCount ? "is-alert" : ""}">${state.failedMore ? "99+" : state.failedCount}</span>${CHEVRON}</span>
        </a>
        <a class="row has-leading" href="#/deliveries?status=pending">
            <span class="tile tile-orange">${icon("i-clock")}</span>
            <span class="row-main"><span class="row-title">Waiting</span></span>
            <span class="row-trailing"><span class="row-count">${pendingLabel}</span>${CHEVRON}</span>
        </a>
        <a class="row has-leading" href="#/deliveries?status=sent">
            <span class="tile tile-green">${icon("i-check")}</span>
            <span class="row-main"><span class="row-title">Delivered</span></span>
            <span class="row-trailing">${CHEVRON}</span>
        </a>`;

    // Recent conversations
    const list = $("overview-conversations");
    clearEmpty(list);
    const conversations = recent.data ?? [];
    if (conversations.length === 0) {
        setEmpty(list, {
            iconName: "i-conversations",
            title: "No conversations yet",
            text: "A conversation starts when a system sends its first message to the bridge.",
        });
    } else {
        list.innerHTML = conversations.map(conversationRow).join("");
    }
}

// Setup checklist: the four things every system needs, in the order they
// have to happen. Hidden once all are done.
async function renderSetupChecklist() {
    const active = state.systems.filter((s) => s.active);
    const withoutTopics = active.filter((s) => s.topics.length === 0);
    let withoutKey = [];
    if (active.length) {
        const keys = await Promise.all(active.map((s) => api(`systems/${encodeURIComponent(s.code)}/api-keys`)));
        withoutKey = active.filter((s, i) => !(keys[i].data ?? []).some((k) => k.active));
    }
    const steps = [
        {
            done: state.topics.length > 0,
            title: "Create a topic",
            text: "The kind of work conversations are about, like Infrastructure.",
            run: () => openTopicSheet(null),
        },
        {
            done: currentSystems().length >= 2,
            title: "Add the systems that exchange tickets",
            text: "At least two, usually one per team's support application.",
            run: () => openSystemSheet(null),
        },
        {
            done: active.length > 0 && withoutTopics.length === 0,
            title: "Subscribe each system to its topics",
            text: withoutTopics.length ? `${withoutTopics.map((s) => s.name).join(", ")} ${withoutTopics.length === 1 ? "has" : "have"} no topics yet.` : "So it receives the conversations it should.",
            run: () => openSystemSheet(withoutTopics[0] ?? null),
        },
        {
            done: active.length > 0 && withoutKey.length === 0,
            title: "Give each system a key to send with",
            text: withoutKey.length ? `${withoutKey.map((s) => s.name).join(", ")} can't send messages yet.` : "Generated from the system's settings, shown once.",
            run: () => openSystemSheet(withoutKey[0] ?? null),
        },
    ];

    const group = $("overview-setup");
    group.hidden = steps.every((s) => s.done);
    const list = $("overview-setup-steps");
    list.innerHTML = "";
    for (const step of steps) {
        const li = document.createElement("li");
        li.className = `setup-step ${step.done ? "is-done" : ""}`;
        li.innerHTML = `
            <${step.done ? "div" : "button type=\"button\""} class="row has-leading">
                <span class="step-marker" aria-hidden="true"></span>
                <span class="row-main">
                    <span class="row-title wrap"></span>
                    <span class="row-subtitle wrap"></span>
                </span>
                ${step.done ? `<span class="visually-hidden">Done</span>` : `<span class="row-trailing">${CHEVRON}</span>`}
            </${step.done ? "div" : "button"}>`;
        li.querySelector(".row-title").textContent = step.title;
        li.querySelector(".row-subtitle").textContent = step.text;
        if (!step.done) li.querySelector("button").addEventListener("click", step.run);
        list.appendChild(li);
    }
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------
let conversationsCache = [];

function conversationRow(c) {
    const refs = c.participants.map((p) => `${systemName(p.system_code)} (${p.external_ref})`).join(", ");
    return `
        <a class="row has-leading" href="#/conversations/${encodeURIComponent(c.conversation_id)}">
            ${avatar(c.topic_code, topicName(c.topic_code))}
            <span class="row-main">
                <span class="row-title">${escapeHtml(c.subject || "Untitled conversation")}</span>
                <span class="row-subtitle">${escapeHtml(topicName(c.topic_code))}${refs ? ` – ${escapeHtml(refs)}` : ""}</span>
            </span>
            <span class="row-trailing">
                ${statusPill(c.overall_status)}
                <span class="row-time">${escapeHtml(formatRelative(c.updated_at))}</span>
                ${CHEVRON}
            </span>
        </a>`;
}

async function renderConversations() {
    const params = new URLSearchParams({ limit: 200 });
    const system = $("filter-conversations-system").value;
    const topic = $("filter-conversations-topic").value;
    if (system) params.set("system_code", system);
    if (topic) params.set("topic_code", topic);
    const result = await api(`conversations?${params}`);
    conversationsCache = result.data ?? [];
    drawConversations();
}

function drawConversations() {
    const list = $("list-conversations");
    clearEmpty(list);
    const query = $("conversations-search").value.trim().toLowerCase();
    const filtersOn = query || $("filter-conversations-system").value || $("filter-conversations-topic").value;
    const items = conversationsCache.filter((c) => !query
        || (c.subject ?? "").toLowerCase().includes(query)
        || c.conversation_id.startsWith(query)
        || c.participants.some((p) => p.external_ref.toLowerCase().includes(query)));

    if (items.length === 0) {
        list.innerHTML = "";
        if (filtersOn) {
            setEmpty(list, {
                iconName: "i-search",
                title: "No matching conversations",
                text: "Try another search, or show every system and topic.",
                actionLabel: "Clear filters",
                actionId: "btn-clear-conversation-filters",
            }, () => {
                $("conversations-search").value = "";
                $("filter-conversations-system").value = "";
                $("filter-conversations-topic").value = "";
                renderConversations();
            });
        } else {
            setEmpty(list, {
                iconName: "i-conversations",
                title: "No conversations yet",
                text: "A conversation starts when a system sends its first message to the bridge.",
            });
        }
    } else {
        list.innerHTML = items.map(conversationRow).join("");
    }
    $("conversations-footer").textContent = conversationsCache.length === 200
        ? "Showing the 200 most recently updated conversations. Filter by system or topic to narrow it down."
        : "";
}

$("conversations-search").addEventListener("input", drawConversations);
$("filter-conversations-system").addEventListener("change", renderConversations);
$("filter-conversations-topic").addEventListener("change", renderConversations);

// ---------------------------------------------------------------------------
// One conversation, as a thread
// ---------------------------------------------------------------------------
async function renderConversation(id) {
    const container = $("conversation-detail");
    $("conversation-activity-link").href = `#/activity?conversation=${encodeURIComponent(id)}`;
    const [conversation, audit, deliveries] = await Promise.all([
        api(`conversations/${encodeURIComponent(id)}`),
        api(`audit?conversation_id=${encodeURIComponent(id)}&limit=500`),
        api(`deliveries?conversation_id=${encodeURIComponent(id)}&limit=500`),
    ]);
    if (!conversation.ok) {
        container.innerHTML = emptyState({
            iconName: "i-conversations",
            title: "Conversation not found",
            text: "It may have been removed, or the link is incomplete.",
        });
        return;
    }
    const c = conversation.data;
    document.title = `${c.subject || "Conversation"} – Ticket Bridge`;

    const deliveriesById = new Map((deliveries.data?.items ?? []).map((d) => [d.id, d]));
    const shown = new Set();
    // Audit rows oldest first. Each message (event_received) is followed in
    // the same transaction by a fanout_scheduled row naming the outbox ids
    // it created - that's how a message is paired with its deliveries.
    const entries = (audit.data?.items ?? []).slice().sort((a, b) => a.id - b.id);
    const thread = [];
    for (const entry of entries) {
        if (entry.event_type === "conversation_created") {
            thread.push({ kind: "note", at: entry.created_at, text: `${systemName(entry.system_code)} started this conversation on ${topicName(entry.detail.topic_code ?? c.topic_code)}` });
        } else if (entry.event_type === "event_received") {
            thread.push({ kind: "message", at: entry.created_at, source: entry.system_code, detail: entry.detail, deliveries: [] });
        } else if (entry.event_type === "fanout_scheduled") {
            const message = [...thread].reverse().find((m) => m.kind === "message" && m.source === entry.system_code);
            for (const outboxId of entry.detail.outbox_ids ?? []) {
                const d = deliveriesById.get(outboxId);
                if (d && message) { message.deliveries.push(d); shown.add(d.id); }
            }
        }
    }
    const orphans = [...deliveriesById.values()].filter((d) => !shown.has(d.id));

    const participants = c.participants.map((p) => `
        <div class="row has-leading">
            ${avatar(p.system_code, systemName(p.system_code))}
            <span class="row-main">
                <span class="row-title">${escapeHtml(systemName(p.system_code))}</span>
                <span class="row-subtitle">Ticket ${escapeHtml(p.external_ref)}</span>
            </span>
            <span class="row-trailing">${p.local_status ? statusPill(p.local_status) : ""}</span>
        </div>`).join("");

    let lastDay = "";
    const threadHtml = thread.map((item) => {
        const day = formatDay(item.at);
        const dayHtml = day !== lastDay ? `<span class="thread-day">${escapeHtml(day)}</span>` : "";
        lastDay = day;
        if (item.kind === "note") return `${dayHtml}<span class="thread-day">${escapeHtml(item.text)}</span>`;
        return dayHtml + messageHtml(item);
    }).join("");

    container.innerHTML = `
        <div class="conversation-head">
            <h1 class="conversation-title">${escapeHtml(c.subject || "Untitled conversation")}</h1>
            <div class="conversation-tags">
                <span class="pill pill-blue">${icon("i-tag")}${escapeHtml(topicName(c.topic_code))}</span>
                ${statusPill(c.overall_status)}
                <button type="button" class="pill pill-mono" id="btn-copy-conversation-id" title="Copy the full conversation ID">${icon("i-copy")}${escapeHtml(c.conversation_id.slice(0, 8))}</button>
            </div>
        </div>

        <section class="group">
            <h2 class="group-title">Tickets in each system</h2>
            <div class="list">${participants}</div>
            ${participants ? "" : `<p class="group-footer">No system has linked its own ticket yet.</p>`}
        </section>

        <section class="group">
            <h2 class="group-title">Messages</h2>
            <div class="thread">${threadHtml || `<span class="thread-day">No messages recorded.</span>`}</div>
            <p class="group-footer">Each message shows where it went. Select a delivery to see exactly what was sent.</p>
        </section>

        ${orphans.length ? `
        <section class="group">
            <h2 class="group-title">Other deliveries</h2>
            <div class="list" id="conversation-orphans"></div>
        </section>` : ""}`;

    if (orphans.length) $("conversation-orphans").innerHTML = orphans.map(deliveryRow).join("");

    container.querySelectorAll("[data-delivery]").forEach((el) => {
        el.addEventListener("click", () => openDeliverySheet(deliveriesById.get(Number(el.dataset.delivery))));
    });
    container.querySelectorAll("[data-retry]").forEach((el) => {
        el.addEventListener("click", () => busy(el, () => retryDeliveries([Number(el.dataset.retry)])));
    });
    $("btn-copy-conversation-id").addEventListener("click", () => copyText(c.conversation_id, "Conversation ID copied"));
}

function messageHtml(m) {
    const d = m.detail ?? {};
    const isFirst = !d.conversation_id;
    const fields = Object.entries(d.metadata ?? {}).map(([k, v]) => `
        <dt>${escapeHtml(k.replaceAll("_", " "))}</dt>
        <dd>${escapeHtml(typeof v === "string" ? v : JSON.stringify(v))}</dd>`).join("");
    const receipts = m.deliveries.map((del) => {
        const s = DELIVERY_STATUS[del.status];
        const verb = del.status === "sent"
            ? (isPull(del.destination) ? "Collected by" : "Delivered to")
            : del.status === "pending"
                ? (isPull(del.destination) ? "Waiting to be collected by" : "Waiting to send to")
                : "Couldn't deliver to";
        return `
            <span class="receipt-line">
                <button type="button" class="receipt ${s.cls}" data-delivery="${del.id}">
                    ${icon(s.icon)}<span>${escapeHtml(verb)} ${escapeHtml(systemName(del.destination))}</span>${icon("i-chevron", "receipt-chevron")}
                </button>
                ${del.status === "failed" ? `<button type="button" class="btn btn-tinted btn-small" data-retry="${del.id}">Retry</button>` : ""}
            </span>`;
    }).join("");
    return `
        <div class="message">
            ${avatar(m.source, systemName(m.source))}
            <div class="message-body">
                <span class="message-sender"><strong>${escapeHtml(systemName(m.source))}</strong> ${escapeHtml(formatRelative(m.at))}</span>
                <div class="bubble">
                    <div class="bubble-status">${isFirst ? "Opened as" : "Status"} ${statusPill(d.status)}</div>
                    ${d.external_ref ? `<div class="bubble-ref">Their ticket ${escapeHtml(d.external_ref)}</div>` : ""}
                    ${fields ? `<dl class="bubble-fields">${fields}</dl>` : ""}
                </div>
                ${receipts ? `<div class="receipts">${receipts}</div>` : `<div class="receipts"><span class="receipt">No other system receives this topic</span></div>`}
            </div>
        </div>`;
}

async function copyText(text, doneMessage) {
    try {
        await navigator.clipboard.writeText(text);
        toast(doneMessage);
    } catch {
        toast("Couldn't copy. Select the text and copy it manually.", "error");
    }
}

// ---------------------------------------------------------------------------
// Deliveries
// ---------------------------------------------------------------------------
const DELIVERIES_PAGE = 50;
const deliveriesState = { items: [], offset: 0, hasMore: false, selecting: false, selected: new Set() };

function deliveryRow(d) {
    const s = DELIVERY_STATUS[d.status];
    const selectable = d.status === "failed";
    const selecting = deliveriesState.selecting;
    const checked = deliveriesState.selected.has(d.id);
    const leading = selecting
        ? `<span class="select-circle">${icon("i-check")}</span>`
        : icon(s.icon, `status-icon ${s.cls}`);
    const roleAttrs = selecting
        ? `role="checkbox" aria-checked="${checked}" ${selectable ? "" : `aria-disabled="true"`}`
        : `aria-label="${escapeHtml(`${s.label}: ${systemName(d.source)} to ${systemName(d.destination)}`)}"`;
    return `
        <button type="button" class="row has-leading ${selecting && !selectable ? "is-unselectable" : ""}" data-delivery="${d.id}" ${roleAttrs}>
            ${leading}
            <span class="row-main">
                <span class="row-title wrap">${escapeHtml(systemName(d.source))} to ${escapeHtml(systemName(d.destination))}</span>
                <span class="row-subtitle">${escapeHtml(eventLabel(d.event))}: ${escapeHtml(d.conversation_subject || "Untitled conversation")}</span>
                ${d.status === "failed" && d.last_error ? `<span class="row-error">${escapeHtml(d.last_error)}</span>` : ""}
                ${d.status === "pending" ? `<span class="row-note">${d.attempts > 0 ? `Attempt ${d.attempts} of ${d.max_attempts} failed, trying again` : escapeHtml(waitingLabel(d))}</span>` : ""}
            </span>
            <span class="row-trailing">
                <span class="row-time">${escapeHtml(formatRelative(d.created_at))}</span>
                ${selecting ? "" : CHEVRON}
            </span>
        </button>`;
}

function deliveriesFilter() {
    const { params } = parseHash();
    return { status: params.get("status") ?? "", to: params.get("to") ?? "" };
}

function setDeliveriesFilter(changes) {
    const next = { ...deliveriesFilter(), ...changes };
    const params = new URLSearchParams();
    if (next.status) params.set("status", next.status);
    if (next.to) params.set("to", next.to);
    const qs = params.toString();
    location.hash = `#/deliveries${qs ? `?${qs}` : ""}`;
}

async function renderDeliveries() {
    const { status, to } = deliveriesFilter();
    document.querySelectorAll("#deliveries-status > button").forEach((b) => {
        b.setAttribute("aria-checked", String(b.dataset.value === status));
    });
    $("filter-deliveries-system").value = to;
    deliveriesState.offset = 0;
    deliveriesState.items = [];
    deliveriesState.selected.clear();
    await loadDeliveriesPage();
}

async function loadDeliveriesPage() {
    const { status, to } = deliveriesFilter();
    const params = new URLSearchParams({ limit: DELIVERIES_PAGE, offset: deliveriesState.offset });
    if (status) params.set("status", status);
    if (to) params.set("system_code", to);
    const result = await api(`deliveries?${params}`);
    const page = result.data ?? { items: [], has_more: false };
    deliveriesState.items.push(...page.items);
    deliveriesState.hasMore = page.has_more;
    drawDeliveries();
}

function drawDeliveries() {
    const list = $("list-deliveries");
    clearEmpty(list);
    const { status, to } = deliveriesFilter();
    const items = deliveriesState.items;
    const hasFailed = items.some((d) => d.status === "failed");
    if (!hasFailed && deliveriesState.selecting) setSelecting(false);

    if (items.length === 0) {
        list.innerHTML = "";
        const where = to ? ` to ${systemName(to)}` : "";
        const empty = {
            failed: { iconName: "i-check-circle", title: "Nothing failed", text: `Every message${where} was delivered or is still being tried.` },
            pending: { iconName: "i-clock", title: "Nothing waiting", text: `Every message${where} has been sent.` },
            sent: { iconName: "i-deliveries", title: "Nothing delivered yet", text: `Messages${where} appear here once their destination accepts them.` },
            "": { iconName: "i-deliveries", title: "No deliveries yet", text: "When a system sends a message, the bridge forwards it to every system subscribed to its topic. Each of those appears here." },
        }[status];
        setEmpty(list, empty);
    } else {
        list.innerHTML = items.map(deliveryRow).join("");
        list.querySelectorAll("[data-delivery]").forEach((row) => {
            row.addEventListener("click", () => onDeliveryRowClick(Number(row.dataset.delivery), row));
        });
    }

    $("btn-select-deliveries").hidden = !hasFailed || deliveriesState.selecting;
    $("btn-deliveries-more").hidden = !deliveriesState.hasMore;
    $("deliveries-footer").textContent = deliveriesState.selecting
        ? "Only failed deliveries can be retried. They're sent again with the same content."
        : items.length ? "Select a delivery to see exactly what was sent, and why it failed." : "";
    updateSelectionBar();
}

function onDeliveryRowClick(id, row) {
    const d = deliveriesState.items.find((x) => x.id === id);
    if (!deliveriesState.selecting) {
        openDeliverySheet(d);
        return;
    }
    if (d.status !== "failed") return;
    if (deliveriesState.selected.has(id)) deliveriesState.selected.delete(id);
    else deliveriesState.selected.add(id);
    row.setAttribute("aria-checked", String(deliveriesState.selected.has(id)));
    updateSelectionBar();
}

function setSelecting(on) {
    deliveriesState.selecting = on;
    deliveriesState.selected.clear();
    $("deliveries-action-bar").hidden = !on;
}

function updateSelectionBar() {
    const n = deliveriesState.selected.size;
    const failedIds = deliveriesState.items.filter((d) => d.status === "failed").map((d) => d.id);
    $("deliveries-selected-count").textContent = n ? `${n} selected` : "Select failed deliveries";
    $("btn-retry-deliveries").disabled = n === 0;
    $("btn-retry-deliveries").textContent = n ? `Retry ${n}` : "Retry";
    const all = failedIds.length > 0 && failedIds.every((id) => deliveriesState.selected.has(id));
    $("btn-deliveries-select-all").textContent = all ? "Deselect all" : "Select all";
}

document.querySelectorAll("#deliveries-status > button").forEach((b) => {
    b.addEventListener("click", () => setDeliveriesFilter({ status: b.dataset.value }));
});
$("filter-deliveries-system").addEventListener("change", (e) => setDeliveriesFilter({ to: e.target.value }));
$("btn-deliveries-more").addEventListener("click", (e) => busy(e.currentTarget, async () => {
    deliveriesState.offset += DELIVERIES_PAGE;
    await loadDeliveriesPage();
}));
$("btn-select-deliveries").addEventListener("click", () => { setSelecting(true); drawDeliveries(); });
$("btn-deliveries-cancel-select").addEventListener("click", () => { setSelecting(false); drawDeliveries(); });
$("btn-deliveries-select-all").addEventListener("click", () => {
    const failedIds = deliveriesState.items.filter((d) => d.status === "failed").map((d) => d.id);
    const all = failedIds.every((id) => deliveriesState.selected.has(id));
    deliveriesState.selected = new Set(all ? [] : failedIds);
    drawDeliveries();
});
$("btn-retry-deliveries").addEventListener("click", (e) => busy(e.currentTarget, async () => {
    const ids = [...deliveriesState.selected];
    setSelecting(false);
    await retryDeliveries(ids);
}));

// Delivery detail sheet: status, route, and the exact payload.
function openDeliverySheet(d) {
    if (!d) return;
    const s = DELIVERY_STATUS[d.status];
    const explanation = {
        sent: `Accepted by ${systemName(d.destination)} on ${formatDateTime(d.processed_at)}.`,
        pending: isPull(d.destination)
            ? `${systemName(d.destination)} collects its messages from the bridge's inbox. This one waits there until it does.`
            : d.attempts
                ? `Attempt ${d.attempts} of ${d.max_attempts} failed. The bridge tries again on its next run.`
                : "Queued. It goes out on the bridge's next run, or when you send pending messages now.",
        failed: `The bridge stopped after ${plural(d.attempts, "attempt")}. Fix the cause below, then retry.`,
    }[d.status];

    const heroLabel = d.status === "pending" && isPull(d.destination) ? "Waiting to be collected"
        : d.status === "sent" && isPull(d.destination) ? "Collected" : s.label;
    $("sheet-delivery-title").textContent = `Delivery ${d.id}`;
    const body = $("delivery-detail");
    body.innerHTML = `
        <div class="reveal-hero">
            <span class="hero-icon ${s.hero}">${icon(s.icon)}</span>
            <p class="reveal-title">${escapeHtml(heroLabel)}</p>
            <p class="reveal-text">${escapeHtml(explanation)}</p>
        </div>
        ${d.status === "failed" ? `
        <section class="group">
            <div class="list"><button type="button" class="list-button list-button-centered" id="btn-sheet-retry">${icon("i-retry")}Retry delivery</button></div>
        </section>` : ""}
        <section class="group">
            <div class="list">
                <div class="row kv-row"><span class="kv-label">From</span><span class="kv-value">${escapeHtml(systemName(d.source))}</span></div>
                <div class="row kv-row"><span class="kv-label">To</span><span class="kv-value">${escapeHtml(systemName(d.destination))}</span></div>
                <a class="row kv-row" href="#/conversations/${encodeURIComponent(d.conversation_id)}" data-close>
                    <span class="kv-label">Conversation</span>
                    <span class="row-trailing"><span class="kv-value">${escapeHtml(d.conversation_subject || "Untitled conversation")}</span>${CHEVRON}</span>
                </a>
                <div class="row kv-row"><span class="kv-label">Type</span><span class="kv-value">${escapeHtml(d.event === "ticket.created" ? "New ticket for them" : `Update to their ticket ${d.payload?.external_ref ?? ""}`)}</span></div>
                <div class="row kv-row"><span class="kv-label">Queued</span><span class="kv-value">${escapeHtml(formatDateTime(d.created_at))}</span></div>
                ${d.processed_at ? `<div class="row kv-row"><span class="kv-label">Delivered</span><span class="kv-value">${escapeHtml(formatDateTime(d.processed_at))}</span></div>` : ""}
                <div class="row kv-row"><span class="kv-label">Attempts</span><span class="kv-value">${d.attempts} of ${d.max_attempts}</span></div>
            </div>
        </section>
        ${d.last_error ? `
        <section class="group">
            <h3 class="group-title">Last error</h3>
            <div class="list"><pre class="code-block" id="delivery-error"></pre></div>
        </section>` : ""}
        <section class="group">
            <h3 class="group-title">${d.status === "sent" ? "What was sent" : "What will be sent"}</h3>
            <div class="list">
                <pre class="code-block" id="delivery-payload"></pre>
                <button type="button" class="list-button" id="btn-copy-payload">${icon("i-copy")}Copy JSON</button>
            </div>
            <p class="group-footer">The exact body the bridge posts to ${escapeHtml(systemName(d.destination))}'s webhook.</p>
        </section>`;
    // textContent: errors and payloads carry data from external systems.
    $("delivery-payload").textContent = JSON.stringify(d.payload, null, 2);
    if (d.last_error) $("delivery-error").textContent = d.last_error;
    $("btn-copy-payload").addEventListener("click", () => copyText(JSON.stringify(d.payload, null, 2), "Payload copied"));
    $("btn-sheet-retry")?.addEventListener("click", (e) => busy(e.currentTarget, async () => {
        $("sheet-delivery").close();
        await retryDeliveries([d.id]);
    }));
    openSheet("sheet-delivery");
}

// ---------------------------------------------------------------------------
// Setup: systems and topics
// ---------------------------------------------------------------------------
let setupTab = "systems";

function renderSetup(tab) {
    setupTab = tab;
    document.querySelectorAll("#view-setup .segmented > a").forEach((a) => {
        if (a.dataset.value === tab) a.setAttribute("aria-current", "page");
        else a.removeAttribute("aria-current");
    });
    $("setup-systems").hidden = tab !== "systems";
    $("setup-topics").hidden = tab !== "topics";
    $("btn-setup-add-label").textContent = tab === "systems" ? "Add system" : "Add topic";
    if (tab === "systems") drawSystems();
    else drawTopics();
}

$("btn-setup-add").addEventListener("click", () => {
    if (setupTab === "systems") openSystemSheet(null);
    else openTopicSheet(null);
});

let showArchived = false;

$("btn-toggle-archived").addEventListener("click", () => {
    showArchived = !showArchived;
    drawSystems();
});

function drawSystems() {
    const list = $("list-systems");
    clearEmpty(list);
    const archivedCount = state.systems.length - currentSystems().length;
    const toggle = $("btn-toggle-archived");
    toggle.hidden = archivedCount === 0;
    toggle.textContent = showArchived ? "Hide archived" : `Show archived (${archivedCount})`;
    const shown = showArchived ? state.systems : currentSystems();
    if (shown.length === 0) {
        list.innerHTML = "";
        setEmpty(list, {
            iconName: "i-setup",
            title: archivedCount ? "No systems in use" : "No systems yet",
            text: archivedCount
                ? "Every system is archived. Add one, or show the archived ones to bring one back."
                : "Add each team's support application, so the bridge can pass tickets between them.",
            actionLabel: "Add system",
            actionId: "btn-empty-add-system",
        }, () => openSystemSheet(null));
        return;
    }
    list.innerHTML = shown.map((s) => {
        const pills = s.archived_at ? `<span class="pill">Archived</span>` : [
            s.active ? "" : `<span class="pill">Inactive</span>`,
            s.has_legacy_secret_ref ? `<span class="pill pill-red">Needs its secret</span>` : "",
            s.topics.length === 0 ? `<span class="pill pill-orange">No topics</span>` : `<span class="pill">${escapeHtml(plural(s.topics.length, "topic"))}</span>`,
            s.delivery_mode === "pull"
                ? (isStalePuller(s) ? `<span class="pill pill-orange">Not collecting</span>` : `<span class="pill pill-blue">Collects its messages</span>`)
                : "",
            s.delivery_mode === "push" && s.verify_tls === false ? `<span class="pill pill-orange">Self-signed certificate</span>` : "",
            s.has_secret ? `<span class="pill">${icon("i-lock")}Secret set</span>` : "",
        ].join("");
        return `
            <button type="button" class="row has-leading ${s.archived_at ? "is-archived" : ""}" data-system="${escapeHtml(s.code)}">
                ${avatar(s.code, s.name)}
                <span class="row-main">
                    <span class="row-title">${escapeHtml(s.name)}</span>
                    <span class="row-subtitle">${escapeHtml(s.code)} – ${escapeHtml(s.archived_at ? `archived ${formatRelative(s.archived_at)}` : s.delivery_mode === "pull"
                        ? (s.last_polled_at ? `last collected ${formatRelative(s.last_polled_at)}` : "never collected")
                        : hostOf(s.base_url))}</span>
                    <span class="row-meta">${pills}</span>
                </span>
                <span class="row-trailing">${CHEVRON}</span>
            </button>`;
    }).join("");
    list.querySelectorAll("[data-system]").forEach((row) => {
        row.addEventListener("click", () => openSystemSheet(state.systemsByCode.get(row.dataset.system)));
    });
}

function drawTopics() {
    const list = $("list-topics");
    clearEmpty(list);
    if (state.topics.length === 0) {
        list.innerHTML = "";
        setEmpty(list, {
            iconName: "i-tag",
            title: "No topics yet",
            text: "Create one for each kind of work teams send each other, like Infrastructure or Patient admin.",
            actionLabel: "Add topic",
            actionId: "btn-empty-add-topic",
        }, () => openTopicSheet(null));
        return;
    }
    list.innerHTML = state.topics.map((t) => {
        const subscribers = currentSystems().filter((s) => s.topics.includes(t.code)).length;
        return `
            <button type="button" class="row has-leading" data-topic="${escapeHtml(t.code)}">
                ${avatar(t.code, t.name)}
                <span class="row-main">
                    <span class="row-title">${escapeHtml(t.name)}</span>
                    ${t.description ? `<span class="row-subtitle wrap">${escapeHtml(t.description)}</span>` : ""}
                    <span class="row-meta">
                        <span class="pill pill-mono">${escapeHtml(t.code)}</span>
                        ${subscribers ? `<span class="pill">${escapeHtml(plural(subscribers, "system"))}</span>` : `<span class="pill pill-orange">No systems</span>`}
                        ${t.active ? "" : `<span class="pill">Inactive</span>`}
                    </span>
                </span>
                <span class="row-trailing">${CHEVRON}</span>
            </button>`;
    }).join("");
    list.querySelectorAll("[data-topic]").forEach((row) => {
        row.addEventListener("click", () => openTopicSheet(state.topicsByCode.get(row.dataset.topic)));
    });
}

// ---- System sheet -----------------------------------------------------------
const systemForm = $("form-system");
const clearSecretSwitch = $("clear-outbound-secret-checkbox");
let systemBeingEdited = null;
let systemDeliveryMode = "push";

function setDeliveryMode(mode) {
    systemDeliveryMode = mode;
    document.querySelectorAll("#delivery-mode > button").forEach((b) => {
        b.setAttribute("aria-checked", String(b.dataset.value === mode));
    });
    $("push-settings").hidden = mode !== "push";
    $("pull-settings").hidden = mode !== "pull";
    $("outbound-auth-section").hidden = mode !== "push";
}
document.querySelectorAll("#delivery-mode > button").forEach((b) => {
    b.addEventListener("click", () => setDeliveryMode(b.dataset.value));
});

clearSecretSwitch.addEventListener("change", () => {
    systemForm.outbound_secret.disabled = clearSecretSwitch.checked;
    if (clearSecretSwitch.checked) systemForm.outbound_secret.value = "";
});

function openSystemSheet(system) {
    systemForm.reset();
    systemBeingEdited = system;
    $("sheet-system-title").textContent = system ? system.name : "New system";
    $("system-form-error").hidden = true;

    systemForm.code.disabled = !!system;
    $("system-code-footer").textContent = system
        ? "The code can't be changed. An inactive system doesn't receive messages."
        : "The code identifies this system in the API and can't be changed later. An inactive system doesn't receive messages.";
    if (system) {
        systemForm.code.value = system.code;
        systemForm.name.value = system.name;
        systemForm.base_url.value = system.base_url ?? "";
        systemForm.active.checked = system.active;
        systemForm.skip_tls_verify.checked = system.verify_tls === false;
        systemForm.auth_header.value = system.auth_header ?? "";
        systemForm.auth_value_prefix.value = system.auth_value_prefix ?? "";
    }
    setDeliveryMode(system?.delivery_mode ?? "push");
    $("system-last-polled").textContent = system?.last_polled_at ? formatDateTime(system.last_polled_at) : "Never";

    // The secret is never returned - only whether one is stored. Leaving
    // the field empty on Save keeps it.
    systemForm.outbound_secret.placeholder = system?.has_secret ? "Stored. Leave empty to keep it" : "Not set";
    systemForm.outbound_secret.disabled = false;
    // A pre-0.8.0 secret_ref can't be used any more: deliveries fail until
    // the admin sets a secret or explicitly chooses none.
    const legacy = !!system?.has_legacy_secret_ref;
    $("legacy-secret-ref-warning").hidden = !legacy;
    $("clear-outbound-secret-label").textContent = system?.has_secret ? "Remove stored secret" : "Send without a secret";
    $("clear-outbound-secret-field").hidden = !(system?.has_secret || legacy);

    const topicsList = $("system-topics");
    topicsList.innerHTML = state.topics.length ? "" : `<div class="row"><span class="row-subtitle wrap">No topics exist yet. Create one in Setup › Topics.</span></div>`;
    for (const t of state.topics) {
        const row = document.createElement("label");
        row.className = "form-row form-row-switch";
        row.innerHTML = `
            <span class="form-label"><span class="topic-name"></span> <span class="pill pill-mono"></span></span>
            <input type="checkbox" class="switch" value="">`;
        row.querySelector(".topic-name").textContent = t.name;
        row.querySelector(".pill").textContent = t.code;
        const input = row.querySelector("input");
        input.value = t.code;
        input.checked = !!system?.topics.includes(t.code);
        topicsList.appendChild(row);
    }

    $("system-api-keys-section").hidden = !system;
    $("new-api-key-description").value = "";
    $("list-api-keys").innerHTML = "";
    if (system) loadApiKeys(system.code);
    renderRetireSection(system);

    openSheet("sheet-system");
    systemForm.name.focus();
}

systemForm.addEventListener("submit", async (e) => {
    // The sheet stays open until the save succeeds, so a rejected save
    // (e.g. SECRETS_ENCRYPTION_KEY not configured) says why.
    e.preventDefault();
    const errorEl = $("system-form-error");
    errorEl.hidden = true;
    const data = new FormData(systemForm);

    const missing = [];
    if (!data.get("name")?.trim()) missing.push("a name");
    if (!systemBeingEdited && !data.get("code")?.trim()) missing.push("a code");
    if (systemDeliveryMode === "push" && !data.get("base_url")?.trim()) missing.push("a webhook URL");
    if (missing.length) {
        errorEl.textContent = `Enter ${missing.join(", ")}.`;
        errorEl.hidden = false;
        return;
    }

    // Only send auth_config keys that have a value: the backend merges
    // auth_config on update, so an omitted key keeps what's stored.
    const authConfig = {};
    if (data.get("auth_header")) authConfig.header = data.get("auth_header");
    if (data.get("auth_value_prefix")) authConfig.value_prefix = data.get("auth_value_prefix");

    const body = {
        name: data.get("name").trim(),
        delivery_mode: systemDeliveryMode,
        verify_tls: !systemForm.skip_tls_verify.checked,
        active: systemForm.active.checked,
        topics: [...$("system-topics").querySelectorAll("input:checked")].map((el) => el.value),
    };
    // A pull system keeps any URL it had (unused) - only send one that's typed.
    if (data.get("base_url")?.trim()) body.base_url = data.get("base_url").trim();
    if (Object.keys(authConfig).length) body.auth_config = authConfig;
    const secret = data.get("outbound_secret");
    if (systemBeingEdited && clearSecretSwitch.checked) body.clear_outbound_secret = true;
    else if (secret) body.outbound_secret = secret;

    const submit = systemForm.querySelector("[type=submit]");
    const result = await busy(submit, () => systemBeingEdited
        ? api(`systems/${encodeURIComponent(systemBeingEdited.code)}`, { method: "PATCH", json: body })
        : api("systems", { json: { ...body, code: data.get("code").trim() } }));
    if (!result.ok) {
        errorEl.textContent = isDuplicateCode(result)
            ? `A system with the code ${data.get("code").trim()} already exists. Choose another code.`
            : errorText(result, "Couldn't save");
        errorEl.hidden = false;
        $("sheet-system").querySelector(".sheet-body").scrollTo({ top: 0, behavior: "smooth" });
        return;
    }
    systemForm.outbound_secret.value = ""; // don't leave the secret in the DOM
    $("sheet-system").close();
    toast(systemBeingEdited ? "System saved" : "System added");
    await loadReference();
    await currentRender?.();
});

// ---- Archive / unarchive / delete (bottom of the system sheet) -------------------
function renderRetireSection(system) {
    $("system-retire-section").hidden = !system;
    if (!system) return;
    const archived = !!system.archived_at;
    $("btn-unarchive-system").hidden = !archived;
    $("btn-archive-system").hidden = archived;
    $("btn-delete-system").hidden = !system.deletable;
    $("system-retire-footer").textContent = [
        archived
            ? `Archived on ${formatDateTime(system.archived_at)}. Unarchiving brings it back switched off, so nothing reaches it until you turn it on.`
            : "Archiving hides this system and stops its messages both ways, keeping its history. You can unarchive it later.",
        system.deletable
            ? "It never exchanged a message, so you can also delete it for good."
            : "It can't be deleted because it has exchanged messages.",
    ].join(" ");
}

async function retireSystem(action) {
    const system = systemBeingEdited;
    const copy = {
        archive: {
            title: `Archive ${system.name}?`,
            message: "It's switched off and hidden. Its keys stop working and it gets no more messages. Its history stays.",
            confirmLabel: "Archive",
            done: "System archived",
        },
        unarchive: { done: "System unarchived" },
        delete: {
            title: `Delete ${system.name}?`,
            message: "Its keys and topic subscriptions are deleted too. This can't be undone.",
            confirmLabel: "Delete",
            done: "System deleted",
        },
    }[action];
    if (copy.title && !(await confirmAlert({ ...copy, destructive: true }))) return;

    const code = encodeURIComponent(system.code);
    const result = action === "delete"
        ? await api(`systems/${code}`, { method: "DELETE" })
        : await api(`systems/${code}/${action}`, { method: "POST" });
    if (!result.ok) {
        toast(errorText(result, `Couldn't ${action} the system`), "error");
        return;
    }
    $("sheet-system").close();
    toast(copy.done);
    await loadReference();
    await currentRender?.();
}

$("btn-archive-system").addEventListener("click", () => retireSystem("archive"));
$("btn-unarchive-system").addEventListener("click", () => retireSystem("unarchive"));
$("btn-delete-system").addEventListener("click", () => retireSystem("delete"));

// ---- Inbound API keys (inside the system sheet) ---------------------------------
async function loadApiKeys(code) {
    const list = $("list-api-keys");
    list.innerHTML = `<div class="loading-row">Loading keys…</div>`;
    const result = await api(`systems/${encodeURIComponent(code)}/api-keys`);
    const keys = result.data ?? [];
    if (keys.length === 0) {
        list.innerHTML = `<div class="row"><span class="row-subtitle wrap">No keys yet. This system can't send messages until it has one.</span></div>`;
        return;
    }
    list.innerHTML = keys.map((k) => `
        <div class="row has-leading">
            <span class="tile ${k.active ? "tile-indigo" : "tile-gray"}">${icon("i-key")}</span>
            <span class="row-main">
                <span class="row-title">${escapeHtml(k.description || "Unnamed key")}</span>
                <span class="row-subtitle">${k.active ? `Created ${escapeHtml(formatDateTime(k.created_at))}` : `Revoked ${escapeHtml(formatDateTime(k.revoked_at))}`}</span>
            </span>
            <span class="row-trailing">
                ${k.active ? `<button type="button" class="text-btn text-btn-destructive" data-revoke="${k.id}">Revoke</button>` : `<span class="pill">Revoked</span>`}
            </span>
        </div>`).join("");
    list.querySelectorAll("[data-revoke]").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const confirmed = await confirmAlert({
                title: "Revoke this key?",
                message: "The system using it can no longer send messages to the bridge. This can't be undone.",
                confirmLabel: "Revoke",
                destructive: true,
            });
            if (!confirmed) return;
            const res = await api(`systems/${encodeURIComponent(code)}/api-keys/${btn.dataset.revoke}`, { method: "DELETE" });
            if (res.ok) toast("Key revoked");
            else toast(errorText(res, "Couldn't revoke the key"), "error");
            await loadApiKeys(code);
        });
    });
}

$("btn-generate-api-key").addEventListener("click", (e) => busy(e.currentTarget, async () => {
    if (!systemBeingEdited) return;
    const input = $("new-api-key-description");
    const result = await api(`systems/${encodeURIComponent(systemBeingEdited.code)}/api-keys`, {
        json: { description: input.value.trim() || null },
    });
    if (!result.ok) {
        toast(errorText(result, "Couldn't generate the key"), "error");
        return;
    }
    input.value = "";
    await loadApiKeys(systemBeingEdited.code);
    openApiKeyReveal(result.data.api_key);
}));

// One-time reveal: the plaintext key exists only in that response. The
// field is cleared on close so it doesn't linger in the DOM.
function openApiKeyReveal(plaintextKey) {
    const field = $("api-key-reveal-value");
    field.value = plaintextKey;
    $("sheet-api-key-reveal").showModal();
    field.focus();
    field.select();
}
$("btn-copy-api-key").addEventListener("click", () => {
    $("api-key-reveal-value").select();
    copyText($("api-key-reveal-value").value, "Key copied");
});
function closeApiKeyReveal() {
    $("api-key-reveal-value").value = "";
    $("sheet-api-key-reveal").close();
}
$("btn-close-api-key-reveal").addEventListener("click", closeApiKeyReveal);
$("sheet-api-key-reveal").addEventListener("close", () => { $("api-key-reveal-value").value = ""; });

// ---- Topic sheet --------------------------------------------------------------
const topicForm = $("form-topic");
let topicBeingEdited = null;

function openTopicSheet(topic) {
    topicForm.reset();
    topicBeingEdited = topic;
    $("sheet-topic-title").textContent = topic ? topic.name : "New topic";
    $("topic-form-error").hidden = true;
    topicForm.code.disabled = !!topic;
    $("topic-code-footer").textContent = topic
        ? "The code can't be changed. An inactive topic can't be used for new conversations."
        : "Systems use the code when starting a conversation. It can't be changed later.";
    if (topic) {
        topicForm.code.value = topic.code;
        topicForm.name.value = topic.name;
        topicForm.description.value = topic.description ?? "";
        topicForm.active.checked = topic.active;
    }

    const subscribers = topic ? currentSystems().filter((s) => s.topics.includes(topic.code)) : [];
    $("topic-subscribers-section").hidden = !topic;
    const list = $("list-topic-subscribers");
    list.innerHTML = subscribers.length
        ? subscribers.map((s) => `
            <button type="button" class="row has-leading" data-system="${escapeHtml(s.code)}">
                ${avatar(s.code, s.name)}
                <span class="row-main"><span class="row-title">${escapeHtml(s.name)}</span></span>
                <span class="row-trailing">${CHEVRON}</span>
            </button>`).join("")
        : `<div class="row"><span class="row-subtitle wrap">No system receives this topic yet.</span></div>`;
    list.querySelectorAll("[data-system]").forEach((row) => {
        row.addEventListener("click", () => {
            $("sheet-topic").close();
            openSystemSheet(state.systemsByCode.get(row.dataset.system));
        });
    });

    openSheet("sheet-topic");
    topicForm.name.focus();
}

topicForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorEl = $("topic-form-error");
    errorEl.hidden = true;
    const data = new FormData(topicForm);
    if (!data.get("name")?.trim() || (!topicBeingEdited && !data.get("code")?.trim())) {
        errorEl.textContent = topicBeingEdited ? "Enter a name." : "Enter a name and a code.";
        errorEl.hidden = false;
        return;
    }
    const body = {
        name: data.get("name").trim(),
        description: data.get("description")?.trim() || null,
        active: topicForm.active.checked,
    };
    const submit = topicForm.querySelector("[type=submit]");
    const result = await busy(submit, () => topicBeingEdited
        ? api(`topics/${encodeURIComponent(topicBeingEdited.code)}`, { method: "PATCH", json: body })
        : api("topics", { json: { ...body, code: data.get("code").trim() } }));
    if (!result.ok) {
        errorEl.textContent = isDuplicateCode(result)
            ? `A topic with the code ${data.get("code").trim()} already exists. Choose another code.`
            : errorText(result, "Couldn't save");
        errorEl.hidden = false;
        return;
    }
    $("sheet-topic").close();
    toast(topicBeingEdited ? "Topic saved" : "Topic added");
    await loadReference();
    await currentRender?.();
});

// ---------------------------------------------------------------------------
// Activity (audit log)
// ---------------------------------------------------------------------------
const ACTIVITY_PAGE = 50;
const activityState = { items: [], offset: 0, hasMore: false };

// Plain-language title and icon for each audit event type.
function describeActivity(r) {
    const sys = systemName(r.system_code);
    const d = r.detail ?? {};
    const types = {
        event_received: { tile: "tile-blue", icon: "i-conversations", title: `Message from ${sys}`, sub: d.status ? `Status: ${CONVERSATION_STATUS[d.status]?.label ?? d.status}` : "" },
        conversation_created: { tile: "tile-blue", icon: "i-plus", title: `${sys} started a conversation`, sub: d.topic_code ? `Topic: ${topicName(d.topic_code)}` : "" },
        fanout_scheduled: { tile: "tile-gray", icon: "i-send", title: (d.outbox_ids ?? []).length ? `Queued for ${plural(d.outbox_ids.length, "system")}` : "Nobody else receives this topic", sub: (d.destinations ?? []).map((x) => systemName(x.destination)).join(", ") },
        delivery_success: { tile: "tile-green", icon: "i-check", title: d.collected ? `Collected by ${sys}` : `Delivered to ${sys}`, sub: "" },
        delivery_failure: { tile: "tile-red", icon: "i-alert", title: `Couldn't deliver to ${sys}`, sub: d.error ?? "" },
        delivery_retry_requested: { tile: "tile-orange", icon: "i-retry", title: `Retry requested for ${sys}`, sub: d.previous_error ?? "" },
        system_config_created: { tile: "tile-gray", icon: "i-gear", title: `${sys} added`, sub: "" },
        system_archived: { tile: "tile-gray", icon: "i-setup", title: `${sys} archived`, sub: "" },
        system_unarchived: { tile: "tile-gray", icon: "i-setup", title: `${sys} unarchived`, sub: "" },
        system_deleted: { tile: "tile-red", icon: "i-setup", title: `${d.name ?? sys} deleted`, sub: r.system_code ?? "" },
        system_config_updated: { tile: "tile-gray", icon: "i-gear", title: `${sys} settings changed`, sub: (d.changed_fields ?? []).map((f) => f.replaceAll("_", " ")).join(", ") },
        topic_config_created: { tile: "tile-gray", icon: "i-tag", title: `Topic ${topicName(d.code)} added`, sub: "" },
        topic_config_updated: { tile: "tile-gray", icon: "i-tag", title: `Topic ${topicName(d.code)} changed`, sub: (d.changed_fields ?? []).join(", ") },
        inbound_api_key_created: { tile: "tile-indigo", icon: "i-key", title: `New key for ${sys}`, sub: d.description ?? "" },
        inbound_api_key_revoked: { tile: "tile-indigo", icon: "i-key", title: `Key revoked for ${sys}`, sub: "" },
        admin_login: { tile: "tile-gray", icon: "i-person", title: `${d.username ?? "Someone"} signed in`, sub: "" },
        admin_login_failed: { tile: "tile-red", icon: "i-lock", title: `Failed sign-in as ${d.username ?? "unknown"}`, sub: "" },
        admin_password_changed: { tile: "tile-gray", icon: "i-lock", title: `${d.username ?? "Someone"} changed their password`, sub: "" },
    };
    return types[r.event_type] ?? { tile: "tile-gray", icon: "i-activity", title: r.event_type.replaceAll("_", " "), sub: "" };
}

function activityParams() {
    const { params } = parseHash();
    return { system: params.get("system") ?? "", conversation: params.get("conversation") ?? "" };
}

async function renderActivity() {
    const { system, conversation } = activityParams();
    $("filter-activity-system").value = system;
    const chip = $("activity-conversation-chip");
    chip.hidden = !conversation;
    if (conversation) {
        const c = await api(`conversations/${encodeURIComponent(conversation)}`);
        $("activity-conversation-label").textContent = `Conversation: ${c.data?.subject || conversation.slice(0, 8)}`;
    }
    activityState.items = [];
    activityState.offset = 0;
    await loadActivityPage();
}

async function loadActivityPage() {
    const { system, conversation } = activityParams();
    const params = new URLSearchParams({ limit: ACTIVITY_PAGE, offset: activityState.offset });
    if (system) params.set("system_code", system);
    if (conversation) params.set("conversation_id", conversation);
    const result = await api(`audit?${params}`);
    const page = result.data ?? { items: [], has_more: false };
    activityState.items.push(...page.items);
    activityState.hasMore = page.has_more;
    drawActivity();
}

function drawActivity() {
    const list = $("list-activity");
    clearEmpty(list);
    $("btn-activity-more").hidden = !activityState.hasMore;
    if (activityState.items.length === 0) {
        list.innerHTML = "";
        setEmpty(list, { iconName: "i-activity", title: "No activity", text: "Nothing has been recorded for this filter yet." });
        return;
    }
    list.innerHTML = activityState.items.map((r) => {
        const a = describeActivity(r);
        return `
            <div class="activity-item">
                <button type="button" class="row has-leading" aria-expanded="false" data-activity="${r.id}">
                    <span class="tile ${a.tile}">${icon(a.icon)}</span>
                    <span class="row-main">
                        <span class="row-title">${escapeHtml(a.title)}</span>
                        ${a.sub ? `<span class="row-subtitle">${escapeHtml(a.sub)}</span>` : ""}
                    </span>
                    <span class="row-trailing">
                        <span class="row-time">${escapeHtml(formatRelative(r.created_at))}</span>
                        ${CHEVRON}
                    </span>
                </button>
                <dl class="activity-detail" hidden></dl>
            </div>`;
    }).join("");
    list.querySelectorAll("[data-activity]").forEach((btn) => {
        btn.addEventListener("click", () => toggleActivity(btn));
    });
}

function toggleActivity(btn) {
    const detail = btn.nextElementSibling;
    const open = btn.getAttribute("aria-expanded") !== "true";
    btn.setAttribute("aria-expanded", String(open));
    detail.hidden = !open;
    if (!open || detail.childElementCount) return;
    const r = activityState.items.find((x) => x.id === Number(btn.dataset.activity));
    const add = (label, value, isLink) => {
        const dt = document.createElement("dt");
        dt.textContent = label;
        const dd = document.createElement("dd");
        if (isLink) {
            const a = document.createElement("a");
            a.href = isLink;
            a.textContent = value;
            dd.appendChild(a);
        } else {
            dd.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
        }
        detail.append(dt, dd);
    };
    add("When", formatDateTime(r.created_at));
    add("Event", r.event_type);
    if (r.system_code) add("System", `${systemName(r.system_code)} (${r.system_code})`);
    if (r.conversation_id) add("Conversation", "Open conversation", `#/conversations/${encodeURIComponent(r.conversation_id)}`);
    for (const [k, v] of Object.entries(r.detail ?? {})) add(k.replaceAll("_", " "), v);
}

$("filter-activity-system").addEventListener("change", (e) => {
    const { conversation } = activityParams();
    const params = new URLSearchParams();
    if (e.target.value) params.set("system", e.target.value);
    if (conversation) params.set("conversation", conversation);
    location.hash = `#/activity${params.toString() ? `?${params}` : ""}`;
});
$("btn-activity-more").addEventListener("click", (e) => busy(e.currentTarget, async () => {
    activityState.offset += ACTIVITY_PAGE;
    await loadActivityPage();
}));

// ---------------------------------------------------------------------------
// Account: change password, log out
// ---------------------------------------------------------------------------
function openAccount() {
    openSheet("sheet-account");
}
$("btn-account").addEventListener("click", openAccount);

$("btn-logout").addEventListener("click", async () => {
    await api("auth/logout", { method: "POST" });
    window.location.href = "login.html";
});

const passwordForm = $("form-change-password");
$("btn-change-password").addEventListener("click", () => {
    $("sheet-account").close();
    passwordForm.reset();
    $("change-password-error").hidden = true;
    openSheet("sheet-change-password");
    passwordForm.current_password.focus();
});

passwordForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = new FormData(passwordForm);
    const errorEl = $("change-password-error");
    errorEl.hidden = true;
    const fail = (text) => { errorEl.textContent = text; errorEl.hidden = false; };

    if (!data.get("current_password")) return fail("Enter your current password.");
    if ((data.get("new_password") ?? "").length < 8) return fail("The new password needs at least 8 characters.");
    if (data.get("new_password") !== data.get("new_password_confirm")) return fail("The new passwords don't match.");

    const result = await busy(passwordForm.querySelector("[type=submit]"), () => api("auth/change-password", {
        json: { current_password: data.get("current_password"), new_password: data.get("new_password") },
    }));
    if (!result.ok) return fail("The current password is incorrect.");
    $("sheet-change-password").close();
    toast("Password changed");
});

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------
(async function start() {
    const me = await api("auth/me"); // 401 -> redirect, see fetch wrapper
    $("current-username").textContent = me.data?.username ?? "";
    $("account-username").textContent = me.data?.username ?? "";

    try {
        await nativeFetch("health").then((r) => r.json());
    } catch {
        $("offline-banner").hidden = false;
        return;
    }
    await loadReference();
    await refreshFailedBadge();
    await router();

    // Keep the failed-deliveries badge current while the page stays open.
    setInterval(() => { if (!document.hidden) refreshFailedBadge(); }, 60000);
})();
