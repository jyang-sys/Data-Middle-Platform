const business = {
  nodes: [],
  groups: [],
  employees: [],
  shops: [],
  catalogs: [],
  rules: [],
  links: [],
  ruleVersion: 0,
  productVersion: 0,
  linkVersion: 0,
  preview: null,
  inventory: null,
  inventoryOffset: 0,
  editingSource: null,
};
const businessAdmin = () => !window.appUser || window.appUser.role === "admin";
const businessRun = (action) =>
  Promise.resolve()
    .then(action)
    .catch((error) => notice(error.message, true));
const businessJson = (body) => ({
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
function businessSelect(id, rows, empty, value, label) {
  const select = $(id),
    previous = select.value;
  select.replaceChildren(new Option(empty, ""));
  rows.forEach((row) => select.append(new Option(label(row), value(row))));
  if ([...select.options].some((o) => o.value === previous))
    select.value = previous;
}
function companyChoices() {
  const result = [];
  function visit(parent, depth) {
    business.nodes
      .filter((n) => n.parent_id === parent)
      .forEach((n) => {
        result.push({
          ...n,
          treeLabel:
            "　".repeat(depth) +
            n.name +
            " · " +
            n.code +
            (n.active ? "" : "（停用）"),
        });
        visit(n.id, depth + 1);
      });
  }
  visit(null, 0);
  return result;
}
function businessShopChoices(platformId, shopId, empty = "全部店铺") {
  businessSelect(
    shopId,
    business.shops.filter((s) => s.platform === $(platformId).value),
    empty,
    (s) => s.shop_id,
    (s) => s.name,
  );
  $(shopId).disabled = !$(platformId).value;
}
function businessActor() {
  const actor = $("change-actor").value.trim() || window.appUser?.name || "";
  if (!actor) throw new Error("请填写操作人");
  return actor;
}
function businessReason() {
  return $("change-reason").value.trim();
}
async function loadBusinessOptions() {
  const [nodes, groups, employees, options, stocks] = await Promise.all([
    api("/api/catalog/products"),
    api("/api/management/groups"),
    api("/api/management/employees"),
    api("/api/reports/options"),
    api("/api/stock-dashboard/options"),
  ]);
  Object.assign(business, {
    nodes,
    groups,
    employees,
    shops: options.shops,
    platforms: options.platforms,
    catalogs: stocks.catalogs,
  });
  const tree = companyChoices();
  for (const id of ["report-node", "assignment-node", "inventory-node"])
    businessSelect(
      id,
      tree,
      "全部 / 未指定",
      (n) => n.id,
      (n) => n.treeLabel,
    );
  businessSelect(
    "company-parent",
    tree.filter((n) => n.kind === "group" && n.active),
    "顶层产品",
    (n) => n.id,
    (n) => n.treeLabel,
  );
  businessSelect(
    "link-node",
    tree.filter((n) => n.kind === "sku" && n.active),
    "选择具体规格",
    (n) => n.id,
    (n) => n.treeLabel,
  );
  for (const id of ["assignment-group", "inventory-group", "user-group"])
    businessSelect(
      id,
      groups,
      "全部 / 未指定",
      (g) => g.id,
      (g) => g.name + (g.active ? "" : "（停用）"),
    );
  $("inventory-group").append(new Option("未分配", "unassigned"));
  for (const id of ["assignment-employee", "user-employee"])
    businessSelect(
      id,
      employees,
      "未指定",
      (e) => e.id,
      (e) => e.name + (e.active ? "" : "（停用）"),
    );
  for (const id of [
    "assignment-platform",
    "inventory-platform",
    "link-platform",
  ])
    businessSelect(
      id,
      options.platforms,
      "全部 / 未指定",
      (p) => p.id,
      (p) => p.name,
    );
  businessShopChoices("assignment-platform", "assignment-shop");
  businessShopChoices("link-platform", "link-shop");
  businessSelect(
    "assignment-catalog",
    stocks.catalogs,
    "全部目录",
    (c) => c.id,
    (c) => c.name,
  );
  businessSelect(
    "inventory-catalog",
    stocks.catalogs,
    "全部目录",
    (c) => c.id,
    (c) => c.name,
  );
  businessSelect(
    "inventory-warehouse",
    stocks.warehouses.filter(
      (w, i, a) => a.findIndex((v) => v.id === w.id) === i,
    ),
    "全部仓库",
    (w) => w.id,
    (w) => w.name,
  );
  business.loaded = true;
  if (window.appUser?.name) $("company-actor").value = window.appUser.name;
  document
    .querySelectorAll("[data-admin]")
    .forEach((el) => (el.hidden = !businessAdmin()));
}

function renderPerformance(data) {
  if (!data) return;
  function render(target, rows, kind) {
    $(target).replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "暂无数据";
      $(target).append(empty);
      return;
    }
    function branch(root, values, scope, depth) {
      for (const row of values) {
        let next = {
          ...scope,
          label: [scope.label, row.name].filter(Boolean).join(" / "),
        };
        if (kind === "hierarchy") next.node_id = row.node_id;
        if (row.platform !== undefined) next.platform = row.platform;
        if (row.shop_id !== undefined) next.shop_id = row.shop_id;
        if (kind === "products" && row.key)
          next.key_json = JSON.stringify(row.key);
        if (kind === "teams")
          next[depth === 0 ? "group_key_json" : "employee_key_json"] =
            JSON.stringify([row.identity_type, row.id]);
        const container = document.createElement(
          row.children.length ? "details" : "div",
        );
        if (!row.children.length) container.className = "performance-leaf";
        const header = row.children.length
          ? document.createElement("summary")
          : container;
        for (const text of [
          row.name,
          number(row.quantity) + " 件",
          row.order_count + " 单",
          row.share + "%",
        ]) {
          const span = document.createElement("span");
          span.textContent = text;
          header.append(span);
        }
        const button = document.createElement("button");
        button.className = "button";
        button.textContent = "明细";
      button.disabled = Boolean(window.__STATIC_PREVIEW__ && !window.__DATA_SNAPSHOT__?.analysis);
        button.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          businessRun(async () => {
            if (typeof setHomeTab === 'function') setHomeTab('orders');
            await loadReportDetails(next);
            $("report-details").scrollIntoView({ behavior: "smooth" });
          });
        });
        header.append(button);
        if (row.children.length) {
          container.append(header);
          branch(container, row.children, next, depth + 1);
        }
        root.append(container);
      }
    }
    branch($(target), rows, {}, 0);
  }
  for (const kind of ["platforms", "products", "teams", "hierarchy"])
    render("performance-" + kind, data[kind] || [], kind);
}

