const $ = (id) => document.getElementById(id);
let noticeTimer;
let dashboardRequest = 0;
let syncing = false;
const number = (value) => new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2}).format(value);
function notice(message, error = false) {
  clearTimeout(noticeTimer); $('notice').textContent = message;
  $('notice').classList.toggle('error', error); $('notice').hidden = false;
  noticeTimer = setTimeout(() => { $('notice').hidden = true; }, 6500);
}
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) {
    const message = typeof result.detail === 'string' ? result.detail : '请求参数不正确，请检查后重试。';
    throw new Error(message);
  }
  return result;
}
function switchTab(id) {
  document.querySelectorAll('.view').forEach((view) => { view.hidden = view.id !== id; });
  document.querySelectorAll('[data-tab]').forEach((button) => button.classList.toggle('active', button.dataset.tab === id));
  $('breadcrumb').textContent = {dashboard: '销售分析', connection: '数据接入', datasets: '表格工具', 'base-data': '中台基础数据', ownership: '员工与归属'}[id];
  if (id === 'ownership') refreshManagement().catch((error) => notice(error.message, true));
  if (id === 'connection') checkConnection().catch((error) => notice(error.message, true));
  if (id === 'base-data') refreshBase().catch((error) => notice(error.message, true));
  if (id === 'datasets') loadDatasets().catch((error) => notice(error.message, true));
}
document.querySelectorAll('[data-tab], [data-open]').forEach((button) => {
  button.addEventListener('click', () => switchTab(button.dataset.tab || button.dataset.open));
});
function table(target, columns, rows, numeric = []) {
  const root = $(target); root.replaceChildren();
  if (!rows.length) { const p = document.createElement('p'); p.className = 'table-empty muted'; p.textContent = '暂无数据'; root.append(p); return; }
  const element = document.createElement('table');
  const head = element.createTHead().insertRow();
  columns.forEach((name, i) => { const th = document.createElement('th'); th.textContent = name; if (numeric.includes(i)) th.className = 'numeric'; head.append(th); });
  const body = element.createTBody();
  rows.forEach((values) => { const row = body.insertRow(); values.forEach((value, i) => { const cell = row.insertCell(); cell.textContent = value ?? '—'; if (numeric.includes(i)) cell.className = 'numeric'; }); });
  root.append(element);
}
async function refreshDashboard() {
  const request = ++dashboardRequest;
  const params = new URLSearchParams({source: $('source').value, days: $('days').value});
  if ($('currency').value) params.set('currency', $('currency').value);
  const data = await api('/api/sales/dashboard?' + params);
  if (request !== dashboardRequest) return;
  $('units').textContent = number(data.units); $('orders').textContent = number(data.orders);
  $('revenue').textContent = number(data.revenue); $('average').textContent = number(data.average_order);
  $('money-unit').textContent = data.currency || '';
  $('demo-banner').hidden = data.source !== 'demo'; $('empty-sales').hidden = data.orders > 0;
  $('last-sync').textContent = data.last_sync ? '最近同步 ' + new Date(data.last_sync.updated_at).toLocaleString('zh-CN') : '尚未同步';
  $('currency').replaceChildren();
  if (!data.currencies.length) $('currency').append(new Option('暂无币种', ''));
  data.currencies.forEach((currency) => $('currency').append(new Option(currency, currency, false, currency === data.currency)));
  table('platform-table', ['平台', '订单', '销量', '销售额'], data.platforms.map((p) => [p.name, number(p.orders), number(p.quantity), number(p.revenue)]), [1, 2, 3]);
  table('shop-table', ['平台 / 店铺', '来源 ID', '订单', '销量', '销售额'], data.shops.map((p) => [p.platform + ' / ' + p.name, p.id, number(p.orders), number(p.quantity), number(p.revenue)]), [1, 2, 3, 4]);
  table('sku-table', ['排名', 'SKU', '商品', '销量', '商品销售额'], data.products.map((p, i) => [i + 1, p.sku, p.name, number(p.quantity), number(p.revenue)]), [0, 3, 4]);
  table('order-table', ['订单 ID', '确认时间', '销售额', '状态 ID'], data.recent_orders.map((p) => [p.id, new Date(p.date).toLocaleString('zh-CN'), number(p.total), p.status_id]), [0, 2, 3]);
  const trend = $('trend'); trend.replaceChildren();
  const max = Math.max(1, ...data.daily.map((day) => day.revenue));
  const step = Math.max(1, Math.ceil(data.daily.length / 7));
  data.daily.forEach((day, i) => {
    const bar = document.createElement('div'); bar.className = 'bar'; bar.tabIndex = 0;
    bar.title = `${day.date}：${number(day.revenue)} ${data.currency || ''} · ${day.orders} 单`;
    bar.setAttribute('aria-label', bar.title);
    const fill = document.createElement('div'); fill.className = 'bar-fill'; fill.style.height = Math.max(0, day.revenue / max * 100) + '%'; bar.append(fill);
    if (i % step === 0) { const label = document.createElement('span'); label.className = 'bar-label'; label.textContent = day.date.slice(5); bar.append(label); }
    trend.append(bar);
  });
}
async function checkConnection() {
  const data = await api('/api/sales/connection');
  $('connection-badge').textContent = data.configured ? (data.last_sync ? '已连接 · 最近同步成功' : '令牌已配置 · 待同步验证') : '待配置令牌';
  $('connection-detail').textContent = data.last_sync ? '最近成功同步：' + new Date(data.last_sync.updated_at).toLocaleString('zh-CN') + '，读取 ' + data.last_sync.order_count + ' 条订单。' : '尚未成功同步真实数据。保存令牌配置并不代表接口已连通。';
}
async function syncOrders() {
  if (syncing) return;
  syncing = true;
  const buttons = [$('sync-top'), $('sync-connection')];
  buttons.forEach((button) => { button.disabled = true; button.textContent = '正在同步…'; });
  try {
    const result = await api('/api/sales/sync', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({days: Number($('days').value)})});
    $('source').value = 'baselinker'; $('currency').value = ''; notice(result.message);
    await refreshDashboard(); await checkConnection();
  } catch (error) { notice(error.message, true); }
  finally { syncing = false; buttons.forEach((button) => { button.disabled = false; button.textContent = '↻ 同步订单'; }); }
}
async function loadDemo(event) {
  const button = event.currentTarget; button.disabled = true;
  try {
    await api('/api/sales/demo', {method: 'POST'}); $('source').value = 'demo'; $('currency').value = '';
    switchTab('dashboard'); await refreshDashboard(); notice('已加载演示数据，当前展示并非真实销售。');
  } catch (error) { notice(error.message, true); } finally { button.disabled = false; }
}
['sync-top', 'sync-connection'].forEach((id) => $(id).addEventListener('click', syncOrders));
['demo-main', 'demo-connection'].forEach((id) => $(id).addEventListener('click', loadDemo));
$('check-connection').addEventListener('click', () => checkConnection().catch((error) => notice(error.message, true)));
['source', 'days', 'currency'].forEach((id) => $(id).addEventListener('change', () => {
  if (id !== 'currency') $('currency').value = '';
  refreshDashboard().catch((error) => notice(error.message, true));
}));
async function loadDatasets() {
  const datasets = await api('/api/datasets'); $('dataset-list').replaceChildren();
  if (!datasets.length) { const p = document.createElement('p'); p.className = 'muted'; p.textContent = '还没有数据集，上传一份表格开始。'; $('dataset-list').append(p); }
  datasets.forEach((dataset) => {
    const card = document.createElement('article'); card.className = 'dataset-card';
    const heading = document.createElement('h2'); heading.textContent = dataset.name;
    const description = document.createElement('p'); description.textContent = `${number(dataset.row_count)} 行 · ${dataset.columns.length} 列`;
    const actions = document.createElement('div'); actions.className = 'actions';
    const preview = document.createElement('button'); preview.className = 'button'; preview.textContent = '预览 / 查询';
    preview.addEventListener('click', async () => {
      try { const data = await api(`/api/datasets/${dataset.id}/preview`); $('preview-title').textContent = dataset.name + ' · 前 50 行'; table('preview-table', data.columns, data.rows); $('sql').value = `SELECT * FROM "${dataset.table_name}" LIMIT 50`; }
      catch (error) { notice(error.message, true); }
    });
    const remove = document.createElement('button'); remove.className = 'button'; remove.textContent = '删除';
    remove.addEventListener('click', async () => {
      if (!confirm(`确定删除数据集“${dataset.name}”？此操作无法撤销。`)) return;
      try { await api(`/api/datasets/${dataset.id}`, {method: 'DELETE'}); await loadDatasets(); $('preview-title').textContent = '数据预览'; $('preview-table').replaceChildren(); $('query-table').replaceChildren(); $('sql').value = ''; notice('数据集已删除。'); }
      catch (error) { notice(error.message, true); }
    });
    actions.append(preview, remove); card.append(heading, description, actions); $('dataset-list').append(card);
  });
}
$('upload-file').addEventListener('change', async (event) => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 10 * 1024 * 1024) { notice('文件不能超过 10 MB。', true); event.target.value = ''; return; }
  const form = new FormData(); form.append('file', file); event.target.disabled = true;
  try { await api('/api/datasets', {method: 'POST', body: form}); await loadDatasets(); notice('表格上传成功。'); }
  catch (error) { notice(error.message, true); } finally { event.target.value = ''; event.target.disabled = false; }
});
$('run-query').addEventListener('click', async () => {
  $('run-query').disabled = true;
  try { const data = await api('/api/query', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({sql: $('sql').value})}); table('query-table', data.columns, data.rows); $('query-status').textContent = `${data.rows.length} 行 · ${data.elapsed_ms} ms${data.truncated ? ' · 已截取前 200 行' : ''}`; }
  catch (error) { notice(error.message, true); } finally { $('run-query').disabled = false; }
});
refreshDashboard().catch((error) => notice(error.message, true));
checkConnection().catch((error) => notice(error.message, true));

