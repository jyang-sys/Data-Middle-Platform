// Recompute the read-only demo from captured, already authorized order lines.
// This adapter never changes source data or calls Base.
function snapshotReportSelection(params) {
  const snapshot = window.__DATA_SNAPSHOT__,
    analysis = snapshot?.analysis;
  if (!analysis) return null;
  const filters = Object.fromEntries(params);
  filters.status_ids = params.getAll("status_ids").map(Number);
  filters.confirmed_only = params.get("confirmed_only") !== "false";
  for (const key of ["group_id", "employee_id", "node_id"])
    filters[key] = params.get(key) ? Number(params.get(key)) : null;
  filters.granularity ||= "company_product";
  filters.timezone ||= analysis.filters.timezone;
  filters.date_basis ||= analysis.filters.date_basis;
  filters.ownership_basis ||= "saved";
  const start = Date.parse(filters.start + "T00:00:00Z"),
    end = Date.parse(filters.end + "T00:00:00Z");
  if (
    !Number.isFinite(start) ||
    !Number.isFinite(end) ||
    end < start ||
    end - start > 365 * 86400000
  )
    throw new Error("请选择有效日期，范围最多为 366 天");
  if (
    filters.start < analysis.filters.start ||
    filters.end > analysis.filters.end
  )
    throw new Error(
      "演示数据范围：" + analysis.filters.start + " 至 " + analysis.filters.end,
    );
  if (
    filters.timezone !== analysis.filters.timezone ||
    filters.date_basis !== analysis.filters.date_basis ||
    filters.ownership_basis !== "saved"
  )
    throw new Error(
      "演示数据使用已保存归属和当前日期口径；切换口径需要连接中台服务",
    );
  if (
    !["company_product", "source_product", "model", "specification"].includes(
      filters.granularity,
    )
  )
    throw new Error("汇总层级无效");
  let lines = analysis.lines.filter(
    (row) =>
      row.date >= filters.start &&
      row.date <= filters.end &&
      (!filters.confirmed_only || row.confirmed) &&
      (!filters.status_ids.length ||
        filters.status_ids.includes(row.status_id)) &&
      (!filters.platform || row.platform === filters.platform) &&
      (!filters.shop_id || row.shop_id === filters.shop_id) &&
      (!filters.group_id || row.group_id === filters.group_id) &&
      (!filters.employee_id || row.employee_id === filters.employee_id) &&
      (!filters.node_id || row.company_path.includes(filters.node_id)) &&
      (!filters.source_sku || row.source_sku === filters.source_sku) &&
      (!filters.model || row.model === filters.model) &&
      (!filters.product_type || row.product_type === filters.product_type),
  );
  lines = lines.map((source) => {
    const row = { ...source };
    row.month = row.date.slice(0, 7);
    row.mapped = Boolean(
      row.business_mapped ||
        (filters.granularity === "company_product" && row.company_node_id),
    );
    row.key =
      filters.granularity === "model"
        ? [row.model]
        : [row.model, row.specification, row.color];
    row.label =
      filters.granularity === "model"
        ? row.model
        : [row.model, row.specification, row.color].filter(Boolean).join(" · ");
    if (["company_product", "source_product"].includes(filters.granularity)) {
      row.key =
        row.inventory_id && row.product_id
          ? ["source", String(row.inventory_id), String(row.product_id)]
          : [
              "unlinked",
              row.platform,
              row.source_sku || row.order_id + ":" + row.position,
            ];
      row.label = row.source_name + " · " + (row.source_sku || "无 SKU");
      if (filters.granularity === "company_product" && row.company_node_id) {
        row.key = ["company", String(row.company_node_id), ""];
        row.label = row.company_path_labels.at(-1) + " · " + row.company_sku;
      }
    }
    return row;
  });
  return { filters, lines };
}