function mountBusiness() {
  const section = document.createElement("section");
  section.id = "catalog";
  section.className = "view";
  section.hidden = true;
  section.innerHTML = `<div class="heading"><div><div class="eyebrow">COMPANY PRODUCTS</div><h1>产品中心</h1></div><button id="company-refresh" class="button">刷新产品</button></div>
  <article class="panel instructions"><h2>公司产品层级</h2><div id="company-tree" class="product-tree"></div></article>
  <article class="panel instructions" data-admin><h2>维护父体与具体规格</h2><form id="company-form" class="business-form"><input id="company-id" type="hidden"><label>公司编号<input id="company-code" maxlength="200" required></label><label>产品名称<input id="company-name" maxlength="200" required></label><label>上级产品<select id="company-parent"></select></label><label>类型<select id="company-kind"><option value="group">汇总节点 / 父体</option><option value="sku">具体规格 SKU</option></select></label><label>颜色<input id="company-color" maxlength="100"></label><label>容量 / 配置<input id="company-config" maxlength="100"></label><label>操作人<input id="company-actor" required maxlength="100"></label><label>变更原因<input id="company-reason" maxlength="500"></label><label class="check-label"><input id="company-active" type="checkbox" checked>启用</label><div class="actions business-wide"><button type="submit" class="button primary">保存产品</button><button id="company-reset" type="button" class="button">新增 / 重置</button><button id="company-history" type="button" class="button">变更历史</button><span id="company-version" class="muted">新产品</span></div></form><div id="company-history-table" class="table-wrap"></div></article>
  <article class="panel instructions" data-admin><h2>来源编码关联</h2><form id="link-form" class="business-form"><label>来源类型<select id="link-domain"><option value="catalog">Base 来源产品</option><option value="sales">平台 / 店铺原 SKU</option><option value="stock">仓库来源产品</option></select></label><label>目录 ID<input id="link-catalog" type="number" min="1"></label><label>来源产品 ID<input id="link-product" type="number" min="1"></label><label>仓库标识<input id="link-warehouse" maxlength="100"></label><label>平台<select id="link-platform"></select></label><label>店铺<select id="link-shop"></select></label><label>原 SKU<input id="link-sku" maxlength="200"></label><label>公司具体规格<select id="link-node" required></select></label><label>操作人<input id="link-actor" maxlength="100" required></label><label>变更原因<input id="link-reason" maxlength="500"></label><div class="actions business-wide"><button type="submit" class="button primary">保存关联</button><button id="link-reset" type="button" class="button">新增 / 重置</button><span id="link-version" class="muted">新关联</span></div></form><div id="company-links" class="table-wrap"></div></article>`;
  $("notice").before(section);
  const inventory = document.createElement("article");
  inventory.id = "inventory-business";
  inventory.className = "panel instructions";
  inventory.innerHTML = `<div class="panel-heading"><h2>统一库存与小组份额</h2><button id="inventory-refresh" class="button">刷新数据</button></div><form id="inventory-business-form" class="business-form"><label>产品父体 / 规格<select id="inventory-node"></select></label><label>来源目录<select id="inventory-catalog"></select></label><label>库存所属小组<select id="inventory-group"></select></label><label>独占分配平台<select id="inventory-platform"></select></label><label>仓库<select id="inventory-warehouse"></select></label><label>公司 SKU / 来源编码<input id="inventory-search"></label><div class="actions business-wide"><button type="submit" class="button primary">查询库存</button><button id="inventory-reset" type="button" class="button">重置</button><button id="inventory-export" type="button" class="button">导出明细</button></div></form><div class="business-summary" id="inventory-business-metrics"></div><h3>公司规格与仓库分布</h3><div id="inventory-product-table" class="table-wrap"></div><h3>来源编码与份额</h3><div id="allocation-results" class="table-wrap"></div><div class="business-pager"><button id="inventory-prev" class="button">上一页</button><span id="inventory-page"></span><button id="inventory-next" class="button">下一页</button></div>`;
  const stock = $("stock-dashboard");
  const source = document.createElement("details");
  source.className = "stock-source-section";
  const title = document.createElement("summary");
  title.textContent = "来源库存核对：汇总 / 矩阵 / 仓库明细";
  source.append(title);
  while (stock.firstChild) source.append(stock.firstChild);
  stock.append(inventory, source);
  const dialog = document.createElement("dialog");
  dialog.id = "allocation-dialog";
  dialog.className = "allocation-dialog";
  dialog.innerHTML = `<form id="allocation-form"><h2>库存归属与份额</h2><p id="allocation-scope"></p><div class="business-form"><label>归属方式<select id="allocation-mode"><option value="unassigned">未分配</option><option value="encoded">来源编码整体归一个小组</option><option value="split">同一来源编码按数量拆分</option></select></label><label>库存类型<select id="allocation-kind"><option value="unknown">待确认</option><option value="physical">实物在库</option><option value="virtual">虚拟库存</option><option value="return_pending">退货待检</option><option value="scrap">报废</option><option value="in_transit">运输在途</option></select></label><label>操作人<input id="allocation-actor" required maxlength="100"></label><label>变更原因<input id="allocation-reason" maxlength="500"></label></div><div id="allocation-shares"></div><button id="allocation-add-share" type="button" class="button">添加小组份额</button><p class="business-muted">编码归组数量随来源变化；拆分份额保持原值。渠道共享池只计一次。</p><div class="actions"><button type="submit" class="button primary">保存分配</button><button id="allocation-close" type="button" class="button">关闭</button><button id="allocation-history" type="button" class="button">变更历史</button></div><div id="allocation-history-table" class="table-wrap"></div></form>`;
  $("notice").before(dialog);
  const users = document.createElement("article");
  users.className = "panel instructions";
  users.dataset.admin = "";
  users.innerHTML = `<h2>账号与数据权限</h2><form id="user-form" class="business-form"><input id="user-id" type="hidden"><label>账号<input id="user-name" required maxlength="100"></label><label>姓名<input id="user-label" required maxlength="100"></label><label>密码（新增或重设）<input id="user-password" type="password" autocomplete="new-password" minlength="12" maxlength="200" required></label><label>角色<select id="user-role"><option value="owner">老板 / 管理层</option><option value="manager">小组负责人</option><option value="operator">运营 / 销售人员</option><option value="admin">系统管理员</option></select></label><label>小组<select id="user-group"></select></label><label>人员<select id="user-employee"></select></label><label class="check-label"><input id="user-active" type="checkbox" checked>启用</label><div class="actions business-wide"><button type="submit" class="button primary">保存账号</button><button id="user-reset" type="button" class="button">新增 / 重置</button><button id="users-refresh" type="button" class="button">刷新账号</button></div></form><div id="users-table" class="table-wrap"></div>`;
  $("ownership").append(users);
}
mountBusiness();

