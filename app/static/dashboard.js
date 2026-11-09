const homeState = {
  trend: "all",
  team: "groups",
  tab: "platforms",
  drawer: null,
  drawerRequest: 0,
  companyNode: null,
  companyRequest: 0,
};
const homePalette = { amazon: "#efb451", ebay: "#6a91ee", tiktok: "#5770be" };
const homeColor = (code, index = 0) =>
  homePalette[code] || ["#6085ec", "#5ebeb4", "#9d87d9", "#78a3c9"][index % 4];
const homeEl = (tag, cls, text) => {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};
const homeShortName = (value) => String(value || "").split(" · ")[0];
const homeDateText = (value) => value?.replaceAll("-", "/") || "";

function setHomeTab(name, scroll = false) {
  homeState.tab = name;
  document.querySelectorAll("[data-home-tab]").forEach((b) => {
    const active = b.dataset.homeTab === name;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", String(active));
  });
  document
    .querySelectorAll("[data-home-panel]")
    .forEach((panel) => (panel.hidden = panel.dataset.homePanel !== name));
  if (name === "orders" && reportState.params && !reportState.detailKey)
    loadReportDetails().catch((e) => notice(e.message, true));
  if (scroll)
    document
      .querySelector(".home-workspace")
      .scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderSalesHome(data) {
  if (!data) return;
  const { comparisons } = data,
    products = (comparisons?.products || []).filter((p) => p.quantity > 0),
    shops = (comparisons?.platforms || [])
      .flatMap((p) => p.children)
      .filter((s) => s.quantity > 0);
  $("home-active-products").textContent = number(products.length);
  $("home-active-shops").textContent = number(shops.length);
  $("home-period-label").textContent =
    homeDateText(data.filters.start) + " — " + homeDateText(data.filters.end);
  $("home-source-label").textContent =
    window.__DATA_SNAPSHOT__?.data_mode === "synthetic" ||
    window.appDataMode === "synthetic"
      ? "演示数据 · 试算"
      : "销量试算";
  $("home-source-label").title =
    "按商品件数统计；取消、退款、退货的最终扣减规则待确认";
  $("home-units-foot").textContent =
    "日均 " + number(data.total_units / Math.max(1, data.daily.length)) + " 件";
  $("home-orders-foot").textContent =
    "平均每单 " +
    number(data.order_count ? data.total_units / data.order_count : 0) +
    " 件";
  $("home-products-foot").textContent =
    "覆盖 " + (comparisons?.hierarchy?.length || 0) + " 个产品父体";
  $("home-shops-foot").textContent =
    "覆盖 " + data.platforms.length + " 个平台";
  $("home-spark-orders").replaceChildren(
    homeEl(
      "span",
      "home-kpi-tag",
      data.filters.confirmed_only ? "已确认订单" : "全部确认状态",
    ),
  );
  renderHomeSpark(data.daily.map((r) => r.total));
  const count =
    Math.round(
      (Date.parse(data.filters.end) - Date.parse(data.filters.start)) /
        86400000,
    ) + 1;
  const anchor = window.__DATA_SNAPSHOT__?.filters.end || reportToday();
  document.querySelectorAll("[data-report-days]").forEach((b) => {
    const active =
      Number(b.dataset.reportDays) === count && data.filters.end === anchor;
    b.classList.toggle("active", active);
    b.setAttribute("aria-pressed", String(active));
  });
  if (
    homeState.trend !== "all" &&
    !data.platforms.some((p) => p.id === homeState.trend)
  )
    homeState.trend = "all";
  $("home-trend-platforms").replaceChildren();
  for (const platform of [{ id: "all", name: "全部" }, ...data.platforms]) {
    const b = homeEl(
      "button",
      platform.id === homeState.trend ? "active" : "",
      platform.name,
    );
    b.type = "button";
    b.setAttribute("aria-pressed", String(platform.id === homeState.trend));
    b.addEventListener("click", () => {
      homeState.trend = platform.id;
      renderSalesHome(reportState.data);
    });
    $("home-trend-platforms").append(b);
  }
  renderHomeTrend(data);
  renderHomePlatforms(data);
  renderHomeProducts(data);
  renderHomeTeams(data);
  $("report-export").setAttribute("aria-disabled", "false");
  $("report-export").textContent = window.__DATA_SNAPSHOT__
    ? "↓ 导出 CSV"
    : "↓ 导出报表";
  if (homeState.tab === "orders" && !reportState.detailKey)
    loadReportDetails().catch((e) => notice(e.message, true));
}

function homeSvg(tag, attributes = {}, text) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attributes).forEach(([k, v]) => el.setAttribute(k, v));
  if (text !== undefined) el.textContent = text;
  return el;
}
function renderHomeSpark(values) {
  const max = Math.max(1, ...values),
    svg = homeSvg("svg", { viewBox: "0 0 80 26", "aria-hidden": "true" }),
    points = values.map((v, i) => [
      (i * 80) / Math.max(1, values.length - 1),
      23 - (v / max) * 18,
    ]);
  if (points.length) {
    svg.append(
      homeSvg("path", {
        d:
          "M " +
          points[0][0] +
          " 26 L " +
          points.map((p) => p.join(" ")).join(" L ") +
          " L 80 26 Z",
        fill: "#dfe8ff",
      }),
    );
    svg.append(
      homeSvg("polyline", {
        points: points.map((p) => p.join(",")).join(" "),
        fill: "none",
        stroke: "#7395ed",
        "stroke-width": 1.5,
      }),
    );
  }
  $("home-spark-units").replaceChildren(svg);
}

