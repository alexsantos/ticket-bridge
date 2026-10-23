/**
 * login.js
 * --------
 * Handles the sign-in form on login.html. POSTs to api/v1/auth/login,
 * which sets an httpOnly session cookie on success - this script never
 * sees or stores the session token itself.
 */

const API_BASE = "api/v1";

document.getElementById("form-login").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = new FormData(form);
    const errorEl = document.getElementById("login-error");
    const submit = form.querySelector("[type=submit]");
    errorEl.hidden = true;

    if (!data.get("username") || !data.get("password")) {
        errorEl.textContent = "Enter your username and password.";
        errorEl.hidden = false;
        return;
    }

    submit.disabled = true;
    submit.textContent = "Signing in…";
    const resp = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: data.get("username"), password: data.get("password") }),
    }).catch(() => null);

    if (resp?.ok) {
        window.location.href = "."; // relative - respects a ROOT_PATH reverse-proxy prefix
        return;
    }
    submit.disabled = false;
    submit.textContent = "Sign in";
    errorEl.textContent = !resp
        ? "Can't reach the service. Check your connection and try again."
        : resp.status === 429
            ? "Too many failed attempts. Wait a few minutes, then try again."
            : "That username and password don't match.";
    errorEl.hidden = false;
    form.password.select();
});
