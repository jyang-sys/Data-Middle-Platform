// API calls wait for the session check; credentials never enter URLs or localStorage.
window.appAuthReady = (async () => {
  if (window.__STATIC_PREVIEW__) {
    window.appUser = { role: "admin" };
    return;
  }
  const response = await fetch("/api/auth/session");
  if (!response.ok) throw new Error("无法检查登录状态");
  const session = await response.json();
  window.appUser = session.user || { role: "admin", local: true };
  window.appDataMode = session.data_mode;
  if (session.data_mode === "synthetic") {
    const banner = document.getElementById("preview-banner");
    banner.textContent = "演示数据 · 独立本地数据库";
    banner.hidden = false;
    document
      .querySelectorAll("[data-base-sync],#sync-top,#sync-connection")
      .forEach((el) => (el.disabled = true));
  }
  const dialog = document.getElementById("auth-dialog"),
    form = document.getElementById("auth-form");
  document.getElementById("auth-title").textContent = session.configured
    ? "登录数据中台"
    : "设置管理员账号";
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    const body = {
      username: document.getElementById("auth-username").value,
      password: document.getElementById("auth-password").value,
    };
    const send = async (path) => {
      const r = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const e = await r.json();
        throw new Error(
          typeof e.detail === "string" ? e.detail : "请检查账号和密码",
        );
      }
      return r;
    };
    try {
      if (!session.configured) await send("/api/auth/setup");
      await send("/api/auth/login");
      location.reload();
    } catch (e) {
      document.getElementById("auth-error").textContent = e.message;
    } finally {
      button.disabled = false;
    }
  });
  const account = document.getElementById("account-button");
  account.textContent = session.configured
    ? session.user?.name || "登录"
    : "启用账号权限";
  account.addEventListener("click", async () => {
    if (session.user) {
      await fetch("/api/auth/logout", { method: "POST" });
      location.reload();
    } else dialog.showModal();
  });
  if (session.configured && !session.user) {
    dialog.showModal();
    dialog.addEventListener("cancel", (e) => e.preventDefault());
    return new Promise(() => {});
  }
  const admin = window.appUser.role === "admin";
  document
    .querySelectorAll("[data-admin]")
    .forEach((el) => (el.hidden = !admin));
  if (!admin)
    document
      .querySelectorAll("#stock-refresh,[data-base-sync]")
      .forEach((el) => (el.disabled = true));
})();
