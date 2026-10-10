const reportState = {request: 0, params: null, data: null, detailKey: null, detailOffset: 0, detailTotal: 0, mappingRequest: 0, mappingOffset: 0, mappingVersion: 0, mappingKey: null, shops: [], orderDates: null, lastCreated: null};
function reportParams() {
  const values = {start: $('report-start').value, end: $('report-end').value, timezone: $('report-timezone').value, date_basis: $('report-date-basis').value,
    confirmed_only: $('report-confirmed').checked, granularity: $('report-granularity').value};
  const params = new URLSearchParams(values);
  for (const [field, id] of [['platform', 'report-platform'], ['shop_id', 'report-shop'], ['group_id', 'report-group'], ['employee_id', 'report-employee'], ['model', 'report-model'], ['source_sku', 'report-sku'], ['product_type', 'report-type']]) {
    if ($(id).value.trim()) params.set(field, $(id).value.trim());
  }
  const statuses = $('report-statuses').value.trim();
  if (statuses) statuses.split(/[,，]/).forEach(value => { if (!/^\d+$/.test(value.trim())) throw new Error('订单状态请填写整数 ID，并用逗号分隔。'); params.append('status_ids', value.trim()); });
  return params;
}
function reportToday() { return new Intl.DateTimeFormat('en-CA', {timeZone: $('report-timezone').value, year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date()); }
function recentReportDays(days) {
  const end = reportToday(); const start = new Date(end + 'T00:00:00Z'); start.setUTCDate(start.getUTCDate() - days + 1);
  $('report-start').value = start.toISOString().slice(0, 10); $('report-end').value = end;
}
function reportOptions(id, rows, emptyLabel, label) {
  const previous = $(id).value; $(id).replaceChildren(new Option(emptyLabel, ''));
  rows.forEach(row => $(id).append(new Option(label(row), row.id)));
  if ([...$(id).options].some(o => o.value === previous)) $(id).value = previous;
}
function reportShopOptions() {
  const platform = $('report-platform').value; const previous = $('report-shop').value;
  $('report-shop').replaceChildren(new Option('全部店铺', ''));
  if (platform) reportState.shops.filter(s => s.platform === platform).forEach(s => $('report-shop').append(new Option(s.name, s.shop_id)));
  $('report-shop').disabled = !platform;
  if ([...$('report-shop').options].some(o => o.value === previous)) $('report-shop').value = previous;
}
async function loadReportOptions() {
  const [options, groups, employees] = await Promise.all([api('/api/reports/options'), api('/api/management/groups'), api('/api/management/employees')]);
  reportOptions('report-platform', options.platforms, '全部平台', p => p.name);
  reportOptions('report-group', groups, '全部小组', g => g.name + (g.active ? '' : '（停用）'));
  reportOptions('report-employee', employees, '全部人员', e => e.name + (e.active ? '' : '（停用）'));
  const old = $('mapping-platform').value;
  $('mapping-platform').replaceChildren(new Option('全部平台（同编码通用）', '*'));
  options.platforms.forEach(p => $('mapping-platform').append(new Option(p.name, p.id)));
  if ([...$('mapping-platform').options].some(o => o.value === old)) $('mapping-platform').value = old;
  reportState.orderDates = options.order_dates;
  reportState.lastCreated = options.order_dates.last_confirmed_created || options.order_dates.last_created;
  reportState.shops = options.shops; reportShopOptions();
  $('report-statuses').title = '当前来源状态 ID：' + options.statuses.join(', ');
}
function reportAction(target, rows, action, text = '核对明细') {
  $(target).querySelectorAll('tbody tr').forEach((tr, index) => {
    if (index >= rows.length) return;
    const button = document.createElement('button'); button.className = 'button'; button.textContent = text;
    button.addEventListener('click', () => Promise.resolve().then(() => action(rows[index])).catch(e => notice(e.message, true))); tr.lastElementChild.replaceChildren(button);
  });
}
async function refreshSalesReport() {
  const request = ++reportState.request; const params = reportParams();
  const data = await api('/api/reports/sales?' + params);
  if (request !== reportState.request) return;
  reportState.params = params; reportState.data = data; reportState.detailKey = null; reportState.detailOffset = 0;
  $('report-filter-changed').hidden = reportParams().toString() === params.toString();
  $('report-export').href = '/api/reports/export?' + params; $('report-export').setAttribute('aria-disabled', window.__DATA_SNAPSHOT__ ? 'true' : 'false');
  $('report-units').textContent = number(data.total_units); $('report-orders').textContent = number(data.order_count);
  $('report-lines').textContent = number(data.line_count); $('report-unmapped-count').textContent = number(data.diagnostics.unmapped_lines);
  const platforms = data.platforms;
  const monthlyRows = data.monthly.map(r => [r.month, r.product_type, r.label, ...platforms.map(p => number(r.platforms[p.id] || 0)), number(r.total), '']);
  if (monthlyRows.length) monthlyRows.push(['期间合计', '', '', ...platforms.map(p => number(data.monthly.reduce((sum, r) => sum + (r.platforms[p.id] || 0), 0))), number(data.total_units), '']);
  table('report-monthly', ['月份', '产品类型', '型号 / 规格', ...platforms.map(p => p.name), '总计', '核对'], monthlyRows);
  reportAction('report-monthly', data.monthly, row => loadReportDetails({month: row.month, key_json: JSON.stringify(row.key), label: row.label}));
  if (!window.__DATA_SNAPSHOT__) $('report-monthly').querySelectorAll('tbody tr').forEach((tr,index) => {
    const row = data.monthly[index]; if (!row || row.key[0] !== 'source' || row.key.length !== 3) return;
    const button = document.createElement('button'); button.className = 'button'; button.textContent = '查库存';
    button.addEventListener('click', () => linkSourceStock(row.key).catch(error => notice(error.message,true)));tr.lastElementChild.append(button);
  });
  const dailyRows = data.daily.map(r => [r.date, ...platforms.map(p => number(r.platforms[p.id] || 0)), number(r.total)]);
  dailyRows.push(['期间合计', ...platforms.map(p => number(data.daily.reduce((sum, r) => sum + (r.platforms[p.id] || 0), 0))), number(data.total_units)]);
  table('report-daily', ['日期', ...platforms.map(p => p.name), '合计'], dailyRows);
  drawUnitTrend(data.daily);
  const days = data.daily.map(r => r.date);
  table('report-product-daily', ['平台', '产品类型', '型号 / 规格', '总销量', ...days], data.product_daily.map(r => [platforms.find(p => p.id === r.platform)?.name || r.platform, r.product_type, r.label, number(r.total), ...days.map(day => number(r.daily[day] || 0))]));
  for (const name of ['groups', 'employees', 'shops']) table('report-' + name, ['名称', '销量'], data[name].map(r => [name === 'shops' ? `${r.identity_type} / ${r.name}` : r.name, number(r.quantity)]), [1]);
  table('report-unmapped', ['来源平台', '原 SKU', '销量', '操作'], data.unmapped.map(r => [r.platform, r.source_sku || '未提供 SKU', number(r.quantity), '']));
  reportAction('report-unmapped', data.unmapped, row => { if (!row.source_sku) { notice('该商品未提供 SKU，请先修正来源编码。', true); return; } editReportMapping({platform: row.platform, source_sku: row.source_sku, version: 0, product_type: '未分类'}); $('report-mapping-form').scrollIntoView({behavior: 'smooth'}); }, '维护映射');
  $('report-details').replaceChildren(); $('report-details-page').textContent = ''; $('report-detail-scope').textContent = '点击月度汇总行核对对应明细，或查看全部明细。';
  await loadReportMappings();
}
async function loadReportDetails(scope = null, offset = 0) {
  if (!reportState.params) throw new Error('请先查询报表。');
  if (scope !== null) reportState.detailKey = scope;
  reportState.detailOffset = offset;
  const params = new URLSearchParams(reportState.params); params.set('limit', '50'); params.set('offset', offset);
  const scopeKey = reportState.detailKey;
  if (scopeKey) { if (scopeKey.month) params.set('month', scopeKey.month); if (scopeKey.key_json) params.set('key_json', scopeKey.key_json); }
  const request = reportState.request;
  const data = await api('/api/reports/details?' + params);
  if (request !== reportState.request || offset !== reportState.detailOffset || scopeKey !== reportState.detailKey) return;
  reportState.detailTotal = data.total;
  $('report-detail-scope').textContent = `${scopeKey ? scopeKey.month + ' · ' + scopeKey.label : '本次查询全部明细'} · 合计 ${number(data.total_units)} 件`;
  table('report-details', ['日期', 'Base 订单 ID', '平台订单号', '平台', '店铺', '负责人', '销售小组', '原 SKU', '来源产品名称', '父 SKU', '型号', '配置', '颜色', '销量'], data.lines.map(r => [r.date, r.order_id, r.external_order_id, r.platform_name, r.shop_name, r.sales_owner || '未分配', r.business_group || '未分配', r.source_sku, r.source_name, r.parent_sku, r.model, r.specification, r.color, number(r.quantity)]));
  $('report-details-page').textContent = `${offset + (data.total ? 1 : 0)}–${Math.min(offset + 50, data.total)} / ${data.total} 条`;
  $('report-details-prev').disabled = offset === 0; $('report-details-next').disabled = offset + 50 >= data.total;
}
function editReportMapping(row) {
  for (const [id, field] of [['mapping-platform', 'platform'], ['mapping-sku', 'source_sku'], ['mapping-model', 'model'], ['mapping-spec', 'specification'], ['mapping-color', 'color'], ['mapping-type', 'product_type']]) {
    if (id === 'mapping-platform' && ![...$(id).options].some(o => o.value === row[field])) $(id).append(new Option(row[field], row[field]));
    $(id).value = row[field] || (field === 'product_type' ? '未分类' : '');
  }
  reportState.mappingVersion = row.version || 0; reportState.mappingKey = JSON.stringify([row.platform, row.source_sku]);
  $('mapping-version').textContent = `已读取版本 ${reportState.mappingVersion}；平台专用映射优先于全部平台通用映射。`;
}
async function loadReportMappings(offset = reportState.mappingOffset) {
  const request = ++reportState.mappingRequest;
  reportState.mappingOffset = offset;
  const data = await api('/api/reports/mappings?' + new URLSearchParams({limit: 50, offset, search: $('mapping-search').value.trim()}));
  if (request !== reportState.mappingRequest) return;
  table('report-mappings', ['平台范围', '原 SKU', '型号', '配置', '颜色', '产品类型', '版本', '操作'], data.mappings.map(r => [r.platform, r.source_sku, r.source_name, r.parent_sku, r.model, r.specification, r.color, r.product_type, r.version, '']));
  reportAction('report-mappings', data.mappings, editReportMapping, '编辑');
  $('mapping-page').textContent = `${offset + (data.total ? 1 : 0)}–${Math.min(offset + 50, data.total)} / ${data.total}`;
  $('mapping-prev').disabled = offset === 0; $('mapping-next').disabled = offset + 50 >= data.total;
}
$('report-form').addEventListener('submit', event => { event.preventDefault(); refreshSalesReport().catch(e => notice(e.message, true)); });
$('report-form').addEventListener('input', () => { $('report-filter-changed').hidden = false; });
$('report-platform').addEventListener('change', () => { $('report-shop').value = ''; reportShopOptions(); });
$('report-month').addEventListener('change', () => {
  const value = $('report-month').value; if (!value) return;
  const [year, month] = value.split('-').map(Number); $('report-start').value = value + '-01';
  $('report-end').value = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
});
document.querySelectorAll('[data-report-days]').forEach(button => button.addEventListener('click', () => { recentReportDays(Number(button.dataset.reportDays)); refreshSalesReport().catch(e => notice(e.message, true)); }));
$('report-all-details').addEventListener('click', () => { reportState.detailKey = null; loadReportDetails(null).catch(e => notice(e.message, true)); });
$('report-details-prev').addEventListener('click', () => loadReportDetails(null, Math.max(0, reportState.detailOffset - 50)).catch(e => notice(e.message, true)));
$('report-details-next').addEventListener('click', () => loadReportDetails(null, reportState.detailOffset + 50).catch(e => notice(e.message, true)));
$('report-mapping-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const body = {platform: $('mapping-platform').value, source_sku: $('mapping-sku').value.trim(), model: $('mapping-model').value.trim(), specification: $('mapping-spec').value.trim(), color: $('mapping-color').value.trim(), product_type: $('mapping-type').value.trim(), actor: $('mapping-actor').value.trim(), reason: $('mapping-reason').value.trim()};
    body.expected_version = reportState.mappingKey === JSON.stringify([body.platform, body.source_sku]) ? reportState.mappingVersion : 0;
    const saved = await api('/api/reports/mappings', managementJson('PUT', body)); editReportMapping(saved); notice('映射已保存。请重新查询销量以应用新映射。');
    await loadReportMappings(); $('report-filter-changed').hidden = false;
  } catch (error) { notice(error.message, true); }
});
$('mapping-reset').addEventListener('click', () => { $('report-mapping-form').reset(); reportState.mappingVersion = 0; reportState.mappingKey = null; $('mapping-version').textContent = '新增映射；已有映射请先从列表读取。'; });
$('mapping-history').addEventListener('click', async () => {
  try {
    const data = await api('/api/reports/mappings/history?' + new URLSearchParams({platform: $('mapping-platform').value, source_sku: $('mapping-sku').value.trim()}));
    table('report-mapping-history', ['时间', '操作人', '原因', '变更前', '变更后'], data.map(r => [new Date(r.created_at).toLocaleString('zh-CN'), r.actor, r.reason, JSON.stringify(r.before), JSON.stringify(r.after)]));
  } catch (e) { notice(e.message, true); }
});
$('mapping-search-button').addEventListener('click', () => loadReportMappings(0).catch(e => notice(e.message, true)));
$('mapping-prev').addEventListener('click', () => loadReportMappings(Math.max(0, reportState.mappingOffset - 50)).catch(e => notice(e.message, true)));
$('mapping-next').addEventListener('click', () => loadReportMappings(reportState.mappingOffset + 50).catch(e => notice(e.message, true)));
recentReportDays(7);
if (window.__DATA_SNAPSHOT__) {
  const f = window.__DATA_SNAPSHOT__.filters; $('report-start').value = f.start; $('report-end').value = f.end; $('report-granularity').value = f.granularity;
  $('report-form').querySelectorAll('input,select,button').forEach(el => el.disabled = true);
}
loadReportOptions().then(() => {
  if (!window.__DATA_SNAPSHOT__ && reportState.lastCreated) {
    const latest = new Intl.DateTimeFormat('en-CA', {timeZone: $('report-timezone').value, year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date(reportState.lastCreated * 1000));
    if (latest < $('report-start').value) {
      const start = new Date(latest + 'T00:00:00Z'); start.setUTCDate(start.getUTCDate() - 13);
      $('report-start').value = start.toISOString().slice(0,10); $('report-end').value = latest;
    }
  }
  return refreshSalesReport();
}).catch(e => notice(e.message, true));

function drawUnitTrend(rows) {
  const ns = 'http://www.w3.org/2000/svg', width = 900, height = 230, pad = 36;
  const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', '每日商品件数走势；详细数值见下方每日销量表');
  const maximum = Math.max(1, ...rows.map(r => r.total));
  const points = rows.map((r, i) => [pad + i * (width - 2 * pad) / Math.max(1, rows.length - 1), height - pad - r.total / maximum * (height - 2 * pad)]);
  function add(name, attributes, text) { const el = document.createElementNS(ns, name); Object.entries(attributes).forEach(([k,v]) => el.setAttribute(k, v)); if (text) el.textContent = text; svg.append(el); return el; }
  [0, maximum].forEach(value => { const y = height - pad - value / maximum * (height - 2 * pad); add('line', {x1: pad, x2: width-pad, y1: y, y2: y, stroke: '#e5ece9'}); add('text', {x: 5, y: y+4, fill: '#657181', 'font-size': 12}, `${value}件`); });
  add('polyline', {points: points.map(p => p.join(',')).join(' '), fill: 'none', stroke: '#2457d6', 'stroke-width': 3});
  points.forEach(([x,y], i) => { const dot = add('circle', {cx: x, cy: y, r: 4, fill: '#2457d6', tabindex: 0}); const title = document.createElementNS(ns, 'title'); title.textContent = `${rows[i].date}：${rows[i].total}件`; dot.append(title); dot.setAttribute('aria-label', title.textContent); });
  if (rows.length) { add('text', {x: pad, y: height-8, fill: '#657181', 'font-size': 12}, rows[0].date); add('text', {x: width-pad, y: height-8, 'text-anchor': 'end', fill: '#657181', 'font-size': 12}, rows.at(-1).date); }
  $('report-trend').replaceChildren(svg);
}

$('report-latest').addEventListener('click', () => {
  reportState.lastCreated = $('report-confirmed').checked ? reportState.orderDates?.last_confirmed_created : reportState.orderDates?.last_created;
  if (!reportState.lastCreated) { notice('系统暂无符合确认条件的有效订单日期。', true); return; }
  const end = new Intl.DateTimeFormat('en-CA', {timeZone: $('report-timezone').value, year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date(reportState.lastCreated * 1000));
  const start = new Date(end + 'T00:00:00Z'); start.setUTCDate(start.getUTCDate() - 13);
  $('report-start').value = start.toISOString().slice(0,10); $('report-end').value = end;
  refreshSalesReport().catch(error => notice(error.message, true));
});