function snapshotComparisons(lines) {
  function add(map, key, row, name, extra = {}) {
    if (!map.has(key))
      map.set(key, {
        name,
        quantity: 0,
        orders: new Set(),
        daily: {},
        children: new Map(),
        ...extra,
      });
    const value = map.get(key);
    value.quantity += row.quantity;
    value.orders.add(row.order_id);
    value.daily[row.date] = (value.daily[row.date] || 0) + row.quantity;
    return value;
  }
  const platforms = new Map(),
    products = new Map(),
    teams = new Map(),
    hierarchy = new Map();
  for (const row of lines) {
    const p = add(platforms, row.platform, row, row.platform_name, {
      platform: row.platform,
    });
    add(
      p.children,
      JSON.stringify([row.platform, row.shop_id]),
      row,
      row.shop_name,
      { platform: row.platform, shop_id: row.shop_id },
    );
    const product = add(products, JSON.stringify(row.key), row, row.label, {
      key: row.key,
      parent_sku: row.parent_sku,
    });
    const pp = add(product.children, row.platform, row, row.platform_name, {
      platform: row.platform,
    });
    add(
      pp.children,
      JSON.stringify([row.platform, row.shop_id]),
      row,
      row.shop_name,
      { platform: row.platform, shop_id: row.shop_id },
    );
    const groupId =
      row.group_id !== null
        ? ["id", row.group_id]
        : ["text", row.business_group];
    const employeeId =
      row.employee_id !== null
        ? ["id", row.employee_id]
        : ["text", row.sales_owner];
    const team = add(
      teams,
      JSON.stringify(groupId),
      row,
      row.business_group || "未分配",
      { identity_type: groupId[0], id: groupId[1] },
    );
    add(
      team.children,
      JSON.stringify(employeeId),
      row,
      row.sales_owner || "未分配",
      { identity_type: employeeId[0], id: employeeId[1] },
    );
    let branch = hierarchy;
    row.company_path.forEach((id, index) => {
      const node = add(branch, id, row, row.company_path_labels[index], {
        node_id: id,
      });
      branch = node.children;
    });
  }
  function finish(map, total) {
    return [...map.values()]
      .map((value) => {
        const { orders, children, ...rest } = value;
        return {
          ...rest,
          order_count: orders.size,
          share: total ? Math.round((value.quantity * 10000) / total) / 100 : 0,
          children: finish(children, value.quantity),
        };
      })
      .sort((a, b) => b.quantity - a.quantity || a.name.localeCompare(b.name));
  }
  const total = lines.reduce((s, r) => s + r.quantity, 0);
  return {
    platforms: finish(platforms, total),
    products: finish(products, total),
    teams: finish(teams, total),
    hierarchy: finish(hierarchy, total),
  };
}