function renderHomeTrend(data) {
  const root = $("report-trend"),
    width = Math.max(230, root.clientWidth - 24),
    height = 190,
    left = 34,
    right = 15,
    top = 12,
    bottom = 26;
  const rows = data.daily,
    values = rows.map((r) =>
      homeState.trend === "all" ? r.total : r.platforms[homeState.trend] || 0,
    ),
    maximum = Math.max(1, ...values),
    ceiling = Math.max(1, Math.ceil(maximum / 4) * 4);
  const label =
    homeState.trend === "all"
      ? "全平台"
      : data.platforms.find((p) => p.id === homeState.trend)?.name ||
        homeState.trend;
  const color =
      homeState.trend === "all" ? "#517cf2" : homeColor(homeState.trend),
    svg = homeSvg("svg", {
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": label + "每日销量趋势，单位件",
    });
  const defs = homeSvg("defs"),
    gradient = homeSvg("linearGradient", {
      id: "home-trend-gradient",
      x1: 0,
      y1: 0,
      x2: 0,
      y2: 1,
    });
  gradient.append(
    homeSvg("stop", {
      offset: "0%",
      "stop-color": color,
      "stop-opacity": 0.17,
    }),
    homeSvg("stop", {
      offset: "100%",
      "stop-color": color,
      "stop-opacity": 0.01,
    }),
  );
  defs.append(gradient);
  svg.append(defs);
  const x = (i) =>
      left + (i * (width - left - right)) / Math.max(1, rows.length - 1),
    y = (v) => height - bottom - (v / ceiling) * (height - top - bottom);
  for (let i = 0; i <= 4; i++) {
    const value = (ceiling * i) / 4,
      yy = y(value);
    svg.append(
      homeSvg("line", {
        x1: left,
        x2: width - right,
        y1: yy,
        y2: yy,
        stroke: "#edf1f8",
        "stroke-dasharray": i ? "3 4" : "0",
      }),
    );
    svg.append(
      homeSvg(
        "text",
        {
          x: left - 9,
          y: yy + 3,
          "text-anchor": "end",
          fill: "#a3b0c4",
          "font-size": 9,
        },
        String(Number(value.toFixed(1))),
      ),
    );
  }
  const ticks = new Set([
    0,
    Math.round((rows.length - 1) / 4),
    Math.round((rows.length - 1) / 2),
    Math.round(((rows.length - 1) * 3) / 4),
    rows.length - 1,
  ]);
  for (const i of ticks)
    if (rows[i])
      svg.append(
        homeSvg(
          "text",
          {
            x: x(i),
            y: height - 6,
            "text-anchor":
              i === 0 ? "start" : i === rows.length - 1 ? "end" : "middle",
            fill: "#a3b0c4",
            "font-size": 9,
          },
          rows[i].date.slice(5).replace("-", "/"),
        ),
      );
  if (values.length) {
    const points = values.map((v, i) => [x(i), y(v)]);
    svg.append(
      homeSvg("path", {
        d:
          `M ${x(0)} ${y(0)} L ` +
          points.map((p) => p.join(" ")).join(" L ") +
          ` L ${x(rows.length - 1)} ${y(0)} Z`,
        fill: "url(#home-trend-gradient)",
      }),
    );
    svg.append(
      homeSvg("polyline", {
        points: points.map((p) => p.join(",")).join(" "),
        fill: "none",
        stroke: color,
        "stroke-width": 2.3,
        "stroke-linejoin": "round",
        "stroke-linecap": "round",
      }),
    );
    const peak = values.indexOf(Math.max(...values));
    svg.append(
      homeSvg("circle", {
        cx: x(peak),
        cy: y(values[peak]),
        r: 4,
        fill: color,
        stroke: "#fff",
        "stroke-width": 2,
      }),
    );
  }
  const cursor = homeSvg("line", {
    x1: 0,
    x2: 0,
    y1: top,
    y2: height - bottom,
    stroke: color,
    "stroke-dasharray": "3 3",
    visibility: "hidden",
  });
  svg.append(cursor);
  const tooltip = homeEl("div", "home-tooltip");
  tooltip.hidden = true;
  rows.forEach((row, i) => {
    const step = (width - left - right) / Math.max(1, rows.length - 1),
      hit = homeSvg("rect", {
        x: Math.max(left, x(i) - step / 2),
        y: top,
        width: Math.min(step, width - right - Math.max(left, x(i) - step / 2)),
        height: height - bottom - top,
        fill: "transparent",
        tabindex: 0,
        "aria-label": row.date + " " + label + " " + values[i] + "件",
      });
    const show = () => {
      cursor.setAttribute("x1", x(i));
      cursor.setAttribute("x2", x(i));
      cursor.setAttribute("visibility", "visible");
      tooltip.textContent = row.date + " · " + label + " " + values[i] + " 件";
      tooltip.hidden = false;
      tooltip.style.left = Math.max(4, Math.min(x(i) - 50, width - 185)) + "px";
      tooltip.style.top = Math.max(0, y(values[i]) - 30) + "px";
    };
    const hide = () => {
      cursor.setAttribute("visibility", "hidden");
      tooltip.hidden = true;
    };
    hit.addEventListener("mouseenter", show);
    hit.addEventListener("focus", show);
    hit.addEventListener("mouseleave", hide);
    hit.addEventListener("blur", hide);
    svg.append(hit);
  });
  root.replaceChildren(svg, tooltip);
  const peak = Math.max(0, ...values),
    peakIndex = values.indexOf(peak);
  $("home-trend-caption").textContent = label + " · 单位：件";
  $("home-trend-peak").textContent =
    peakIndex >= 0
      ? "单日峰值 " +
        peak +
        " 件 · " +
        rows[peakIndex].date.slice(5).replace("-", "/")
      : "暂无销量";
  $("home-trend-days").textContent =
    "共 " +
    rows.length +
    " 天 · 合计 " +
    number(values.reduce((a, b) => a + b, 0)) +
    " 件";
}