async function refreshCompanyCatalog() {
  await loadBusinessOptions();
  const root = $("company-tree");
  root.replaceChildren();
  function tree(parent, host) {
    const ul = document.createElement("ul");
    for (const node of business.nodes.filter((n) => n.parent_id === parent)) {
      const li = document.createElement("li"),
        span = document.createElement("span");
      span.textContent = node.name + " · " + node.code;
      li.append(span);
      if (businessAdmin()) {
        const button = document.createElement("button");
        button.className = "button";
        button.textContent = "编辑";
        button.addEventListener("click", () => editCompany(node));
        li.append(button);
      }
      tree(node.id, li);
      ul.append(li);
    }
    if (ul.children.length) host.append(ul);
  }
  tree(null, root);
  if (!root.children.length)
    root.textContent = "暂无公司产品，请先维护父体和具体规格";
  if (businessAdmin()) await loadCompanyLinks();
}
function editCompany(row) {
  $("company-id").value = row.id;
  $("company-code").value = row.code;
  $("company-name").value = row.name;
  $("company-parent").value = row.parent_id || "";
  $("company-kind").value = row.kind;
  $("company-color").value = row.attributes?.color || "";
  $("company-config").value = row.attributes?.configuration || "";
  $("company-active").checked = Boolean(row.active);
  business.productVersion = row.version;
  $("company-version").textContent = "版本 " + row.version;
}
async function loadCompanyLinks() {
  business.links = await api("/api/catalog/links");
  table(
    "company-links",
    ["类型", "来源标识", "公司规格", "版本", "操作"],
    business.links.map((r) => [
      r.domain,
      r.source_key,
      business.nodes.find((n) => n.id === r.node_id)?.code || r.node_id,
      r.version,
      "",
    ]),
  );
  reportAction(
    "company-links",
    business.links,
    (row) => {
      const key = JSON.parse(row.source_key);
      $("link-domain").value = row.domain;
      $("link-catalog").value = "";
      $("link-product").value = "";
      $("link-warehouse").value = "";
      $("link-platform").value = "";
      $("link-shop").value = "";
      $("link-sku").value = "";
      if (row.domain === "sales") {
        $("link-platform").value = key[0];
        businessShopChoices("link-platform", "link-shop");
        $("link-shop").value = key[1];
        $("link-sku").value = key[2];
      } else {
        $("link-catalog").value = key[0];
        $("link-product").value = key[row.domain === "stock" ? 2 : 1];
        if (row.domain === "stock") $("link-warehouse").value = key[1];
      }
      $("link-node").value = row.node_id;
      business.linkVersion = row.version;
      $("link-version").textContent = "版本 " + row.version;
      business.linkKey = row.domain + "|" + row.source_key;
    },
    "编辑",
  );
}
$("company-refresh").addEventListener("click", () =>
  businessRun(refreshCompanyCatalog),
);
$("company-reset").addEventListener("click", () => {
  $("company-form").reset();
  $("company-id").value = "";
  business.productVersion = 0;
  $("company-version").textContent = "新产品";
});
$("company-form").addEventListener("submit", (event) => {
  event.preventDefault();
  businessRun(async () => {
    const old = business.nodes.find(
      (n) => n.id === Number($("company-id").value),
    );
    const body = {
      id: old?.id || null,
      code: $("company-code").value,
      name: $("company-name").value,
      parent_id: Number($("company-parent").value) || null,
      kind: $("company-kind").value,
      attributes: {
        ...(old?.attributes || {}),
        color: $("company-color").value,
        configuration: $("company-config").value,
      },
      active: $("company-active").checked,
      expected_version: business.productVersion,
      actor: $("company-actor").value,
      reason: $("company-reason").value,
    };
    const saved = await api("/api/catalog/products", businessJson(body));
    await refreshCompanyCatalog();
    editCompany(business.nodes.find((n) => n.id === saved.id));
    notice("公司产品已保存");
  });
});
$("company-history").addEventListener("click", () =>
  businessRun(() =>
    showBusinessHistory(
      "product",
      $("company-id").value,
      "company-history-table",
    ),
  ),
);
$("link-platform").addEventListener("change", () =>
  businessShopChoices("link-platform", "link-shop"),
);
$("link-reset").addEventListener("click", () => {
  $("link-form").reset();
  business.linkVersion = 0;
  business.linkKey = null;
  $("link-version").textContent = "新关联";
});
$("link-form").addEventListener("submit", (event) => {
  event.preventDefault();
  businessRun(async () => {
    const domain = $("link-domain").value,
      body = {
        domain,
        node_id: Number($("link-node").value),
        actor: $("link-actor").value,
        reason: $("link-reason").value,
        expected_version: 0,
      };
    if (domain === "sales")
      Object.assign(body, {
        platform: $("link-platform").value,
        shop_id: $("link-shop").value,
        source_sku: $("link-sku").value,
      });
    else
      Object.assign(body, {
        inventory_id: Number($("link-catalog").value),
        product_id: Number($("link-product").value),
        warehouse_key: domain === "stock" ? $("link-warehouse").value : "",
      });
    const key =
      domain === "sales"
        ? [body.platform, body.shop_id, body.source_sku]
        : domain === "stock"
          ? [body.inventory_id, body.warehouse_key, body.product_id]
          : [body.inventory_id, body.product_id];
    const existing = business.links.find(
      (r) =>
        r.domain === domain &&
        JSON.stringify(JSON.parse(r.source_key)) === JSON.stringify(key),
    );
    if (
      existing &&
      business.linkKey !== existing.domain + "|" + existing.source_key
    )
      throw new Error("该来源已有关联，请先从列表读取");
    body.expected_version = existing ? business.linkVersion : 0;
    await api("/api/catalog/links", businessJson(body));
    await loadCompanyLinks();
    $("link-reset").click();
    notice("来源关联已保存，请重新查询报表");
  });
});