function snapshotAggregate(selection) {
  const { lines, filters } = selection,
    comparisons = snapshotComparisons(lines),
    monthly = new Map(),
    productDaily = new Map(),
    daily = new Map(),
    unmapped = new Map();
  for (
    let d = new Date(filters.start + "T00:00:00Z");
    d <= new Date(filters.end + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + 1)
  )
    daily.set(d.toISOString().slice(0, 10), {});
  for (const row of lines) {
    const key = JSON.stringify([row.month, row.key]);
    if (!monthly.has(key))
      monthly.set(key, {
        month: row.month,
        model: row.model,
        label: row.label,
        key: row.key,
        product_type: row.product_type,
        platforms: {},
        total: 0,
      });
    const month = monthly.get(key);
    month.total += row.quantity;
    month.platforms[row.platform] =
      (month.platforms[row.platform] || 0) + row.quantity;
    const day = daily.get(row.date);
    day[row.platform] = (day[row.platform] || 0) + row.quantity;
    const pk = JSON.stringify([row.platform, row.key]);
    if (!productDaily.has(pk))
      productDaily.set(pk, {
        platform: row.platform,
        label: row.label,
        model: row.model,
        key: row.key,
        product_type: row.product_type,
        daily: {},
        total: 0,
      });
    const product = productDaily.get(pk);
    product.total += row.quantity;
    product.daily[row.date] = (product.daily[row.date] || 0) + row.quantity;
    if (!row.mapped) {
      const mk = JSON.stringify([row.platform, row.source_sku]);
      if (!unmapped.has(mk))
        unmapped.set(mk, {
          platform: row.platform,
          source_sku: row.source_sku,
          quantity: 0,
        });
      unmapped.get(mk).quantity += row.quantity;
    }
  }
  const shops = comparisons.platforms
    .flatMap((p) =>
      p.children.map((s) => ({
        id: s.shop_id,
        identity_type: s.platform,
        name: s.name,
        quantity: s.quantity,
      })),
    )
    .sort((a, b) => b.quantity - a.quantity);
  const employees = new Map();
  for (const team of comparisons.teams)
    for (const employee of team.children) {
      const key = JSON.stringify([employee.identity_type, employee.id]);
      if (!employees.has(key))
        employees.set(key, {
          id: employee.id,
          identity_type: employee.identity_type,
          name: employee.name,
          quantity: 0,
        });
      employees.get(key).quantity += employee.quantity;
    }
  return {
    trial: true,
    filters,
    unit: "件",
    total_units: lines.reduce((s, r) => s + r.quantity, 0),
    order_count: new Set(lines.map((r) => r.order_id)).size,
    line_count: lines.length,
    platforms: comparisons.platforms
      .map((p) => ({ id: p.platform, name: p.name }))
      .sort((a, b) => a.id.localeCompare(b.id)),
    comparisons,
    monthly: [...monthly.values()].sort(
      (a, b) => a.month.localeCompare(b.month) || b.total - a.total,
    ),
    daily: [...daily].map(([date, platforms]) => ({
      date,
      platforms,
      total: Object.values(platforms).reduce((a, b) => a + b, 0),
    })),
    product_daily: [...productDaily.values()].sort(
      (a, b) => a.platform.localeCompare(b.platform) || b.total - a.total,
    ),
    groups: comparisons.teams.map((t) => ({
      id: t.id,
      identity_type: t.identity_type,
      name: t.name,
      quantity: t.quantity,
    })),
    employees: [...employees.values()].sort((a, b) => b.quantity - a.quantity),
    shops,
    unmapped: [...unmapped.values()].sort((a, b) => b.quantity - a.quantity),
    diagnostics: { unmapped_lines: lines.filter((r) => !r.mapped).length },
    warnings: [],
    last_sync: window.__DATA_SNAPSHOT__.captured_at,
  };
}

function snapshotSalesApi(url) {
  if (
    !window.__DATA_SNAPSHOT__?.analysis ||
    !["/api/reports/sales", "/api/reports/details"].includes(url.pathname)
  )
    return null;
  const selection = snapshotReportSelection(url.searchParams);
  if (url.pathname.endsWith("/sales")) return snapshotAggregate(selection);
  let { lines } = selection;
  const params = url.searchParams;
  if (params.has("month"))
    lines = lines.filter((r) => r.month === params.get("month"));
  if (params.has("key_json")) {
    const key = JSON.parse(params.get("key_json"));
    lines = lines.filter((r) => JSON.stringify(r.key) === JSON.stringify(key));
  }
  if (params.has("node_id"))
    lines = lines.filter((r) =>
      r.company_path.includes(Number(params.get("node_id"))),
    );
  for (const [name, idField, textField] of [
    ["group_key_json", "group_id", "business_group"],
    ["employee_key_json", "employee_id", "sales_owner"],
  ])
    if (params.has(name)) {
      const [kind, value] = JSON.parse(params.get(name));
      lines = lines.filter((r) =>
        kind === "id"
          ? r[idField] === value
          : r[idField] === null && r[textField] === value,
      );
    }
  const offset = Math.max(0, Number(params.get("offset") || 0)),
    limit = Math.min(200, Math.max(1, Number(params.get("limit") || 50)));
  lines.sort(
    (a, b) =>
      b.date.localeCompare(a.date) ||
      b.order_id - a.order_id ||
      b.position - a.position,
  );
  return {
    total: lines.length,
    total_units: lines.reduce((s, r) => s + r.quantity, 0),
    lines: lines.slice(offset, offset + limit).map(({ key, ...row }) => row),
  };
}