function renderHomePlatforms(data) {
  const root = $("home-platform-chart"),
    list = homeEl("div", "home-platform-list"),
    total = homeEl("div", "home-platform-total");
  total.append(
    homeEl("strong", "", number(data.total_units)),
    homeEl("span", "", "件 / " + data.platforms.length + " 个平台"),
  );
  list.append(total);
  const platforms = data.comparisons?.platforms || [];
  for (const [index, p] of platforms.entries()) {
    const row = homeEl("div", "home-channel-row"),
      icon = homeEl(
        "span",
        "home-brand-icon " + p.platform,
        { amazon: "a", ebay: "e", tiktok: "♪" }[p.platform] ||
          p.name.slice(0, 1),
      ),
      body = homeEl("div"),
      name = homeEl("div", "home-channel-name"),
      button = homeEl("button", "home-text-button", p.name);
    button.title = "展开 " + p.name + " 的店铺";
    button.addEventListener("click", () => {
      setHomeTab("platforms", true);
      for (const detail of $("performance-platforms").querySelectorAll(
        ":scope > details",
      ))
        if (detail.querySelector("summary span")?.textContent === p.name)
          detail.open = true;
    });
    name.append(button, homeEl("span", "", p.share.toFixed(1) + "%"));
    const progress = homeEl("div", "home-progress"),
      fill = homeEl("span");
    fill.style.width = p.share + "%";
    fill.style.background = homeColor(p.platform, index);
    progress.append(fill);
    body.append(name, progress);
    row.append(
      icon,
      body,
      homeEl("span", "home-channel-value", number(p.quantity)),
    );
    list.append(row);
  }
  if (!platforms.length)
    list.append(homeEl("div", "home-empty", "当前筛选暂无销售数据"));
  root.replaceChildren(
    list,
    homeEl(
      "div",
      "home-platform-note",
      "按平台订单来源统计 · 点击平台展开店铺",
    ),
  );
}

