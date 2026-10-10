const managementState = {groups: [], employees: [], shops: [], orders: [], total: 0, offset: 0, selected: new Map(), loadedOrder: null, history: null, historyOffset: 0};
let managementOrderRequest = 0;
function invalidateLoadedOrder() {
  managementState.loadedOrder = null; managementOrderRequest++;
  $('order-version').textContent = '请重新读取该订单的当前归属。';
}
const managementJson = (method, body) => ({method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
function changeMetadata() {
  const actor = $('change-actor').value.trim();
  if (!actor) throw new Error('请先填写操作人。');
  return {actor, reason: $('change-reason').value.trim()};
}
function entityOptions(id, entities, kind = 'empty') {
  const select = $(id); const previous = select.value;
  const legacy = [...select.options].find((option) => option.value === '__legacy__');
  select.replaceChildren();
  if (kind === 'batch') select.append(new Option('保持不变', '__keep__'), new Option('清空归属', '__clear__'));
  else select.append(new Option(kind === 'filter' ? '全部小组' : '未分配 / 清空', ''));
  entities.forEach((entity) => {
    const option = new Option(`${entity.code} · ${entity.name}${entity.active ? '' : '（已停用）'}`, entity.id);
    option.disabled = !entity.active && kind !== 'filter'; select.append(option);
  });
  if (legacy) select.append(legacy);
  if ([...select.options].some((option) => option.value === previous)) select.value = previous;
}
function entityTable(target, columns, rows, onEdit, onHistory) {
  table(target, [...columns, '操作'], rows.map((row) => row.values.concat('')));
  $(target).querySelectorAll('tbody tr').forEach((tr, index) => {
    const actions = document.createElement('div'); actions.className = 'management-actions';
    if (onEdit) { const button = document.createElement('button'); button.className = 'button'; button.textContent = '编辑'; button.addEventListener('click', () => onEdit(rows[index].entity)); actions.append(button); }
    if (onHistory) { const button = document.createElement('button'); button.className = 'button'; button.textContent = '历史'; button.addEventListener('click', () => onHistory(rows[index].entity)); actions.append(button); }
    tr.lastElementChild.replaceChildren(actions);
  });
}
async function refreshManagement() {
  const [groups, employees, shops] = await Promise.all([api('/api/management/groups'), api('/api/management/employees'), api('/api/management/shops')]);
  Object.assign(managementState, {groups, employees, shops});
  ['employee-group', 'shop-group', 'order-group'].forEach((id) => entityOptions(id, groups));
  ['shop-employee', 'order-employee'].forEach((id) => entityOptions(id, employees));
  entityOptions('batch-employee', employees, 'batch'); entityOptions('batch-group', groups, 'batch');
  entityOptions('order-group-filter', groups, 'filter');
  entityTable('group-table', ['编号', '名称', '状态'], groups.map((group) => ({entity: group, values: [group.code, group.name, group.active ? '启用' : '停用']})), (group) => {
    $('group-id').value = group.id; $('group-code').value = group.code; $('group-name').value = group.name; $('group-active').checked = !!group.active;
  }, (group) => showManagementHistory({entity_type: 'group', entity_id: group.id}, `小组 ${group.name}`));
  entityTable('employee-table', ['编号', '姓名', '小组', '状态'], employees.map((employee) => ({entity: employee, values: [employee.code, employee.name, employee.group_name, employee.active ? '在职' : '停用']})), (employee) => {
    $('employee-id').value = employee.id; $('employee-code').value = employee.code; $('employee-name').value = employee.name;
    $('employee-group').value = employee.group_id || ''; $('employee-active').checked = !!employee.active;
  }, (employee) => showManagementHistory({entity_type: 'employee', entity_id: employee.id}, `员工 ${employee.name}`));
  table('ownership-shops', ['平台', '店铺', '默认负责人', '默认小组', '版本'], shops.map((shop) => [shop.platform, shop.name, shop.sales_owner, shop.business_group, shop.version]));
  ['ownership-shop', 'order-shop-filter'].forEach((id) => {
    const select = $(id); const previous = select.value; select.replaceChildren();
    select.append(new Option(id === 'ownership-shop' ? '选择店铺' : '全部平台 / 店铺', ''));
    shops.forEach((shop) => {
      const option = new Option(`${shop.platform} / ${shop.name}${shop.active ? '' : '（已移除）'}`, JSON.stringify([shop.platform, shop.shop_id]));
      option.disabled = id === 'ownership-shop' && !shop.active; select.append(option);
    });
    if ([...select.options].some((option) => option.value === previous)) select.value = previous;
  });
  loadShopForm(); await loadManagementOrders();
}
function currentShop() {
  if (!$('ownership-shop').value) throw new Error('请先选择店铺。');
  const [platform, id] = JSON.parse($('ownership-shop').value);
  return managementState.shops.find((shop) => shop.platform === platform && shop.shop_id === id);
}
function loadShopForm() {
  if (!$('ownership-shop').value) { $('shop-version').textContent = '选择店铺后读取当前版本。'; return; }
  const shop = currentShop(); $('shop-employee').value = shop.employee_id || ''; $('shop-group').value = shop.group_id || '';
  $('shop-version').textContent = `当前版本 ${shop.version} · 原负责人 ${shop.sales_owner || '未分配'} · 原小组 ${shop.business_group || '未分配'}`;
}
function bindManagementForm(id, handler) {
  $(id).addEventListener('submit', async (event) => {
    event.preventDefault(); const button = event.submitter; if (button) button.disabled = true;
    try { await handler(); } catch (error) { notice(error.message, true); }
    finally { if (button) button.disabled = false; }
  });
}
['group', 'employee'].forEach((kind) => {
  $(kind + '-form').addEventListener('reset', () => { $(kind + '-id').value = ''; });
  bindManagementForm(kind + '-form', async () => {
    const body = {code: $(kind + '-code').value, name: $(kind + '-name').value, active: $(kind + '-active').checked, ...changeMetadata()};
    if (kind === 'employee') body.group_id = Number($('employee-group').value) || null;
    const id = $(kind + '-id').value;
    await api('/api/management/' + (kind === 'group' ? 'groups' : 'employees') + (id ? '/' + id : ''), managementJson(id ? 'PUT' : 'POST', body));
    $(kind + '-form').reset(); await refreshManagement(); notice('已保存。');
  });
});
$('ownership-shop').addEventListener('change', loadShopForm);
function autoSelectGroup(employeeSelect, groupSelect) {
  const employee = managementState.employees.find((row) => String(row.id) === $(employeeSelect).value);
  if (employee) $(groupSelect).value = employee.group_id || '';
}
[['shop-employee', 'shop-group'], ['order-employee', 'order-group']].forEach(([employee, group]) => $(employee).addEventListener('change', () => autoSelectGroup(employee, group)));
bindManagementForm('shop-form', async () => {
  const shop = currentShop(); const body = {expected_version: shop.version, ...changeMetadata()};
  const employee = Number($('shop-employee').value) || null; const group = Number($('shop-group').value) || null;
  if (employee !== shop.employee_id) body.employee_id = employee;
  if (group !== shop.group_id) body.group_id = group;
  if (!('employee_id' in body) && !('group_id' in body)) { notice('归属未变化。'); return; }
  await api(`/api/management/shops/${encodeURIComponent(shop.platform)}/${encodeURIComponent(shop.shop_id)}/ownership`, managementJson('PATCH', body));
  await refreshManagement(); notice('店铺归属已保存；订单未被自动改写。');
});
function displayOrderSelection(id, value, legacyName) {
  const select = $(id); [...select.options].filter((option) => option.value === '__legacy__').forEach((option) => option.remove());
  if (value == null && legacyName) { select.append(new Option(`历史文本：${legacyName}（保留）`, '__legacy__')); select.value = '__legacy__'; }
  else select.value = value || '';
}
async function loadOrderOwnership() {
  const id = $('ownership-order-id').value;
  if (!id) throw new Error('请填写订单 ID。');
  const request = ++managementOrderRequest;
  managementState.loadedOrder = null; $('order-version').textContent = '正在读取当前归属…';
  const data = await api(`/api/management/orders/${encodeURIComponent(id)}/ownership`);
  if (request !== managementOrderRequest || id !== $('ownership-order-id').value) return;
  managementState.loadedOrder = data;
  displayOrderSelection('order-employee', data.employee_id, data.sales_owner);
  displayOrderSelection('order-group', data.group_id, data.business_group);
  $('order-extra').value = JSON.stringify(data.extra_fields, null, 2);
  $('order-version').textContent = `当前版本 ${data.version} · 原负责人 ${data.sales_owner || '未分配'} · 原小组 ${data.business_group || '未分配'}`;
}
$('ownership-order-id').addEventListener('input', invalidateLoadedOrder);
$('order-load').addEventListener('click', () => loadOrderOwnership().catch((error) => notice(error.message, true)));
$('order-defaults').addEventListener('click', () => {
  const data = managementState.loadedOrder;
  if (!data || String(data.order_id) !== $('ownership-order-id').value) { notice('请先读取当前订单。', true); return; }
  if (!data.shop_defaults) { notice('该店铺尚未配置默认归属。', true); return; }
  $('order-employee').value = data.shop_defaults.employee_id || ''; $('order-group').value = data.shop_defaults.group_id || '';
  notice('已载入店铺建议，点击保存后才会应用到订单。');
});
bindManagementForm('order-form', async () => {
  const data = managementState.loadedOrder;
  if (!data || String(data.order_id) !== $('ownership-order-id').value) throw new Error('请先读取该订单当前归属。');
  const body = {expected_version: data.version, ...changeMetadata()};
  for (const [id, key, legacyKey] of [['order-employee', 'employee_id', 'sales_owner'], ['order-group', 'group_id', 'business_group']]) {
    const selected = $(id).value;
    if (selected === '__legacy__') continue;
    const value = Number(selected) || null;
    if (value !== data[key] || (value == null && data[key] == null && data[legacyKey])) body[key] = value;
  }
  const extra = JSON.parse($('order-extra').value);
  if (!extra || Array.isArray(extra) || typeof extra !== 'object') throw new Error('附加字段必须是 JSON 对象。');
  if (JSON.stringify(extra) !== JSON.stringify(data.extra_fields)) body.extra_fields = extra;
  if (Object.keys(body).length === 3) { notice('归属未变化。'); return; }
  await api(`/api/management/orders/${data.order_id}/ownership`, managementJson('PATCH', body));
  await loadOrderOwnership(); await loadManagementOrders(); notice('订单归属已保存，并记录变更历史。');
});
async function loadManagementOrders() {
  const params = new URLSearchParams({offset: managementState.offset, limit: 50});
  if ($('order-shop-filter').value) { const [platform, shop] = JSON.parse($('order-shop-filter').value); params.set('platform', platform); params.set('shop_id', shop); }
  if ($('order-group-filter').value) params.set('group_id', $('order-group-filter').value);
  if ($('order-unassigned').checked) params.set('unassigned', true);
  const data = await api('/api/management/orders?' + params);
  managementState.orders = data.orders; managementState.total = data.total;
  managementState.selected.clear();
  table('ownership-orders', ['选择', '订单 ID', '平台 / 店铺', '销售负责人', '业务小组', '版本'], data.orders.map((order) => ['', order.order_id, `${order.platform} / ${order.shop_id}`, order.sales_owner, order.business_group, order.version]));
  $('ownership-orders').querySelectorAll('tbody tr').forEach((row, index) => {
    const order = data.orders[index]; const check = document.createElement('input'); check.type = 'checkbox'; check.className = 'ownership-check'; check.setAttribute('aria-label', '选择订单 ' + order.order_id);
    check.addEventListener('change', () => { if (check.checked) managementState.selected.set(order.order_id, order.version); else managementState.selected.delete(order.order_id); updateSelectionCount(); });
    row.firstElementChild.replaceChildren(check);
    const button = document.createElement('button'); button.className = 'button'; button.textContent = order.order_id;
    button.addEventListener('click', () => { $('ownership-order-id').value = order.order_id; loadOrderOwnership().catch((error) => notice(error.message, true)); });
    row.children[1].replaceChildren(button);
  });
  $('orders-page').textContent = `共 ${data.total} 笔 · 第 ${Math.floor(managementState.offset / 50) + 1} 页（每页 50 笔）`;
  $('orders-prev').disabled = managementState.offset === 0; $('orders-next').disabled = managementState.offset + 50 >= data.total;
  updateSelectionCount();
}
function updateSelectionCount() { $('batch-selected').textContent = `已选 ${managementState.selected.size} 笔（刷新或翻页会清空选择）`; }
['prev', 'next'].forEach((direction) => $('orders-' + direction).addEventListener('click', () => {
  managementState.offset = Math.max(0, managementState.offset + (direction === 'prev' ? -50 : 50)); loadManagementOrders().catch((error) => notice(error.message, true));
}));
$('order-search').addEventListener('click', () => { managementState.offset = 0; loadManagementOrders().catch((error) => notice(error.message, true)); });
$('orders-select-all').addEventListener('click', () => {
  const choose = managementState.selected.size !== managementState.orders.length;
  $('ownership-orders').querySelectorAll('input[type=checkbox]').forEach((check) => { check.checked = choose; check.dispatchEvent(new Event('change')); });
});
bindManagementForm('batch-form', async () => {
  if (!managementState.selected.size) throw new Error('请先选择订单。');
  const changes = {};
  for (const [id, key] of [['batch-employee', 'employee_id'], ['batch-group', 'group_id']]) {
    const value = $(id).value; if (value !== '__keep__') changes[key] = value === '__clear__' ? null : Number(value);
  }
  if (!Object.keys(changes).length) throw new Error('请选择需要修改的字段。');
  const metadata = changeMetadata();
  if (!confirm(`将修改已选 ${managementState.selected.size} 笔订单归属并记录历史，确定继续？`)) return;
  const result = await api('/api/management/orders/ownership/batch', managementJson('POST', {order_ids: [...managementState.selected.keys()], expected_versions: Object.fromEntries(managementState.selected), changes, ...metadata}));
  await loadManagementOrders(); invalidateLoadedOrder(); notice(`批量保存完成，实际变更 ${result.changed} 笔。`);
});
async function showManagementHistory(parameters, title) {
  managementState.history = {parameters, title}; managementState.historyOffset = 0;
  try { await loadManagementHistory(); $('history-title').scrollIntoView({behavior: 'smooth'}); }
  catch (error) { notice(error.message, true); }
}
async function loadManagementHistory() {
  const query = managementState.history; if (!query) return;
  const params = new URLSearchParams({...query.parameters, offset: managementState.historyOffset, limit: 50});
  const data = await api('/api/management/history?' + params);
  $('history-title').textContent = query.title + ' · 变更历史';
  const summarize = (state) => {
    const fields = {...state}; delete fields.updated_at;
    return JSON.stringify(fields, null, 2);
  };
  table('ownership-history', ['时间', '操作人', '原因', '修改前', '修改后', '批次'], data.history.map((entry) => [new Date(entry.created_at).toLocaleString('zh-CN'), entry.actor, entry.reason || '—', summarize(entry.before), summarize(entry.after), entry.batch_id]));
  $('ownership-history').querySelectorAll('tbody tr').forEach((row) => { row.children[3].className = 'history-value'; row.children[4].className = 'history-value'; });
  $('history-page').textContent = `共 ${data.total} 条 · 第 ${Math.floor(managementState.historyOffset / 50) + 1} 页`;
  $('history-prev').disabled = managementState.historyOffset === 0; $('history-next').disabled = managementState.historyOffset + 50 >= data.total;
}
['prev', 'next'].forEach((direction) => $('history-' + direction).addEventListener('click', () => {
  managementState.historyOffset = Math.max(0, managementState.historyOffset + (direction === 'prev' ? -50 : 50)); loadManagementHistory().catch((error) => notice(error.message, true));
}));
$('order-history').addEventListener('click', () => {
  if (!$('ownership-order-id').value) { notice('请填写订单 ID。', true); return; }
  showManagementHistory({entity_type: 'order', order_id: $('ownership-order-id').value}, '订单 ' + $('ownership-order-id').value);
});
$('shop-history').addEventListener('click', () => {
  try { const shop = currentShop(); showManagementHistory({entity_type: 'shop', platform: shop.platform, shop_id: shop.shop_id}, `${shop.platform} / ${shop.name}`); }
  catch (error) { notice(error.message, true); }
});
$('ownership-refresh').addEventListener('click', () => refreshManagement().catch((error) => notice(error.message, true)));
