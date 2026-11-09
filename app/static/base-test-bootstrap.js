// The credential stays in the Site's server secret. Browsers receive sanitized data only.
function applyBaseTestSnapshot(snapshot, initial = false) {
  if (snapshot.data_mode !== "base_test" || !snapshot.analysis)
    throw new Error("测试数据格式不正确");
  const old = window.__DATA_SNAPSHOT__;
  const start = document.getElementById("report-start"),
    end = document.getElementById("report-end");
  const follow = end && (!end.value || end.value === old?.filters.end);
  const days =
    start && end && start.value && end.value
      ? Math.round(
          (Date.parse(end.value) - Date.parse(start.value)) / 86400000,
        ) + 1
      : 14;
  window.__DATA_SNAPSHOT__ = snapshot;
  window.appDataMode = "base_test";
  if (start && end && (initial || follow)) {
    end.value = snapshot.filters.end;
    const date = new Date(snapshot.filters.end + "T00:00:00Z");
    date.setUTCDate(date.getUTCDate() - days + 1);
    start.value = date.toISOString().slice(0, 10);
  }
  for (const id of ["report-start", "report-end"]) {
    const field = document.getElementById(id);
    if (field) {
      field.min = snapshot.analysis.filters.start;
      field.max = snapshot.analysis.filters.end;
    }
  }
}
window.baseTestReady = fetch("/api/base-test/snapshot", {
  credentials: "same-origin",
  cache: "no-store",
})
  .then(async (response) => {
    if (!response.ok) throw new Error("暂时无法读取测试数据");
    applyBaseTestSnapshot(await response.json(), true);
  })
  .catch(() => {
    window.baseTestWarning = "当前显示上次成功同步的测试数据";
  });
window.refreshBaseTestSnapshot = async function () {
  const response = await fetch("/api/base-test/refresh", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: "{}",
    cache: "no-store",
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : "测试数据同步失败，保留上次数据",
    );
  applyBaseTestSnapshot(result);
  window.baseTestWarning = null;
};
document.addEventListener(
  "click",
  (event) => {
    const button = event.target.closest?.(
      "#inventory-refresh,#stock-refresh,#company-refresh",
    );
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    button.disabled = true;
    Promise.resolve()
      .then(() => window.refreshBaseTestSnapshot())
      .then(async () => {
        await loadBusinessOptions();
        stockState.data = stockState.options = null;
        if (button.id === "company-refresh") await refreshCompanyCatalog();
        else if (button.id === "stock-refresh")
          await initializeStockDashboard(true);
        else await refreshInventoryBusiness();
      })
      .catch((error) => notice(error.message, true))
      .finally(() => (button.disabled = false));
  },
  true,
);