async function refreshBase() {
  const [state, products, inventory, shops, orders] = await Promise.all([
    api('/api/base/status'), api('/api/base/products'), api('/api/base/inventory'),
    api('/api/base/shops'), api('/api/base/orders')
  ]);
  $('base-status').textContent = `${state.configured ? '令牌已配置' : '待配置令牌'} · 产品 ${state.counts.products} · 库存明细 ${state.counts.inventory} · 订单 ${state.counts.orders} · 店铺 ${state.counts.shops}`;
  table('base-jobs', ['数据域', '状态', '已保存记录', '开始时间', '说明'], state.jobs.map(j => [j.domain, j.status, j.records, new Date(j.started_at).toLocaleString('zh-CN'), j.error || '—']));
  table('base-products', ['目录', '产品 ID', '父 SKU', '子 SKU', '名称', '状态'], products.map(p => [p.inventory_id, p.product_id, p.parent_sku, p.child_sku, p.name, p.active ? '有效' : '来源已移除']));
  table('base-inventory', ['目录 / 产品', '仓库', 'Base 库存', '预占', '供应商待入库', '获取时间'], inventory.map(i => [`${i.inventory_id} / ${i.sku || i.product_id}`, i.warehouse_name || i.warehouse_key, i.stock, i.reservations, i.incoming, new Date(i.fetched_at).toLocaleString('zh-CN')]));
  table('base-shops', ['平台', '店铺 ID', '店铺名称', '状态'], shops.map(s => [s.platform, s.shop_id, s.name, s.active ? '有效' : '来源已移除']));
  table('base-orders', ['订单 ID', '平台 / 店铺', 'Base 创建时间', '是否确认', '负责人', '业务小组'], orders.map(o => [o.order_id, `${o.platform} / ${o.shop_id}`, o.created_at ? new Date(o.created_at * 1000).toLocaleString('zh-CN') : '未知', o.confirmed ? '已确认' : '未确认', o.sales_owner, o.business_group]));
}
$('base-refresh').addEventListener('click', () => refreshBase().catch(error => notice(error.message, true)));
document.querySelectorAll('[data-base-sync]').forEach(button => button.addEventListener('click', async () => {
  button.disabled = true;
  try {
    await api('/api/base/sync/' + button.dataset.baseSync, {method: 'POST'});
    notice('该数据域同步完成。');
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; refreshBase().catch(error => notice(error.message, true)); }
}));