function renderHomeProducts(data) {
  const products = (data.comparisons?.products || [])
    .filter((p) => p.quantity > 0)
    .slice(0, 5);
  table(
    "home-product-ranking",
    ["排名", "产品 / 规格", "销量", "占比", "渠道", ""],
    products.map((p, i) => [
      i + 1,
      homeShortName(p.name),
      number(p.quantity),
      p.share.toFixed(1) + "%",
      p.children.length + " 个平台",
      "",
    ]),
  );
  $("home-product-ranking")
    .querySelectorAll("tbody tr")
    .forEach((tr, i) => {
      const p = products[i];
      tr.children[0].replaceChildren(
        homeEl(
          "span",
          "home-rank-number " + (i < 3 ? "top" : ""),
          String(i + 1),
        ),
      );
      const product = homeEl("div", "home-product-cell"),
        description = homeEl("div");
      description.append(
        homeEl("div", "home-product-name", homeShortName(p.name)),
        homeEl(
          "div",
          "home-product-code",
          p.key[0] === "company"
            ? business.nodes.find((n) => n.id === Number(p.key[1]))?.code || ""
            : "跨平台商品汇总",
        ),
      );
      product.append(homeEl("span", "home-product-icon", "▯"), description);
      tr.children[1].replaceChildren(product);
      const share = homeEl("div", "home-share-cell", p.share.toFixed(1) + "%"),
        bar = homeEl("div", "home-progress"),
        fill = homeEl("span");
      fill.style.width = p.share + "%";
      bar.append(fill);
      share.append(bar);
      tr.children[3].replaceChildren(share);
      const button = homeEl("button", "home-open-detail", "查看 ↗");
      button.addEventListener("click", () =>
        openHomeDetails(
          { key_json: JSON.stringify(p.key) },
          homeShortName(p.name),
        ).catch((e) => notice(e.message, true)),
      );
      tr.lastElementChild.replaceChildren(button);
    });
}

function renderHomeTeams(data) {
  document.querySelectorAll("[data-home-team]").forEach((b) => {
    const active = b.dataset.homeTeam === homeState.team;
    b.classList.toggle("active", active);
    b.setAttribute("aria-pressed", String(active));
  });
  const list = homeEl("div", "home-team-list"),
    rows = (data[homeState.team] || []).slice(0, 5),
    max = Math.max(1, ...rows.map((r) => r.quantity));
  for (const [index, row] of rows.entries()) {
    const wrap = homeEl(
        "div",
        "home-team-row " + (row.name === "未分配" ? "unassigned" : ""),
      ),
      content = homeEl("div", "home-team-content"),
      bar = homeEl("div", "home-progress"),
      fill = homeEl("span");
    fill.style.width = (row.quantity / max) * 100 + "%";
    fill.style.background = [
      "#7b9af0",
      "#7dbbb7",
      "#a599d0",
      "#8db8d2",
      "#b3bcd0",
    ][index];
    bar.append(fill);
    const label = homeEl("button", "home-text-button home-team-name", row.name);
    label.addEventListener("click", () => {
      const scope = {};
      scope[
        homeState.team === "groups" ? "group_key_json" : "employee_key_json"
      ] = JSON.stringify([row.identity_type, row.id]);
      openHomeDetails(scope, row.name).catch((e) => notice(e.message, true));
    });
    content.append(label, bar);
    const value = homeEl(
      "div",
      "home-team-value",
      number(row.quantity) + " 件",
    );
    value.append(
      homeEl(
        "small",
        "",
        data.total_units
          ? ((row.quantity / data.total_units) * 100).toFixed(1) + "%"
          : "0%",
      ),
    );
    wrap.append(
      homeEl(
        "span",
        "home-team-avatar",
        row.name === "未分配" ? "?" : row.name.slice(-2),
      ),
      content,
      value,
    );
    list.append(wrap);
  }
  if (!rows.length)
    list.append(homeEl("div", "home-empty", "当前筛选暂无业绩"));
  $("home-team-ranking").replaceChildren(list);
}