async function loadAssignmentRules() {
  await loadBusinessOptions();
  business.rules = await api("/api/performance/rules");
  table(
    "assignment-table",
    [
      "类型",
      "平台 / 店铺",
      "SKU / 公司产品",
      "生效区间",
      "小组",
      "负责人",
      "状态",
      "操作",
    ],
    business.rules.map((r) => [
      r.scope === "sku" ? "商品" : "店铺",
      (r.platform || "全部") + " / " + (r.shop_id || "全部"),
      r.source_sku ||
        business.nodes.find((n) => n.id === r.node_id)?.code ||
        "—",
      r.valid_from + " ～ " + (r.valid_to || "长期"),
      r.business_group || "未分配",
      r.sales_owner || "未分配",
      r.active ? "启用" : "停用",
      "",
    ]),
  );
  reportAction("assignment-table", business.rules, editAssignment, "编辑");
}
function editAssignment(row) {
  $("assignment-id").value = row.id;
  for (const [id, key] of [
    ["scope", "scope"],
    ["platform", "platform"],
    ["sku", "source_sku"],
    ["catalog", "inventory_id"],
    ["node", "node_id"],
    ["start", "valid_from"],
    ["end", "valid_to"],
    ["group", "group_id"],
    ["employee", "employee_id"],
  ])
    $("assignment-" + id).value = row[key] || "";
  businessShopChoices("assignment-platform", "assignment-shop");
  $("assignment-shop").value = row.shop_id || "";
  $("assignment-active").checked = Boolean(row.active);
  business.ruleVersion = row.version;
  $("assignment-version").textContent =
    "规则 " + row.id + " · 版本 " + row.version;
}
$("performance-refresh").addEventListener("click", () =>
  businessRun(loadAssignmentRules),
);
$("assignment-platform").addEventListener("change", () =>
  businessShopChoices("assignment-platform", "assignment-shop"),
);
$("assignment-employee").addEventListener("change", () => {
  const emp = business.employees.find(
    (e) => e.id === Number($("assignment-employee").value),
  );
  if (emp) $("assignment-group").value = emp.group_id || "";
});
$("assignment-reset").addEventListener("click", () => {
  $("assignment-form").reset();
  $("assignment-id").value = "";
  business.ruleVersion = 0;
  $("assignment-version").textContent = "新规则";
});
$("assignment-form").addEventListener("submit", (event) => {
  event.preventDefault();
  businessRun(async () => {
    const scope = $("assignment-scope").value;
    const body = {
      id: Number($("assignment-id").value) || null,
      scope,
      platform: $("assignment-platform").value,
      shop_id: $("assignment-shop").value,
      source_sku: scope === "sku" ? $("assignment-sku").value : "",
      inventory_id:
        scope === "sku" ? Number($("assignment-catalog").value) || 0 : 0,
      node_id:
        scope === "sku" ? Number($("assignment-node").value) || null : null,
      valid_from: $("assignment-start").value,
      valid_to: $("assignment-end").value || null,
      employee_id: Number($("assignment-employee").value) || null,
      group_id: Number($("assignment-group").value) || null,
      active: $("assignment-active").checked,
      expected_version: business.ruleVersion,
      actor: businessActor(),
      reason: businessReason(),
    };
    const saved = await api("/api/performance/rules", businessJson(body));
    await loadAssignmentRules();
    editAssignment(saved);
    business.preview = null;
    $("assignment-apply").disabled = true;
    notice("规则已保存；已有订单归属保持原值");
  });
});
async function showBusinessHistory(kind, key, target) {
  if (!key) throw new Error("请先读取对象");
  const data = await api(
    "/api/performance/history?" + new URLSearchParams({ kind, key }),
  );
  table(
    target,
    ["时间", "操作人", "原因", "变更前", "变更后"],
    data.history.map((r) => [
      new Date(r.created_at).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
      }),
      r.actor,
      r.reason,
      JSON.stringify(r.before),
      JSON.stringify(r.after),
    ]),
  );
}
$("assignment-history").addEventListener("click", () =>
  businessRun(() =>
    showBusinessHistory(
      "sales_rule",
      $("assignment-id").value,
      "assignment-history-table",
    ),
  ),
);
$("assignment-preview").addEventListener("click", () =>
  businessRun(async () => {
    if (!reportState.data) throw new Error("请先查询销量");
    const body = {
      filters: reportState.data.filters,
      actor: businessActor(),
      reason: businessReason(),
    };
    const data = await api("/api/performance/materialize", {
      ...businessJson(body),
      method: "POST",
    });
    business.preview = { body, token: data.token };
    $("assignment-preview-summary").textContent =
      data.line_count + " 条明细 / " + data.total_units + " 件";
    table(
      "assignment-preview-table",
      ["订单", "SKU", "数量", "小组", "负责人"],
      data.lines.map((r) => [
        r.order_id,
        r.source_sku,
        r.quantity,
        r.business_group,
        r.sales_owner,
      ]),
    );
    $("assignment-apply").disabled = !data.line_count;
  }),
);
$("assignment-apply").addEventListener("click", () =>
  businessRun(async () => {
    if (!business.preview) throw new Error("请先预览");
    await api("/api/performance/materialize", {
      ...businessJson({
        ...business.preview.body,
        expected_token: business.preview.token,
      }),
      method: "POST",
    });
    business.preview = null;
    $("assignment-apply").disabled = true;
    await refreshSalesReport();
    notice("已保存明细归属");
  }),
);

