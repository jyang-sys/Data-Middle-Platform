// Read-only Base adapter for the owner-private test Site. No upstream write methods.
import { handleShopSettings, createSnapshotStore, readShopSources, syncShopSources } from './shop-settings.mjs';
const READ_METHODS = new Set([
  "getOrderSources",
  "getInventories",
  "getInventoryWarehouses",
  "getInventoryProductsList",
  "getInventoryProductsData",
  "getOrders",
]);
export async function fetchBaseState(
  token,
  transport = fetch,
  pause = (ms) => new Promise((r) => setTimeout(r, ms)),
) {
  let nextCall = 0;
  async function call(method, parameters = {}) {
    if (!READ_METHODS.has(method)) throw new Error("不支持的读取接口");
    await pause(Math.max(0, nextCall - Date.now()));
    nextCall = Date.now() + 750;
    const response = await transport(
      "https://api.baselinker.com/connector.php",
      {
        method: "POST",
        headers: {
          "X-BLToken": token,
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({
          method,
          parameters: JSON.stringify(parameters),
        }),
        signal: AbortSignal.timeout(35000),
      },
    );
    if (!response.ok) throw new Error("Base 接口连接失败");
    const data = await response.json();
    if (data.status !== "SUCCESS")
      throw new Error(
        data.error_code === "ERROR_BAD_TOKEN"
          ? "测试密钥无效，请更新服务端密钥"
          : "Base 拒绝读取，请检查权限或接口限额",
      );
    return data;
  }
  const sources = (await call("getOrderSources")).sources;
  const catalogs = (await call("getInventories")).inventories;
  const warehouses = (await call("getInventoryWarehouses")).warehouses;
  if (!sources || !Array.isArray(catalogs) || !Array.isArray(warehouses))
    throw new Error("来源目录或仓库格式不正确");
  const products = [];
  for (const catalog of catalogs) {
    const seen = new Set();
    for (let page = 1; ; page++) {
      const batch = (
        await call("getInventoryProductsList", {
          inventory_id: catalog.inventory_id,
          page,
          include_variants: true,
          filter_sort: "id ASC",
        })
      ).products;
      if (!batch || typeof batch !== "object")
        throw new Error("来源产品格式不正确");
      const ids = Object.keys(batch);
      if (!ids.length) break;
      if (ids.some((id) => seen.has(id))) throw new Error("产品分页未前进");
      ids.forEach((id) => seen.add(id));
      for (let offset = 0; offset < ids.length; offset += 100) {
        const chunk = ids.slice(offset, offset + 100),
          details = (
            await call("getInventoryProductsData", {
              inventory_id: catalog.inventory_id,
              products: chunk.map(Number),
            })
          ).products;
        for (const id of chunk) {
          if (!details || !Object.hasOwn(details, id))
            throw new Error("产品详情不完整");
          products.push({
            ...batch[id],
            ...details[id],
            inventory_id: Number(catalog.inventory_id),
            product_id: Number(id),
          });
        }
      }
    }
  }
  const orders = [];
  let cursor = 0;
  for (;;) {
    const rows = (
      await call("getOrders", {
        id_from: cursor,
        get_unconfirmed_orders: true,
        include_custom_extra_fields: true,
        include_commissions: true,
        include_connect_data: true,
        include_discounts_data: true,
      })
    ).orders;
    if (!Array.isArray(rows)) throw new Error("来源订单格式不正确");
    if (!rows.length) break;
    const ids = rows.map((row) => Number(row.order_id));
    if (
      ids.some(
        (id, index) =>
          !Number.isInteger(id) ||
          id < cursor ||
          (index > 0 && id <= ids[index - 1]),
      )
    )
      throw new Error("订单分页未前进");
    orders.push(...rows);
    cursor = ids.at(-1) + 1;
  }
  return { sources, catalogs, warehouses, products, orders };
}

const dateText = (timestamp, zone = "Asia/Shanghai") => {
  const date = new Date(timestamp);
  if (!Number.isFinite(date.valueOf())) return null;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const get = (key) => parts.find((p) => p.type === key).value;
  return get("year") + "-" + get("month") + "-" + get("day");
};
const shift = (date, days) => {
  const d = new Date(date + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};
const qty = (value) =>
  value === null ||
  value === undefined ||
  value === "" ||
  !Number.isFinite(Number(value))
    ? null
    : String(value);
const platformName = (code) =>
  ({
    amazon: "亚马逊",
    ebay: "eBay",
    tiktok: "TikTok",
    shop: "独立站",
    personal: "手工来源",
    order_return: "退货来源",
  })[code] ||
  code ||
  "未提供平台";
const identityKey = (value) => JSON.stringify(value);

export function createBaseSnapshot(state, reference, clock = new Date()) {
  const snapshot = structuredClone(reference.snapshot),
    captured = clock.toISOString(),
    end = dateText(clock),
    todayStart = shift(end, -365);
  const nodes = structuredClone(
      snapshot.responses["/api/catalog/products"] || [],
    ),
    nodeMap = new Map(nodes.map((n) => [n.id, n]));
  const links = new Map(
    (snapshot.responses["/api/catalog/links"] || []).map((l) => [
      l.domain + "|" + identityKey(JSON.parse(l.source_key)),
      l.node_id,
    ]),
  );
  const nativeMap = new Map(
    state.products.map((p) => [p.inventory_id + ":" + p.product_id, p]),
  );
  for (const node of nodes) {
    if (node.attributes?.base_product_key && node.version === 1) {
      const current = nativeMap.get(node.attributes.base_product_key);
      node.active = current ? 1 : 0;
      if (current) {
        node.name =
          current.text_fields?.name || current.name || current.sku || node.name;
        node.attributes.source_sku = current.sku || "";
      }
    }
  }
  const sourceMap = new Map(
    (snapshot.responses["/api/catalog/links"] || [])
      .filter((l) => l.domain === "catalog")
      .map((l) => [JSON.parse(l.source_key).join(":"), l.node_id]),
  );
  let nextId = Math.max(0, ...nodes.map((n) => n.id)) + 1;
  // New source products remain separate source references; no name/SKU guessing across catalogs.
  for (const p of state.products)
    if (
      !sourceMap.has(p.inventory_id + ":" + p.product_id) &&
      !state.products.some(
        (c) =>
          c.inventory_id === p.inventory_id &&
          Number(c.parent_id) === p.product_id,
      )
    ) {
      const id = nextId++,
        node = {
          id,
          code: "BASE-" + p.inventory_id + "-" + p.product_id,
          name: p.text_fields?.name || p.name || p.sku || String(p.product_id),
          parent_id: null,
          kind: "sku",
          attributes: {
            base_product_key: p.inventory_id + ":" + p.product_id,
            source_sku: p.sku || "",
          },
          active: 1,
          version: 1,
          updated_at: captured,
        };
      nodes.push(node);
      nodeMap.set(id, node);
      sourceMap.set(p.inventory_id + ":" + p.product_id, id);
      links.set("catalog|" + identityKey([p.inventory_id, p.product_id]), id);
    }
  const ancestry = (id) => {
    const path = [],
      seen = new Set();
    while (nodeMap.has(id) && !seen.has(id)) {
      seen.add(id);
      path.unshift(id);
      id = nodeMap.get(id).parent_id;
    }
    return path;
  };
  const shops = [];
  for (const [platform, entries] of Object.entries(state.sources))
    for (const [shopId, name] of Object.entries(entries || {}))
      shops.push({ platform, shop_id: String(shopId), name: String(name) });
  const shopNames = new Map(
    shops.map((s) => [s.platform + "|" + s.shop_id, s.name]),
  );
  const lines = [],
    dates = [],
    confirmedDates = [],
    statuses = new Set();
  for (const order of state.orders) {
    const platform = String(order.order_source || ""),
      shopId = String(order.order_source_id ?? ""),
      day = dateText(Number(order.date_add) * 1000);
    if (day) {
      dates.push(day);
      if (order.confirmed) confirmedDates.push(day);
    }
    if (order.order_status_id !== null && order.order_status_id !== undefined)
      statuses.add(Number(order.order_status_id));
    const annotation = reference.annotations?.[order.order_id] || {};
    if (
      !day ||
      day < todayStart ||
      day > end ||
      platform === "order_return" ||
      annotation.exclude_from_sales === true
    )
      continue;
    for (const [position, item] of (Array.isArray(order.products)
      ? order.products
      : []
    ).entries()) {
      const quantity = Number(item.quantity);
      if (
        item.quantity === null ||
        item.quantity === undefined ||
        typeof item.quantity === "boolean" ||
        !Number.isFinite(quantity) ||
        quantity < 0 ||
        !Number.isInteger(quantity)
      )
        continue;
      const storage = String(item.storage || ""),
        storageId = String(item.storage_id || ""),
        nativeId = String(
          item.variant_id && String(item.variant_id) !== "0"
            ? item.variant_id
            : item.product_id || "",
        );
      let native =
        storage === "db" ? nativeMap.get(storageId + ":" + nativeId) : null;
      if (
        native &&
        nativeId !== String(item.product_id) &&
        String(native.parent_id) !== String(item.product_id)
      )
        native = null;
      const sku = String(item.sku || ""),
        nodeId =
          links.get("sales|" + identityKey([platform, shopId, sku])) ||
          links.get("sales|" + identityKey([platform, "", sku])) ||
          (native
            ? sourceMap.get(native.inventory_id + ":" + native.product_id)
            : null) ||
          null;
      const path = ancestry(nodeId),
        node = nodeMap.get(nodeId),
        sourceName = String(
          native?.text_fields?.name ||
            native?.name ||
            item.name ||
            sku ||
            "未提供名称",
        );
      const parent =
        native && native.parent_id
          ? nativeMap.get(storageId + ":" + native.parent_id)
          : native;
      const owner = reference.line_owners?.[order.order_id + ":" + position];
      const currentIdentity = [
        item.order_product_id,
        item.storage,
        item.storage_id,
        item.product_id,
        item.variant_id,
        item.sku,
      ].map((v) => String(v || ""));
      const validOwner =
        owner && identityKey(owner.identity) === identityKey(currentIdentity)
          ? owner
          : null;
      const manual =
        annotation.employee_id ||
        annotation.group_id ||
        annotation.sales_owner ||
        annotation.business_group ||
        annotation.ownership_locked;
      const ownership = validOwner?.manual
        ? validOwner
        : manual
          ? annotation
          : validOwner || {};
      lines.push({
        order_id: Number(order.order_id),
        external_order_id: String(order.external_order_id || ""),
        position,
        date: day,
        month: day.slice(0, 7),
        platform,
        platform_name: platformName(platform),
        shop_id: shopId,
        shop_name: shopNames.get(platform + "|" + shopId) || shopId,
        employee_id: ownership.employee_id ?? null,
        group_id: ownership.group_id ?? null,
        sales_owner: ownership.sales_owner ?? null,
        business_group: ownership.business_group ?? null,
        ownership_source: validOwner?.manual
          ? "line_manual"
          : manual
            ? "order"
            : validOwner
              ? "snapshot"
              : "unassigned",
        rule_id: ownership.rule_id ?? null,
        rule_version: ownership.rule_version ?? null,
        confirmed: Boolean(order.confirmed),
        status_id: order.order_status_id ?? null,
        company_node_id: nodeId,
        company_path: path,
        company_path_labels: path.map((id) => nodeMap.get(id).name),
        company_sku: node?.code || null,
        source_sku: sku,
        source_name: sourceName,
        parent_sku: parent?.sku || null,
        inventory_id: native?.inventory_id ?? null,
        product_id: native?.product_id ?? null,
        model: "未映射 SKU: " + (sku || "未提供 SKU") + " [" + platform + "]",
        specification: "",
        color: "",
        product_type: "未分类",
        label: node ? node.name + " · " + node.code : sourceName + " · " + sku,
        quantity,
        mapped: Boolean(node),
        business_mapped: false,
      });
    }
  }
  const warehouseMap = new Map(
    state.warehouses.map((w) => [w.warehouse_type + "_" + w.warehouse_id, w]),
  );
  const oldAllocations = new Map(
    (reference.snapshot.responses["/api/allocations/view"]?.rows || []).map(
      (r) => [r.inventory_id + ":" + r.product_id + ":" + r.warehouse_key, r],
    ),
  );
  const stockRows = [];
  for (const p of state.products) {
    const warehouses = new Set([
      ...Object.keys(p.stock || {}),
      ...Object.keys(p.reservations || {}),
      ...Object.keys(p.incoming || {}),
    ]);
    for (const warehouseKey of warehouses) {
      const w = warehouseMap.get(warehouseKey),
        key = p.inventory_id + ":" + p.product_id + ":" + warehouseKey,
        old = oldAllocations.get(key),
        stock = qty(p.stock?.[warehouseKey]),
        nodeId =
          links.get(
            "stock|" +
              identityKey([p.inventory_id, warehouseKey, p.product_id]),
          ) ||
          sourceMap.get(p.inventory_id + ":" + p.product_id) ||
          null,
        node = nodeMap.get(nodeId);
      const shares = structuredClone(
          old?.shares?.filter((s) => s.group_id !== null) || [],
        ),
        mode = old?.mode || "unassigned";
      if (mode === "encoded" && shares[0]) shares[0].quantity = stock;
      const assigned = shares.reduce((s, a) => s + Number(a.quantity || 0), 0),
        difference = stock === null ? null : Number(stock) - assigned;
      if (difference === null || difference > 0 || !shares.length)
        shares.push({
          group_id: null,
          business_group: "未分配",
          quantity:
            difference === null ? null : String(Math.max(0, difference)),
          channels: [],
        });
      stockRows.push({
        inventory_id: p.inventory_id,
        product_id: p.product_id,
        warehouse_key: warehouseKey,
        stock,
        reservations: qty(p.reservations?.[warehouseKey]),
        incoming: qty(p.incoming?.[warehouseKey]),
        fetched_at: captured,
        sku: p.sku || "",
        name: p.text_fields?.name || p.name || "",
        parent_id: Number(p.parent_id || 0),
        parent_sku:
          nativeMap.get(p.inventory_id + ":" + p.parent_id)?.sku || null,
        warehouse_name: w?.name || warehouseKey,
        warehouse_type: w?.warehouse_type || null,
        country: w?.country || null,
        catalog_name:
          state.catalogs.find((c) => Number(c.inventory_id) === p.inventory_id)
            ?.name || "",
        provider: null,
        account: null,
        external_sku: null,
        company_node_id: nodeId,
        company_path: ancestry(nodeId),
        company_sku: node?.code || null,
        company_name: node?.name || null,
        mode,
        stock_kind: old?.stock_kind || "unknown",
        allocation_version: old?.allocation_version || 0,
        source_stock: stock,
        allocated_quantity: String(assigned),
        difference: difference === null ? null : String(difference),
        unassigned_quantity:
          difference === null ? null : String(Math.max(0, difference)),
        allocation_conflict: difference !== null && difference < 0,
        channel_conflict: shares.some(
          (s) =>
            s.quantity === null ||
            s.channels.reduce((n, c) => n + c.quantity, 0) > Number(s.quantity),
        ),
        shares,
      });
    }
  }
  const products = new Map();
  for (const row of stockRows) {
    const key = row.company_node_id
      ? "company:" + row.company_node_id
      : "source:" + row.inventory_id + ":" + row.product_id;
    if (!products.has(key))
      products.set(key, {
        key,
        company_node_id: row.company_node_id,
        sku: row.company_sku || row.sku,
        name: row.company_name || row.name,
        physical_total: "0",
        unknown_physical_rows: 0,
        warehouses: {},
      });
    const target = products.get(key);
    target.warehouses[row.warehouse_key] ||= {
      name: row.warehouse_name,
      physical: "0",
      unknown_rows: 0,
    };
    const warehouse = target.warehouses[row.warehouse_key];
    if (row.stock_kind === "physical" && row.stock !== null) {
      target.physical_total = String(
        Number(target.physical_total) + Number(row.stock),
      );
      warehouse.physical = String(
        Number(warehouse.physical) + Number(row.stock),
      );
    } else if (["unknown", "physical"].includes(row.stock_kind)) {
      target.unknown_physical_rows++;
      warehouse.unknown_rows++;
    }
  }
  const responses = snapshot.responses;
  responses["/api/catalog/products"] = nodes;
  responses["/api/reports/options"] = {
    platforms: [
      ...new Set(state.orders.map((o) => String(o.order_source || ""))),
    ]
      .sort()
      .map((id) => ({ id, name: platformName(id) })),
    statuses: [...statuses],
    products: [],
    shops,
    order_dates: {
      first_created: Math.min(
        ...state.orders.map((o) => Number(o.date_add) || Infinity),
      ),
      last_created: Math.max(
        0,
        ...state.orders.map((o) => Number(o.date_add) || 0),
      ),
      last_confirmed_created:
        Math.max(
          0,
          ...state.orders
            .filter((o) => o.confirmed)
            .map((o) => Number(o.date_add) || 0),
        ) || null,
    },
    source_quality: {},
  };
  responses["/api/allocations/view"] = {
    rows: stockRows,
    products: [...products.values()],
    row_count: stockRows.length,
    privileged: true,
    platform_basis: "独占渠道份额；共享池单独列出",
  };
  const catalogs = state.catalogs.map((c) => ({
    id: Number(c.inventory_id),
    name: c.name,
    rows: stockRows.filter((r) => r.inventory_id === Number(c.inventory_id))
      .length,
  }));
  const warehouses = [];
  for (const c of catalogs)
    for (const w of state.warehouses) {
      const key = w.warehouse_type + "_" + w.warehouse_id;
      if (
        stockRows.some(
          (r) => r.inventory_id === c.id && r.warehouse_key === key,
        )
      )
        warehouses.push({
          inventory_id: c.id,
          id: key,
          name: w.name,
          warehouse_type: w.warehouse_type,
          country: w.country || null,
          provider: null,
          account: null,
        });
    }
  responses["/api/stock-dashboard/options"] = {
    catalogs,
    warehouses,
    default_catalog:
      catalogs.find((c) => c.rows)?.id || catalogs[0]?.id || null,
  };
  for (const c of catalogs) {
    const rows = stockRows.filter((r) => r.inventory_id === c.id);
    responses["/api/stock-dashboard/dataset?inventory_id=" + c.id] = {
      catalog: { inventory_id: c.id, name: c.name },
      rows,
      total_rows: rows.length,
      physical_stock: null,
      available_stock: null,
      in_transit: null,
      platform_saleable: null,
      last_sync: { status: "success", finished_at: captured },
    };
  }
  const earliest = dates.sort()[0] || shift(end, -29),
    start = earliest < shift(end, -29) ? earliest : shift(end, -29);
  snapshot.data_mode = "base_test";
  snapshot.captured_at = captured;
  snapshot.filters = {
    ...snapshot.filters,
    start: shift(end, -13),
    end,
    confirmed_only: "false",
  };
  snapshot.analysis = {
    filters: {
      ...snapshot.filters,
      start: start < todayStart ? todayStart : start,
    },
    lines,
  };
  snapshot.connection = {
    environment: "test",
    products: state.products.length,
    inventory: stockRows.length,
    orders: state.orders.length,
    shops: shops.length,
    confirmed_orders: state.orders.filter((o) => o.confirmed).length,
    unconfirmed_orders: state.orders.filter((o) => !o.confirmed).length,
    last_success: captured,
  };
  return snapshot;
}

let refreshInFlight = null;
export function createHandler(
  reference,
  html,
  { transport = fetch, cache, pause, settingsHtml = '' } = {},
) {
  async function cacheKey(request, env) {
    const hash = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(env.BASELINKER_TEST_API_TOKEN || ""),
    );
    const tag = Array.from(new Uint8Array(hash), (b) =>
      b.toString(16).padStart(2, "0"),
    ).join("");
    return new Request(
      new URL("/__private_base_test_snapshot_" + tag, request.url),
    );
  }
  return {
    async fetch(request, env, ctx) {
      const storage = cache || createSnapshotStore(env.DB);
      const url = new URL(request.url),
        json = (body, status = 200) =>
          Response.json(body, {
            status,
            headers: { "Cache-Control": "no-store" },
          });
      if (url.pathname === "/" || url.pathname === "/index.html")
        return new Response(html, {
          headers: {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
          },
        });
      if (url.pathname === "/favicon.ico")
        return new Response(null, { status: 204 });
      if (url.pathname === '/settings/shops')
        return new Response(settingsHtml, {headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}});
      if (['/api/shop-settings','/api/shop-settings/sync'].includes(url.pathname)) {
        try {
        if (!env.BASELINKER_TEST_API_TOKEN) return json({detail:'服务端尚未配置测试密钥'},503);
        const key = await cacheKey(request, env);
        const scope = new URL(key.url).pathname;
        if(url.pathname.endsWith('/sync'))return syncShopSources(request,env,scope,transport);
        const sources = await readShopSources(env.DB,scope);
        const cached = sources ? null : await storage?.match(key);
        const snapshot = cached ? await cached.json() : reference.snapshot;
        const shops = sources ? JSON.parse(sources.payload) : snapshot.responses?.['/api/reports/options']?.shops || [];
        const response = await handleShopSettings(request, env, scope, shops);
        if(request.method==='GET' && response.ok){const result=await response.json();return json({...result,sources_synced_at:sources?.updated_at||null});}
        return response;
        }catch{return json({detail:'店铺数据暂时无法读取，请稍后重试'},503);}
      }
      if (
        !["/api/base-test/snapshot", "/api/base-test/refresh"].includes(
          url.pathname,
        )
      )
        return json({ detail: "接口不存在" }, 404);
      const refreshing = url.pathname.endsWith("/refresh");
      if (request.method !== (refreshing ? "POST" : "GET"))
        return json({ detail: "请求方式不支持" }, 405);
      if (
        refreshing &&
        request.headers.get("Origin") &&
        request.headers.get("Origin") !== url.origin
      )
        return json({ detail: "请从当前测试看板刷新" }, 403);
      if (!env.BASELINKER_TEST_API_TOKEN)
        return json({ detail: "服务端尚未配置测试密钥" }, 503);
      const key = await cacheKey(request, env);
      if (!refreshing) {
        try {
        const cached = await storage?.match(key);
        return cached ? json(await cached.json()) : json(reference.snapshot);
        }catch{return json({detail:"暂时无法读取上次同步数据，请稍后重试"},503);}
      }
      try {
        if (!refreshInFlight) {
          refreshInFlight = fetchBaseState(
            env.BASELINKER_TEST_API_TOKEN,
            transport,
            pause,
          )
            .then((state) => createBaseSnapshot(state, reference))
            .finally(() => (refreshInFlight = null));
        }
        const snapshot = await refreshInFlight;
        if (storage)
          await storage.put(
            key,
            new Response(JSON.stringify(snapshot), {
              headers: {
                "Content-Type": "application/json",
                "Cache-Control": "max-age=86400",
              },
            }),
          );
        return json(snapshot);
      } catch (error) {
        const messages = [
          "Base 接口连接失败",
          "测试密钥无效，请更新服务端密钥",
          "Base 拒绝读取，请检查权限或接口限额",
          "产品分页未前进",
          "订单分页未前进",
          "产品详情不完整",
        ];
        const detail = messages.includes(error?.message)
          ? error.message
          : "测试数据同步失败，保留上次成功的数据";
        return json({ detail }, 502);
      }
    },
  };
}