async function homeDetailPage(offset = 0) {
  const state = homeState.drawer;
  if (!state) return;
  const request = ++homeState.drawerRequest,
    params = new URLSearchParams(state.params);
  Object.entries(state.scope).forEach(([key, value]) => params.set(key, value));
  params.set("limit", "50");
  params.set("offset", offset);
  const data = await api("/api/reports/details?" + params);
  if (request !== homeState.drawerRequest) return;
  state.offset = offset;
  state.total = data.total;
  $("home-detail-counts").replaceChildren();
  for (const [value, label] of [
    [data.total_units, "件销量"],
    [data.total, "条商品明细"],
  ]) {
    const item = homeEl("span");
    item.append(
      homeEl("strong", "", number(value)),
      document.createTextNode(label),
    );
    $("home-detail-counts").append(item);
  }
  table(
    "home-detail-table",
    ["订单日期", "订单号", "平台 / 店铺", "商品", "小组 / 负责人", "销量"],
    data.lines.map((r) => [
      r.date,
      r.external_order_id || r.order_id,
      r.platform_name + " / " + r.shop_name,
      homeShortName(r.label),
      [r.business_group || "未分配", r.sales_owner || "未分配"].join(" / "),
      r.quantity,
    ]),
    [5],
  );
  $("home-detail-page").textContent =
    (data.total ? offset + 1 : 0) +
    "–" +
    Math.min(offset + 50, data.total) +
    " / " +
    data.total;
  $("home-detail-prev").disabled = offset === 0;
  $("home-detail-next").disabled = offset + 50 >= data.total;
}
async function openHomeDetails(scope, title) {
  if (!reportState.params) throw new Error("请先查询销量");
  homeState.drawer = {
    scope,
    title,
    params: new URLSearchParams(reportState.params),
    offset: 0,
  };
  $("home-detail-title").textContent = title;
  $("home-detail-period").textContent =
    homeDateText(reportState.data.filters.start) +
    " — " +
    homeDateText(reportState.data.filters.end);
  $("home-detail-table").replaceChildren(
    homeEl("div", "home-loading", "正在读取明细…"),
  );
  $("home-detail-counts").replaceChildren();
  let key = null;
  try {
    key = JSON.parse(scope.key_json || "null");
  } catch (_) {}
  $("home-detail-stock").hidden = !(
    scope.node_id ||
    (key && ["company", "source"].includes(key[0]))
  );
  if (!$("home-detail-dialog").open) $("home-detail-dialog").showModal();
  await homeDetailPage();
}
async function exportHomeCsv() {
  if (!reportState.params) return;
  const lines = [],
    params = new URLSearchParams(reportState.params);
  let offset = 0;
  for (;;) {
    params.set("limit", "200");
    params.set("offset", offset);
    const data = await api("/api/reports/details?" + params);
    lines.push(...data.lines);
    offset += data.lines.length;
    if (offset >= data.total || !data.lines.length) break;
  }
  businessCsv(
    [
      "订单日期",
      "订单号",
      "平台",
      "店铺",
      "公司 SKU",
      "商品",
      "销售小组",
      "负责人",
      "销量",
    ],
    lines.map((r) => [
      r.date,
      r.external_order_id || r.order_id,
      r.platform_name,
      r.shop_name,
      r.company_sku || r.source_sku,
      homeShortName(r.label),
      r.business_group || "未分配",
      r.sales_owner || "未分配",
      r.quantity,
    ]),
    "销售明细-" +
      reportState.data.filters.start +
      "-" +
      reportState.data.filters.end +
      ".csv",
  );
}