async function refreshInventoryBusiness() {
  if (!business.loaded) await loadBusinessOptions();
  const params = new URLSearchParams();
  for (const [id, key] of [
    ["inventory-node", "node_id"],
    ["inventory-catalog", "inventory_id"],
    ["inventory-group", "group_id"],
    ["inventory-platform", "platform"],
    ["inventory-warehouse", "warehouse_key"],
    ["inventory-search", "search"],
  ])
    if ($(id).value) params.set(key, $(id).value);
  const request = (business.inventoryRequest || 0) + 1;
  business.inventoryRequest = request;
  const data = window.__DATA_SNAPSHOT__
    ? filterInventorySnapshot(await api("/api/allocations/view"), params)
    : await api("/api/allocations/view?" + params);
  if (request !== business.inventoryRequest) return;
  business.inventory = data;
  business.inventoryOffset = 0;
  renderInventoryBusiness();
}
function filterInventorySnapshot(data, params) {
  const node = Number(params.get("node_id")),
    catalog = Number(params.get("inventory_id"));
  const group = params.get("group_id"),
    platform = params.get("platform");
  const warehouse = params.get("warehouse_key"),
    search = (params.get("search") || "").toLowerCase();
  const rows = [];
  for (const row of data.rows) {
    if (
      (node && !row.company_path.includes(node)) ||
      (catalog && row.inventory_id !== catalog) ||
      (warehouse && row.warehouse_key !== warehouse) ||
      (search &&
        ![row.sku, row.parent_sku, row.name, row.company_sku, row.company_name]
          .join(" ")
          .toLowerCase()
          .includes(search))
    )
      continue;
    const shares = row.shares.filter(
      (s) =>
        !group ||
        (group === "unassigned"
          ? s.group_id === null
          : s.group_id === Number(group)),
    );
    if (group && !shares.length) continue;
    row.shares = shares;
    if (platform) {
      row.stock = shares.reduce(
        (sum, s) =>
          sum +
          s.channels
            .filter((c) => c.kind === "dedicated" && c.platform === platform)
            .reduce((a, c) => a + c.quantity, 0),
        0,
      );
      if (!row.stock) continue;
    } else if (group)
      row.stock = shares.some((s) => s.quantity === null)
        ? null
        : shares.reduce((sum, s) => sum + Number(s.quantity), 0);
    rows.push(row);
  }
  const products = new Map();
  for (const row of rows) {
    const key = row.company_node_id
      ? "company:" + row.company_node_id
      : "source:" + row.inventory_id + ":" + row.product_id;
    if (!products.has(key))
      products.set(key, {
        key,
        company_node_id: row.company_node_id,
        sku: row.company_sku || row.sku,
        name: row.company_name || row.name || row.sku,
        physical_total: 0,
        unknown_physical_rows: 0,
        warehouses: {},
      });
    const target = products.get(key);
    target.warehouses[row.warehouse_key] ||= {
      name: row.warehouse_name,
      physical: 0,
      unknown_rows: 0,
    };
    const w = target.warehouses[row.warehouse_key],
      q = stockNumber(row.stock);
    if (row.stock_kind === "physical" && q !== null) {
      target.physical_total += q;
      w.physical += q;
    } else if (["physical", "unknown"].includes(row.stock_kind)) {
      target.unknown_physical_rows++;
      w.unknown_rows++;
    }
  }
  return {
    ...data,
    rows,
    row_count: rows.length,
    products: [...products.values()],
  };
}
function renderInventoryBusiness() {
  const data = business.inventory;
  if (!data) return;
  const conflict = data.rows.filter(
    (r) => r.allocation_conflict || r.channel_conflict,
  ).length;
  $("inventory-business-metrics").replaceChildren();
  for (const [label, value] of [
    ["公司规格", data.products.length],
    ["来源明细", data.row_count],
    ["分配差异", conflict],
  ]) {
    const span = document.createElement("span");
    span.textContent = label + " ";
    const strong = document.createElement("strong");
    strong.textContent = number(value);
    span.append(strong);
    $("inventory-business-metrics").append(span);
  }
  table(
    "inventory-product-table",
    [
      "公司 SKU / 来源 SKU",
      "产品名称",
      "已确认实物合计",
      "仓库分布",
      "待确认明细",
    ],
    data.products.map((p) => [
      p.sku,
      p.name,
      number(p.physical_total),
      Object.values(p.warehouses)
        .map(
          (w) =>
            w.name +
            "：" +
            number(w.physical) +
            (w.unknown_rows ? "（含待确认）" : ""),
        )
        .join("；"),
      p.unknown_physical_rows,
    ]),
  );
  const page = data.rows.slice(
    business.inventoryOffset,
    business.inventoryOffset + 50,
  );
  table(
    "allocation-results",
    [
      "目录 / 来源产品",
      "来源 SKU",
      "公司 SKU",
      "仓库",
      "类型",
      "当前范围数量",
      "小组份额 / 渠道",
      "差异",
      "操作",
    ],
    page.map((r) => [
      r.inventory_id + " / " + r.product_id,
      r.sku || "—",
      r.company_sku || "未关联",
      r.warehouse_name,
      {
        physical: "实物在库",
        unknown: "待确认",
        virtual: "虚拟库存",
        return_pending: "退货待检",
        scrap: "报废",
        in_transit: "运输在途",
      }[r.stock_kind] || r.stock_kind,
      stockTextNumber(r.stock),
      r.shares
        .map(
          (s) =>
            (s.business_group || "未分配") +
            " " +
            stockTextNumber(s.quantity) +
            "台" +
            (s.channels.length
              ? " [" +
                s.channels
                  .map(
                    (c) =>
                      (c.kind === "shared"
                        ? "共享 " + c.shared_platforms.join("/")
                        : c.platform + (c.shop_id ? "/" + c.shop_id : "")) +
                      " " +
                      c.quantity,
                  )
                  .join("；") +
                "]"
              : ""),
        )
        .join("；"),
      r.allocation_conflict || r.channel_conflict ? "需核对" : "—",
      "",
    ]),
  );
  reportAction(
    "allocation-results",
    page,
    (row) => openAllocation(row),
    "维护份额",
  );
  if (!businessAdmin() || window.__STATIC_PREVIEW__)
    $("allocation-results")
      .querySelectorAll("button")
      .forEach((b) => (b.hidden = true));
  $("inventory-page").textContent =
    (data.row_count ? business.inventoryOffset + 1 : 0) +
    "–" +
    Math.min(data.row_count, business.inventoryOffset + 50) +
    " / " +
    data.row_count;
  $("inventory-prev").disabled = !business.inventoryOffset;
  $("inventory-next").disabled =
    business.inventoryOffset + 50 >= data.row_count;
}
$("inventory-business-form").addEventListener("submit", (event) => {
  event.preventDefault();
  businessRun(refreshInventoryBusiness);
});
$("inventory-refresh").addEventListener("click", () =>
  businessRun(async () => {
    await loadBusinessOptions();
    await refreshInventoryBusiness();
  }),
);
$("inventory-reset").addEventListener("click", () => {
  $("inventory-business-form").reset();
  businessRun(refreshInventoryBusiness);
});
$("inventory-prev").addEventListener("click", () => {
  business.inventoryOffset = Math.max(0, business.inventoryOffset - 50);
  renderInventoryBusiness();
});
$("inventory-next").addEventListener("click", () => {
  business.inventoryOffset += 50;
  renderInventoryBusiness();
});
function businessCsv(headers, rows, name) {
  const safe = (v) => {
    let s = String(v ?? "");
    if (/^[=+@\-\t\r]/.test(s)) s = "'" + s;
    return '"' + s.replaceAll('"', '""') + '"';
  };
  const csv =
    "\ufeff" +
    [headers, ...rows].map((row) => row.map(safe).join(",")).join("\r\n");
  const url = URL.createObjectURL(
    new Blob([csv], { type: "text/csv;charset=utf-8" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("inventory-export").addEventListener("click", () => {
  if (!business.inventory) return;
  businessCsv(
    [
      "目录",
      "来源产品",
      "来源 SKU",
      "公司 SKU",
      "仓库",
      "类型",
      "当前范围数量",
      "库存小组份额",
    ],
    business.inventory.rows.map((r) => [
      r.inventory_id,
      r.product_id,
      r.sku,
      r.company_sku,
      r.warehouse_name,
      r.stock_kind,
      r.stock,
      r.shares.map((s) => s.business_group + ":" + s.quantity).join(";"),
    ]),
    "统一库存与份额.csv",
  );
});
async function linkCompanyInventory(identity) {
  await loadBusinessOptions();
  $("inventory-node").value = identity;
  $("inventory-catalog").value = "";
  $("inventory-group").value = "";
  $("inventory-platform").value = "";
  $("inventory-warehouse").value = "";
  $("inventory-search").value = "";
  switchTab("stock-dashboard");
  await refreshInventoryBusiness();
}
function simpleSelect(rows, empty, value, label) {
  const select = document.createElement("select");
  select.append(new Option(empty, ""));
  rows.forEach((r) => select.append(new Option(label(r), value(r))));
  return select;
}
function addShare(share = {}) {
  const box = document.createElement("div");
  box.className = "share-entry";
  const head = document.createElement("div");
  head.className = "share-head";
  const group = simpleSelect(
    business.groups.filter((g) => g.active),
    "选择小组",
    (g) => g.id,
    (g) => g.name,
  );
  group.className = "share-group";
  group.value = share.group_id || "";
  const qty = document.createElement("input");
  qty.type = "number";
  qty.min = 0;
  qty.step = 1;
  qty.className = "share-quantity";
  qty.value = share.quantity ?? business.editingSource?.stock ?? 0;
  qty.disabled = $("allocation-mode").value === "encoded";
  const unit = document.createElement("span");
  unit.textContent = "台";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "button";
  remove.textContent = "移除小组";
  remove.addEventListener("click", () => box.remove());
  const add = document.createElement("button");
  add.type = "button";
  add.className = "button";
  add.textContent = "添加渠道分配";
  head.append(group, qty, unit, add, remove);
  box.append(head);
  const channels = document.createElement("div");
  channels.className = "share-channels";
  box.append(channels);
  add.addEventListener("click", () => addChannel(channels));
  for (const channel of share.channels || []) addChannel(channels, channel);
  $("allocation-shares").append(box);
}
function addChannel(root, channel = {}) {
  const row = document.createElement("div");
  row.className = "channel-entry";
  const kind = simpleSelect(
    [],
    "独占渠道",
    () => "",
    () => "",
  );
  kind.options[0].value = "dedicated";
  kind.append(new Option("共享池", "shared"));
  kind.className = "channel-kind";
  kind.value = channel.kind || "dedicated";
  const platforms = [...new Set(business.shops.map((s) => s.platform))];
  const platform = simpleSelect(
    platforms,
    "选择平台",
    (p) => p,
    (p) => p,
  );
  platform.className = "channel-platform";
  platform.value = channel.platform || "";
  const shop = simpleSelect(
    [],
    "全部店铺",
    (s) => s.shop_id,
    (s) => s.name,
  );
  shop.className = "channel-shop";
  const shared = simpleSelect(
    platforms,
    "",
    (p) => p,
    (p) => p,
  );
  shared.options[0].remove();
  shared.multiple = true;
  shared.className = "channel-shared";
  [...shared.options].forEach(
    (o) => (o.selected = (channel.shared_platforms || []).includes(o.value)),
  );
  const qty = document.createElement("input");
  qty.className = "channel-quantity";
  qty.type = "number";
  qty.min = 0;
  qty.step = 1;
  qty.value = channel.quantity || 0;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "button";
  remove.textContent = "移除";
  remove.addEventListener("click", () => row.remove());
  const update = () => {
    platform.hidden = shop.hidden = kind.value === "shared";
    shared.hidden = kind.value !== "shared";
  };
  platform.addEventListener("change", () => {
    shop.replaceChildren(new Option("全部店铺", ""));
    business.shops
      .filter((s) => s.platform === platform.value)
      .forEach((s) => shop.append(new Option(s.name, s.shop_id)));
  });
  platform.dispatchEvent(new Event("change"));
  shop.value = channel.shop_id || "";
  kind.addEventListener("change", update);
  update();
  row.append(kind, platform, shop, shared, qty, remove);
  root.append(row);
}
async function openAllocation(row) {
  const data = await api(
    "/api/allocations/view?" +
      new URLSearchParams({ inventory_id: row.inventory_id }),
  );
  const full = data.rows.find(
    (r) =>
      r.product_id === row.product_id && r.warehouse_key === row.warehouse_key,
  );
  if (!full) throw new Error("来源库存已变化，请刷新");
  business.editingSource = full;
  $("allocation-scope").textContent =
    (full.company_sku || full.sku || full.product_id) +
    " / " +
    full.warehouse_name +
    " / 来源数量 " +
    stockTextNumber(full.stock);
  $("allocation-mode").value = full.mode;
  $("allocation-kind").value = full.stock_kind;
  $("allocation-actor").value = window.appUser?.name || $("change-actor").value;
  $("allocation-reason").value = "";
  $("allocation-shares").replaceChildren();
  $("allocation-history-table").replaceChildren();
  for (const share of full.shares.filter((s) => s.group_id !== null))
    addShare(share);
  $("allocation-dialog").showModal();
}
$("allocation-mode").addEventListener("change", () => {
  const mode = $("allocation-mode").value;
  if (mode === "unassigned") $("allocation-shares").replaceChildren();
  if (mode === "encoded" && $("allocation-shares").children.length !== 1) {
    $("allocation-shares").replaceChildren();
    addShare();
  }
  $("allocation-shares")
    .querySelectorAll(".share-quantity")
    .forEach((el) => (el.disabled = mode === "encoded"));
});
$("allocation-add-share").addEventListener("click", () => {
  if ($("allocation-mode").value !== "split") {
    notice("添加多个小组请选择按数量拆分", true);
    return;
  }
  addShare({ quantity: 0 });
});
$("allocation-close").addEventListener("click", () =>
  $("allocation-dialog").close(),
);
$("allocation-form").addEventListener("submit", (event) => {
  event.preventDefault();
  businessRun(async () => {
    const source = business.editingSource;
    if (!source) throw new Error("请先读取来源库存");
    const shares = [...$("allocation-shares").children].map((box) => ({
      group_id: Number(box.querySelector(".share-group").value),
      quantity: Number(box.querySelector(".share-quantity").value),
      channels: [...box.querySelectorAll(".channel-entry")].map((row) => {
        const shared = row.querySelector(".channel-kind").value === "shared";
        return {
          kind: shared ? "shared" : "dedicated",
          platform: shared
            ? null
            : row.querySelector(".channel-platform").value || null,
          shop_id: shared
            ? null
            : row.querySelector(".channel-shop").value || null,
          shared_platforms: shared
            ? [...row.querySelector(".channel-shared").selectedOptions].map(
                (o) => o.value,
              )
            : [],
          quantity: Number(row.querySelector(".channel-quantity").value),
        };
      }),
    }));
    await api(
      "/api/allocations/source",
      businessJson({
        inventory_id: source.inventory_id,
        product_id: source.product_id,
        warehouse_key: source.warehouse_key,
        mode: $("allocation-mode").value,
        stock_kind: $("allocation-kind").value,
        shares,
        expected_version: source.allocation_version,
        expected_source_token: source.source_token,
        actor: $("allocation-actor").value,
        reason: $("allocation-reason").value,
      }),
    );
    $("allocation-dialog").close();
    await refreshInventoryBusiness();
    notice("库存份额已保存");
  });
});
$("allocation-history").addEventListener("click", () =>
  businessRun(() => {
    const r = business.editingSource;
    return showBusinessHistory(
      "stock_allocation",
      JSON.stringify([r.inventory_id, r.product_id, r.warehouse_key]),
      "allocation-history-table",
    );
  }),
);

async function refreshUsers() {
  const rows = await api("/api/auth/users");
  table(
    "users-table",
    ["账号", "姓名", "角色", "小组", "人员", "状态", "操作"],
    rows.map((r) => [
      r.username,
      r.name,
      {
        admin: "系统管理员",
        owner: "老板 / 管理层",
        manager: "小组负责人",
        operator: "运营 / 销售人员",
      }[r.role],
      business.groups.find((g) => g.id === r.group_id)?.name || "—",
      business.employees.find((e) => e.id === r.employee_id)?.name || "—",
      r.active ? "启用" : "停用",
      "",
    ]),
  );
  reportAction(
    "users-table",
    rows,
    (row) => {
      $("user-id").value = row.id;
      $("user-name").value = row.username;
      $("user-label").value = row.name;
      $("user-role").value = row.role;
      $("user-group").value = row.group_id || "";
      $("user-employee").value = row.employee_id || "";
      $("user-active").checked = Boolean(row.active);
      $("user-password").value = "";
    },
    "编辑 / 重设密码",
  );
}
$("user-employee").addEventListener("change", () => {
  const e = business.employees.find(
    (e) => e.id === Number($("user-employee").value),
  );
  if (e) $("user-group").value = e.group_id || "";
});
$("users-refresh").addEventListener("click", () => businessRun(refreshUsers));
$("user-reset").addEventListener("click", () => {
  $("user-form").reset();
  $("user-id").value = "";
});
$("user-form").addEventListener("submit", (event) => {
  event.preventDefault();
  businessRun(async () => {
    const body = {
      id: Number($("user-id").value) || null,
      username: $("user-name").value,
      name: $("user-label").value,
      password: $("user-password").value,
      role: $("user-role").value,
      group_id: Number($("user-group").value) || null,
      employee_id: Number($("user-employee").value) || null,
      active: $("user-active").checked,
    };
    await api("/api/auth/users", businessJson(body));
    $("user-reset").click();
    await refreshUsers();
    notice("账号已保存；密码和权限修改后原会话失效");
  });
});
if (window.__STATIC_PREVIEW__) {
  document
    .querySelectorAll(
      "#catalog form,#assignment-form,#allocation-form,#user-form",
    )
    .forEach((f) =>
      f
        .querySelectorAll("input,select,button")
        .forEach((el) => (el.disabled = true)),
    );
}
loadBusinessOptions()
  .then(() => {
    if (businessAdmin() && !window.__STATIC_PREVIEW__)
      return Promise.all([loadAssignmentRules(), refreshUsers()]);
  })
  .catch((error) => notice(error.message, true));

let editingLineOwner = null;
const lineDialog = document.createElement("dialog");
lineDialog.id = "line-owner-dialog";
lineDialog.className = "allocation-dialog";
lineDialog.innerHTML = `<form id="line-owner-form"><h2>修正商品明细归属</h2><p id="line-owner-scope"></p><div class="business-form"><label>负责人<select id="line-owner-employee"></select></label><label>小组<select id="line-owner-group"></select></label><label>操作人<input id="line-owner-actor" required maxlength="100"></label><label>原因<input id="line-owner-reason" maxlength="500"></label></div><div class="actions"><button type="submit" class="button primary">保存明细归属</button><button id="line-owner-close" type="button" class="button">关闭</button><button id="line-owner-history" type="button" class="button">变更历史</button></div><div id="line-owner-history-table" class="table-wrap"></div></form>`;
$("notice").before(lineDialog);
function addLineOwnerActions(rows) {
  if (!businessAdmin() || window.__STATIC_PREVIEW__) return;
  $("report-details")
    .querySelectorAll(":scope > table > tbody > tr")
    .forEach((tr, index) => {
      const cell = document.createElement("td"),
        button = document.createElement("button");
      button.className = "button";
      button.textContent = "修正归属";
      button.addEventListener("click", () =>
        businessRun(async () => {
          const row = rows[index];
          editingLineOwner = await api(
            "/api/performance/lines/" + row.order_id + "/" + row.position,
          );
          businessSelect(
            "line-owner-employee",
            business.employees,
            "未分配",
            (e) => e.id,
            (e) => e.name,
          );
          businessSelect(
            "line-owner-group",
            business.groups,
            "未分配",
            (g) => g.id,
            (g) => g.name,
          );
          $("line-owner-employee").value =
            editingLineOwner.employee_id || row.employee_id || "";
          $("line-owner-group").value =
            editingLineOwner.group_id || row.group_id || "";
          $("line-owner-scope").textContent =
            row.order_id + " / " + row.source_sku;
          $("line-owner-actor").value =
            window.appUser?.name || $("change-actor").value;
          $("line-owner-reason").value = "";
          $("line-owner-history-table").replaceChildren();
          lineDialog.showModal();
        }),
      );
      cell.append(button);
      tr.append(cell);
    });
  const head = $("report-details").querySelector("thead tr");
  if (head) {
    const th = document.createElement("th");
    th.textContent = "归属维护";
    head.append(th);
  }
}
$("line-owner-employee").addEventListener("change", () => {
  const e = business.employees.find(
    (e) => e.id === Number($("line-owner-employee").value),
  );
  if (e) $("line-owner-group").value = e.group_id || "";
});
$("line-owner-close").addEventListener("click", () => lineDialog.close());
$("line-owner-history").addEventListener("click", () =>
  businessRun(() =>
    showBusinessHistory(
      "line_owner",
      JSON.stringify([editingLineOwner.order_id, editingLineOwner.position]),
      "line-owner-history-table",
    ),
  ),
);
$("line-owner-form").addEventListener("submit", (event) => {
  event.preventDefault();
  businessRun(async () => {
    const r = editingLineOwner;
    await api(
      "/api/performance/lines/" + r.order_id + "/" + r.position,
      businessJson({
        employee_id: Number($("line-owner-employee").value) || null,
        group_id: Number($("line-owner-group").value) || null,
        expected_version: r.version,
        expected_fingerprint: r.fingerprint,
        actor: $("line-owner-actor").value,
        reason: $("line-owner-reason").value,
      }),
    );
    lineDialog.close();
    await refreshSalesReport();
    notice("商品明细归属已修正");
  });
});
