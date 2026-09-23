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
    const data = new FormData(e.target);
    const errorEl = document.getElementById("login-error");
    errorEl.hidden = true;

    const resp = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: data.get("username"), password: data.get("password") }),
    });

    if (resp.ok) {
        window.location.href = "."; // relative - respects a ROOT_PATH reverse-proxy prefix
        return;
    }
    errorEl.textContent = resp.status === 429
        ? "Too many failed attempts. Wait a few minutes and try again."
        : "Invalid username or password.";
    errorEl.hidden = false;
});