function mountHomeInteractions() {
  $("home-advanced-toggle").addEventListener("click", () => {
    const expanded = $("home-advanced").hidden;
    $("home-advanced").hidden = !expanded;
    $("home-advanced-toggle").setAttribute("aria-expanded", String(expanded));
    $("home-advanced-toggle").firstChild.textContent = expanded
      ? "收起筛选 "
      : "更多筛选 ";
  });
  $("home-reset").addEventListener("click", () => {
    $("report-form").reset();
    recentReportDays(window.__DATA_SNAPSHOT__ ? 14 : 7);
    reportShopOptions();
    homeState.trend = "all";
    refreshSalesReport().catch((e) => notice(e.message, true));
  });
  $("home-refresh").addEventListener("click", async () => {
    const b = $("home-refresh");
    b.disabled = true;
    try {
      await refreshSalesReport();
    } catch (e) {
      notice(e.message, true);
    } finally {
      b.disabled = false;
    }
  });
  $("report-export").addEventListener("click", (event) => {
    if (window.__DATA_SNAPSHOT__) {
      event.preventDefault();
      exportHomeCsv().catch((e) => notice(e.message, true));
    }
  });
  document
    .querySelectorAll("[data-home-tab],[data-home-open]")
    .forEach((button) =>
      button.addEventListener("click", () =>
        setHomeTab(
          button.dataset.homeTab || button.dataset.homeOpen,
          Boolean(button.dataset.homeOpen),
        ),
      ),
    );
  document.querySelectorAll("[data-home-team]").forEach((button) =>
    button.addEventListener("click", () => {
      homeState.team = button.dataset.homeTeam;
      if (reportState.data) renderHomeTeams(reportState.data);
    }),
  );
  const dialog = homeEl("dialog", "home-drawer");
  dialog.id = "home-detail-dialog";
  dialog.innerHTML =
    '<div class="home-drawer-heading"><div><div class="home-eyebrow">SALES DETAILS</div><h2 id="home-detail-title"></h2><p id="home-detail-period"></p></div><div class="home-drawer-actions"><button id="home-detail-stock" class="button">查看库存 ↗</button><button id="home-detail-close" class="home-text-button" aria-label="关闭明细">✕</button></div></div><div id="home-detail-counts" class="home-drawer-counts"></div><div id="home-detail-table" class="table-wrap"></div><div class="home-drawer-pager"><button id="home-detail-prev" class="button">上一页</button><span id="home-detail-page"></span><button id="home-detail-next" class="button">下一页</button></div>';
  $("notice").before(dialog);
  $("home-detail-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => homeState.drawerRequest++);
  $("home-detail-prev").addEventListener("click", () =>
    homeDetailPage(Math.max(0, homeState.drawer.offset - 50)).catch((e) =>
      notice(e.message, true),
    ),
  );
  $("home-detail-next").addEventListener("click", () =>
    homeDetailPage(homeState.drawer.offset + 50).catch((e) =>
      notice(e.message, true),
    ),
  );
  $("home-detail-stock").addEventListener("click", () => {
    const scope = homeState.drawer.scope,
      key = scope.key_json ? JSON.parse(scope.key_json) : null;
    dialog.close();
    if (scope.node_id || key?.[0] === "company")
      linkCompanyInventory(Number(scope.node_id || key[1])).catch((e) =>
        notice(e.message, true),
      );
    else if (key?.[0] === "source")
      linkSourceStock(key)
        .then(() => {
          document.querySelector(".stock-source-section").open = true;
          document
            .querySelector(".stock-source-section")
            .scrollIntoView({ behavior: "smooth" });
        })
        .catch((e) => notice(e.message, true));
  });
  if (window.__DATA_SNAPSHOT__?.analysis) {
    const bounds = window.__DATA_SNAPSHOT__.analysis.filters;
    for (const id of ["report-start", "report-end"]) {
      $(id).min = bounds.start;
      $(id).max = bounds.end;
    }
    $("report-month").addEventListener("change", () => {
      if ($("report-start").value < bounds.start)
        $("report-start").value = bounds.start;
      if ($("report-end").value > bounds.end)
        $("report-end").value = bounds.end;
    });
  }
  let timer;
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (reportState.data && !$("sales-report").hidden)
        renderHomeTrend(reportState.data);
    }, 120);
  });
  if (reportState.data) renderSalesHome(reportState.data);
}

