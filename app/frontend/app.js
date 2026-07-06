/**
 * app.js
 * ------
 * JavaScript vanilla (sem framework/build step) que consome a API REST do
 * Ticket Bridge e popula os três separadores: Sistemas, Conversas, Auditoria.
 *
 * Nota: estes endpoints (/api/v1/systems, /api/v1/conversations,
 * /api/v1/audit) assumem-se protegidos por IAM do Cloud Run ou por um
 * proxy de autenticação à frente do serviço - este ficheiro não implementa
 * login. Ver README.md secção "Segurança do frontend de configuração".
 */

const API_BASE = "/api/v1";

// ---------------------------------------------------------------------------
// Navegação entre separadores
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
// Sistemas
// ---------------------------------------------------------------------------
async function carregarSistemas() {
    const resp = await fetch(`${API_BASE}/systems`);
    const sistemas = await resp.json();

    const tbody = document.querySelector("#tabela-sistemas tbody");
    tbody.innerHTML = "";
    const selects = [
        document.getElementById("filtro-conversas-sistema"),
        document.getElementById("filtro-auditoria-sistema"),
    ];
    selects.forEach((sel) => {
        while (sel.options.length > 1) sel.remove(1);
    });

    for (const s of sistemas) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><code>${s.codigo}</code></td>
            <td>${s.nome}</td>
            <td>${s.base_url}</td>
            <td>${s.auth_type}</td>
            <td><span class="badge ${s.active ? "ativo" : "inativo"}">${s.active ? "Ativo" : "Inativo"}</span></td>
            <td>${formatarData(s.updated_at)}</td>
        `;
        tr.style.cursor = "pointer";
        tr.addEventListener("click", () => abrirDialogSistema(s));
        tbody.appendChild(tr);

        selects.forEach((sel) => {
            const opt = document.createElement("option");
            opt.value = s.codigo;
            opt.textContent = s.nome;
            sel.appendChild(opt);
        });
    }
}

const dialogSistema = document.getElementById("dialog-sistema");
const formSistema = document.getElementById("form-sistema");
let codigoEmEdicao = null;

document.getElementById("btn-novo-sistema").addEventListener("click", () => abrirDialogSistema(null));
document.getElementById("btn-cancelar-sistema").addEventListener("click", () => dialogSistema.close());

function abrirDialogSistema(sistema) {
    formSistema.reset();
    codigoEmEdicao = sistema ? sistema.codigo : null;
    formSistema.codigo.disabled = !!sistema;

    if (sistema) {
        formSistema.codigo.value = sistema.codigo;
        formSistema.nome.value = sistema.nome;
        formSistema.base_url.value = sistema.base_url;
        formSistema.auth_type.value = sistema.auth_type;
        formSistema.status_mapping.value = JSON.stringify(sistema.status_mapping || {}, null, 2);
        formSistema.active.checked = sistema.active;
    }
    dialogSistema.showModal();
}

formSistema.addEventListener("submit", async (ev) => {
    const dados = new FormData(formSistema);
    let statusMapping = {};
    let payloadTemplate = {};
    try {
        statusMapping = dados.get("status_mapping") ? JSON.parse(dados.get("status_mapping")) : {};
        payloadTemplate = dados.get("payload_template") ? JSON.parse(dados.get("payload_template")) : {};
    } catch (e) {
        alert("JSON inválido em mapeamento de estados ou template de payload.");
        ev.preventDefault();
        return;
    }

    const corpo = {
        nome: dados.get("nome"),
        base_url: dados.get("base_url"),
        auth_type: dados.get("auth_type"),
        auth_config: { secret_ref: dados.get("secret_ref") || null },
        status_mapping: statusMapping,
        payload_template: payloadTemplate,
        active: formSistema.active.checked,
    };

    if (codigoEmEdicao) {
        await fetch(`${API_BASE}/systems/${codigoEmEdicao}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(corpo),
        });
    } else {
        corpo.codigo = dados.get("codigo");
        await fetch(`${API_BASE}/systems`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(corpo),
        });
    }
    await carregarSistemas();
});

// ---------------------------------------------------------------------------
// Conversas
// ---------------------------------------------------------------------------
async function carregarConversas() {
    const sistema = document.getElementById("filtro-conversas-sistema").value;
    const qs = sistema ? `?sistema=${encodeURIComponent(sistema)}` : "";
    const resp = await fetch(`${API_BASE}/conversations${qs}`);
    const conversas = await resp.json();

    const tbody = document.querySelector("#tabela-conversas tbody");
    tbody.innerHTML = "";
    for (const c of conversas) {
        const participantes = c.participants
            .map((p) => `${p.sistema}: <code>${p.ref_externa}</code> (${p.status_local ?? "—"})`)
            .join("<br>");
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><code>${c.conversation_id.slice(0, 8)}…</code></td>
            <td>${c.assunto ?? "—"}</td>
            <td>${c.status_geral}</td>
            <td>${participantes}</td>
            <td>${formatarData(c.updated_at)}</td>
        `;
        tbody.appendChild(tr);
    }
}

document.getElementById("filtro-conversas-sistema").addEventListener("change", carregarConversas);

// ---------------------------------------------------------------------------
// Auditoria
// ---------------------------------------------------------------------------
async function carregarAuditoria() {
    const sistema = document.getElementById("filtro-auditoria-sistema").value;
    const qs = sistema ? `?sistema=${encodeURIComponent(sistema)}` : "";
    const resp = await fetch(`${API_BASE}/audit${qs}`);
    const registos = await resp.json();

    const tbody = document.querySelector("#tabela-auditoria tbody");
    tbody.innerHTML = "";
    for (const r of registos) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${formatarData(r.created_at)}</td>
            <td>${r.evento_tipo}</td>
            <td>${r.sistema ?? "—"}</td>
            <td>${r.conversation_id ? r.conversation_id.slice(0, 8) + "…" : "—"}</td>
            <td><code>${JSON.stringify(r.detalhe)}</code></td>
        `;
        tbody.appendChild(tr);
    }
}

document.getElementById("filtro-auditoria-sistema").addEventListener("change", carregarAuditoria);

// ---------------------------------------------------------------------------
// Utilitários
// ---------------------------------------------------------------------------
function formatarData(iso) {
    return new Date(iso).toLocaleString("pt-PT");
}

// ---------------------------------------------------------------------------
// Arranque
// ---------------------------------------------------------------------------
(async function iniciar() {
    const statusEl = document.getElementById("status-conexao");
    try {
        await fetch("/health").then((r) => r.json());
        statusEl.textContent = "Ligado ao serviço.";
        await carregarSistemas();
        await carregarConversas();
        await carregarAuditoria();
    } catch (e) {
        statusEl.textContent = "Não foi possível contactar o serviço.";
    }
})();
