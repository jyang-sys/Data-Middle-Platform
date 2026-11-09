import assert from "node:assert/strict";
import {
  createHandler,
  createBaseSnapshot,
  fetchBaseState,
} from "../app/hosted/base-test-worker.mjs";
const secret = "fixture-secret-never-in-browser";
const product = {
  product_id: 10,
  inventory_id: 1,
  parent_id: 0,
  sku: "A",
  text_fields: { name: "产品 A" },
  stock: { bl_1: 100 },
  reservations: {},
  incoming: {},
};
const order = {
  order_id: 20,
  date_add: Date.parse("2026-11-07T12:00:00Z") / 1000,
  confirmed: false,
  order_status_id: 10,
  order_source: "personal",
  order_source_id: 0,
  customer_name: "DO NOT EXPOSE CUSTOMER",
  delivery_address: "PRIVATE ADDRESS",
  products: [
    { storage: "db", storage_id: 1, product_id: 10, sku: "A", quantity: 2 },
    { sku: "INVALID", quantity: null },
  ],
};
const reference = {
  snapshot: {
    filters: {
      start: "2026-10-27",
      end: "2026-11-09",
      timezone: "Asia/Shanghai",
      date_basis: "base_created",
      ownership_basis: "saved",
      granularity: "company_product",
    },
    responses: {
      "/api/catalog/products": [
        {
          id: 7,
          code: "SKU-A",
          name: "产品 A",
          kind: "sku",
          parent_id: null,
          active: 1,
        },
      ],
      "/api/catalog/links": [
        { domain: "catalog", source_key: "[1, 10]", node_id: 7 },
      ],
    },
  },
  annotations: {},
  line_owners: {},
};
const methods = [];
const transport = async (url, options) => {
  assert.equal(url, "https://api.baselinker.com/connector.php");
  assert.equal(options.headers["X-BLToken"], secret);
  const body = options.body,
    method = body.get("method"),
    params = JSON.parse(body.get("parameters"));
  methods.push(method);
  let data = {};
  if (method === "getOrderSources")
    data = { sources: { personal: { 0: "手工店铺" } } };
  else if (method === "getInventories")
    data = { inventories: [{ inventory_id: 1, name: "测试目录" }] };
  else if (method === "getInventoryWarehouses")
    data = {
      warehouses: [
        {
          warehouse_type: "bl",
          warehouse_id: 1,
          name: "测试仓",
          country: "PL",
        },
      ],
    };
  else if (method === "getInventoryProductsList")
    data = { products: params.page === 1 ? { 10: product } : {} };
  else if (method === "getInventoryProductsData")
    data = { products: { 10: product } };
  else if (method === "getOrders")
    data = { orders: params.id_from === 0 ? [order] : [] };
  else assert.fail("unexpected upstream method");
  return Response.json({ status: "SUCCESS", ...data });
};
const state = await fetchBaseState(secret, transport, async () => {});
assert.equal(state.orders.length, 1);
assert.ok(methods.every((m) => m.startsWith("get")));
const snapshot = createBaseSnapshot(
  state,
  reference,
  new Date("2026-11-09T12:00:00Z"),
);
assert.equal(snapshot.data_mode, "base_test");
assert.equal(snapshot.analysis.lines.length, 1);
assert.equal(snapshot.analysis.lines[0].quantity, 2);
assert.equal(snapshot.analysis.lines[0].confirmed, false);
assert.equal(snapshot.analysis.lines[0].company_node_id, 7);
assert.equal(snapshot.connection.orders, 1);
assert.equal(snapshot.responses["/api/allocations/view"].rows[0].stock, "100");
assert.equal(
  snapshot.responses["/api/allocations/view"].rows[0].stock_kind,
  "unknown",
);
assert.equal(
  snapshot.responses["/api/allocations/view"].products[0].physical_total,
  "0",
);
const encoded = JSON.stringify(snapshot);
assert.ok(!encoded.includes(secret));
assert.ok(!encoded.includes("PRIVATE ADDRESS"));
assert.ok(!encoded.includes("DO NOT EXPOSE CUSTOMER"));
const cacheStore = new Map(),
  cache = {
    async match(key) {
      return cacheStore.get(key.url)?.clone();
    },
    async put(key, response) {
      cacheStore.set(key.url, response.clone());
    },
  };
const handler = createHandler(reference, "<html>test</html>", {
    transport,
    cache,
    pause: async () => {},
  }),
  env = { BASELINKER_TEST_API_TOKEN: secret };
assert.equal(
  (
    await handler.fetch(
      new Request("https://test.site/api/base-test/refresh"),
      env,
    )
  ).status,
  405,
);
assert.equal(
  (
    await handler.fetch(
      new Request("https://test.site/api/base-test/refresh", {
        method: "POST",
        headers: { Origin: "https://other.site" },
      }),
      env,
    )
  ).status,
  403,
);
assert.equal(
  (
    await handler.fetch(
      new Request("https://test.site/api/base-test/snapshot"),
      {},
    )
  ).status,
  503,
);
assert.equal(
  (await handler.fetch(new Request("https://test.site/api/key"), env)).status,
  404,
);
const response = await handler.fetch(
  new Request("https://test.site/api/base-test/refresh", {
    method: "POST",
    headers: { Origin: "https://test.site" },
  }),
  env,
);
assert.equal(response.status, 200);
assert.ok(!JSON.stringify(await response.json()).includes(secret));
const cached = await handler.fetch(
  new Request("https://test.site/api/base-test/snapshot"),
  env,
);
assert.equal((await cached.json()).connection.orders, 1);
const broken = createHandler(reference, "<html/>", {
  transport: async () => {
    throw new Error(secret);
  },
  pause: async () => {},
});
const failure = await broken.fetch(
  new Request("https://test.site/api/base-test/refresh", { method: "POST" }),
  env,
);
assert.equal(failure.status, 502);
assert.ok(!(await failure.text()).includes(secret));
console.log(
  JSON.stringify({
    readonlyMethods: true,
    privateFieldsRemoved: true,
    secretNotExposed: true,
    unknownStockPreserved: true,
    refresh: true,
    cache: true,
    origin: true,
    failures: true,
  }),
);