function companyPathContains(node, ancestor) {
  const visited = new Set();
  while (node && !visited.has(node.id)) {
    if (node.id === ancestor) return true;
    visited.add(node.id);
    node = business.nodes.find((n) => n.id === node.parent_id);
  }
  return false;
}
function companyLeaves(node) {
  return business.nodes.filter(
    (n) => n.kind === "sku" && companyPathContains(n, node.id),
  );
}
function renderCompanyNavigation() {
  const root = $("company-browser-tree");
  if (!root) return;
  const query = $("company-browser-search").value.toLowerCase();
  root.replaceChildren();
  const matches = (node) =>
    !query ||
    [node.name, node.code].join(" ").toLowerCase().includes(query) ||
    business.nodes.some(
      (n) =>
        companyPathContains(n, node.id) &&
        [n.name, n.code].join(" ").toLowerCase().includes(query),
    );
  function branch(parent, host) {
    for (const node of business.nodes.filter(
      (n) => n.parent_id === parent && matches(n),
    )) {
      if (node.kind === "group") {
        const detail = homeEl("details"),
          summary = homeEl(
            "summary",
            node.id === homeState.companyNode ? "selected" : "",
          );
        summary.dataset.companyNode = node.id;
        summary.append(
          homeEl("span", "", node.name),
          homeEl("small", "", companyLeaves(node).length + " SKU"),
        );
        summary.addEventListener("click", () => selectCompanyNode(node.id));
        detail.open =
          parent === null ||
          companyPathContains(
            business.nodes.find((n) => n.id === homeState.companyNode),
            node.id,
          );
        detail.append(summary);
        branch(node.id, detail);
        host.append(detail);
      } else {
        const button = homeEl(
          "button",
          "company-leaf " +
            (node.id === homeState.companyNode ? "selected" : ""),
          node.name,
        );
        button.dataset.companyNode = node.id;
        button.addEventListener("click", () => selectCompanyNode(node.id));
        host.append(button);
      }
    }
  }
  branch(null, root);
  if (!root.children.length)
    root.append(homeEl("div", "home-empty", "未找到匹配的产品"));
}
async function selectCompanyNode(identity) {
  homeState.companyNode = identity;
  document
    .querySelectorAll("[data-company-node]")
    .forEach((el) =>
      el.classList.toggle(
        "selected",
        Number(el.dataset.companyNode) === identity,
      ),
    );
  const node = business.nodes.find((n) => n.id === identity);
  if (!node) return;
  const root = $("company-browser-detail"),
    request = ++homeState.companyRequest;
  root.replaceChildren(homeEl("div", "home-loading", "正在读取产品表现…"));
  try {
    const params = new URLSearchParams(
      reportState.params ||
        new URLSearchParams(window.__DATA_SNAPSHOT__?.filters || {}),
    );
    params.set("node_id", identity);
    params.set("granularity", "company_product");
    const [sales, inventory] = await Promise.all([
      api("/api/reports/sales?" + params),
      window.__DATA_SNAPSHOT__
        ? api("/api/allocations/view").then((data) =>
            filterInventorySnapshot(
              data,
              new URLSearchParams({ node_id: identity }),
            ),
          )
        : api(
            "/api/allocations/view?" +
              new URLSearchParams({ node_id: identity }),
          ),
    ]);
    if (request !== homeState.companyRequest) return;
    root.replaceChildren();
    root.append(
      homeEl(
        "div",
        "company-detail-type",
        node.kind === "sku" ? "具体规格 SKU" : "产品父体 / 分组",
      ),
    );
    const title = homeEl("div", "company-detail-title");
    title.append(
      homeEl("h2", "", node.name),
      homeEl("span", "home-pill", node.active ? "启用" : "停用"),
    );
    root.append(title, homeEl("div", "company-detail-code", node.code));
    const actions = homeEl("div", "company-detail-actions"),
      salesButton = homeEl("button", "button primary", "查看销售表现 ↗"),
      stockButton = homeEl("button", "button", "查看库存分布 ↗");
    salesButton.addEventListener("click", () => {
      $("report-node").value = identity;
      switchTab("sales-report");
      setHomeTab("products");
    });
    stockButton.addEventListener("click", () =>
      linkCompanyInventory(identity).catch((e) => notice(e.message, true)),
    );
    actions.append(salesButton, stockButton);
    root.append(actions);
    const metrics = homeEl("div", "company-detail-metrics"),
      physical = inventory.products.reduce(
        (sum, p) => sum + Number(p.physical_total),
        0,
      ),
      unknown = inventory.products.reduce(
        (sum, p) => sum + p.unknown_physical_rows,
        0,
      );
    for (const [label, value] of [
      ["下级具体规格", companyLeaves(node).length],
      ["本期销量", number(sales.total_units) + " 件"],
      [
        "已确认实物",
        physical === 0 && unknown ? "待确认" : number(physical) + " 台",
      ],
    ]) {
      const item = homeEl("div", "company-detail-metric");
      item.append(
        homeEl("span", "", label),
        homeEl("strong", "", String(value)),
      );
      metrics.append(item);
    }
    root.append(metrics);
    if (unknown)
      root.append(
        homeEl(
          "p",
          "business-muted",
          "另有 " + unknown + " 条库存明细待确认类型",
        ),
      );
    const wrap = homeEl("div", "table-wrap");
    wrap.id = "company-detail-specs";
    root.append(wrap);
    const salesById = new Map(
      sales.comparisons.products
        .filter((p) => p.key[0] === "company")
        .map((p) => [Number(p.key[1]), p.quantity]),
    );
    const stockById = new Map(
      inventory.products.map((p) => [p.company_node_id, p]),
    );
    table(
      "company-detail-specs",
      ["具体规格", "颜色 / 配置", "本期销量", "已确认实物"],
      companyLeaves(node).map((leaf) => {
        const stock = stockById.get(leaf.id);
        return [
          leaf.name,
          [leaf.attributes?.color, leaf.attributes?.configuration]
            .filter(Boolean)
            .join(" / ") || "—",
          number(salesById.get(leaf.id) || 0),
          !stock ||
          (stock.unknown_physical_rows && Number(stock.physical_total) === 0)
            ? "待确认"
            : number(stock.physical_total),
        ];
      }),
    );
  } catch (error) {
    if (request === homeState.companyRequest)
      root.replaceChildren(homeEl("div", "home-empty", error.message));
  }
}

const originalRefreshCompanyCatalog = refreshCompanyCatalog;
refreshCompanyCatalog = async function () {
  await originalRefreshCompanyCatalog();
  const root = $("company-tree");
  if (!root) return;
  const browser = homeEl("div", "company-browser"),
    nav = homeEl("div", "company-browser-nav"),
    search = homeEl("input", "company-search");
  search.id = "company-browser-search";
  search.placeholder = "搜索父体、规格或公司 SKU";
  search.setAttribute("aria-label", "搜索产品层级");
  const tree = homeEl("div", "company-browser-tree");
  tree.id = "company-browser-tree";
  nav.append(search, tree);
  const detail = homeEl("div", "company-detail");
  detail.id = "company-browser-detail";
  browser.append(nav, detail);
  root.replaceChildren(browser);
  if (!business.nodes.some((n) => n.id === homeState.companyNode))
    homeState.companyNode =
      business.nodes.find((n) => n.parent_id === null)?.id || null;
  search.addEventListener("input", renderCompanyNavigation);
  renderCompanyNavigation();
  if (homeState.companyNode) await selectCompanyNode(homeState.companyNode);
};

const originalRenderInventoryBusiness = renderInventoryBusiness;
renderInventoryBusiness = function () {
  originalRenderInventoryBusiness();
  const data = business.inventory;
  if (!data) return;
  const physical = data.products.reduce(
      (sum, p) => sum + Number(p.physical_total),
      0,
    ),
    pending = data.rows.filter(
      (r) =>
        r.stock_kind === "unknown" ||
        r.allocation_conflict ||
        r.channel_conflict,
    ).length,
    warehouses = new Set(data.rows.map((r) => r.warehouse_key)).size;
  $("inventory-business-metrics").replaceChildren();
  for (const [label, value] of [
    ["已确认实物", physical],
    ["库存规格", data.products.length],
    ["覆盖仓库", warehouses],
    ["待核对明细", pending],
  ]) {
    const item = homeEl("span", "", label);
    item.append(
      homeEl(
        "strong",
        "",
        label === "已确认实物" && physical === 0 && pending
          ? "待确认"
          : number(value),
      ),
    );
    $("inventory-business-metrics").append(item);
  }
  table(
    "inventory-product-table",
    ["公司 SKU / 来源 SKU", "产品名称", "已确认实物", "仓库分布", "待确认明细"],
    data.products.map((p) => [
      p.sku,
      p.name,
      p.unknown_physical_rows && Number(p.physical_total) === 0
        ? "待确认"
        : number(p.physical_total),
      Object.values(p.warehouses)
        .map(
          (w) =>
            w.name +
            "：" +
            (w.unknown_rows && Number(w.physical) === 0
              ? "待确认"
              : number(w.physical)) +
            (w.unknown_rows && Number(w.physical) > 0 ? "（另有待确认）" : ""),
        )
        .join("；"),
      p.unknown_physical_rows,
    ]),
  );
};
const inventoryHeading = homeEl("div", "inventory-business-heading"),
  inventoryHeadingText = homeEl("div");
inventoryHeadingText.append(
  homeEl("h1", "", "库存中心"),
  homeEl("p", "", "统一查看公司规格、仓库分布与小组份额"),
);
inventoryHeading.append(
  inventoryHeadingText,
  homeEl("span", "home-pill", "来源库存与份额分开核对"),
);
$("inventory-business").before(inventoryHeading);
mountHomeInteractions();
