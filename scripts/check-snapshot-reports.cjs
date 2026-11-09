// Compare the browser's read-only calculations with independently computed API results.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = vm.createContext({window:{__DATA_SNAPSHOT__:fixture.snapshot},URL,URLSearchParams});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/snapshot-reports.js'),'utf8'),context);
function normalized(rows, identity) {
  return rows.map(row=>({identity:identity(row),quantity:row.quantity??row.total,order_count:row.order_count}))
    .sort((a,b)=>JSON.stringify(a.identity).localeCompare(JSON.stringify(b.identity)));
}
function compareTree(actual, expected, kind) {
  const key = row => row.platform!==undefined?[row.platform,row.shop_id??null]:row.node_id!==undefined?row.node_id:row.key||[row.identity_type,row.id];
  assert.deepEqual(normalized(actual,key),normalized(expected,key),kind+' tree quantities/orders');
  for(const row of actual){const other=expected.find(r=>JSON.stringify(key(r))===JSON.stringify(key(row)));compareTree(row.children,other.children,kind);}
}
for (const test of fixture.cases) {
  const actual=JSON.parse(JSON.stringify(context.snapshotSalesApi(new URL('http://snapshot.local/api/reports/sales?'+test.query))));
  const expected=test.expected;
  for(const field of ['total_units','order_count','line_count'])assert.equal(actual[field],expected[field],test.query+' '+field);
  assert.deepEqual(actual.daily,expected.daily,'daily totals and zero-filled dates');
  assert.deepEqual(normalized(actual.monthly,r=>[r.month,r.key]),normalized(expected.monthly,r=>[r.month,r.key]),'monthly quantities');
  for(const kind of ['platforms','products','teams','hierarchy'])compareTree(actual.comparisons[kind],expected.comparisons[kind],kind);
  for(const kind of ['groups','employees','shops'])assert.deepEqual(normalized(actual[kind],r=>[r.identity_type,r.id]),normalized(expected[kind],r=>[r.identity_type,r.id]),kind);
  assert.equal(actual.diagnostics.unmapped_lines,expected.diagnostics.unmapped_lines);
  const detail=JSON.parse(JSON.stringify(context.snapshotSalesApi(new URL('http://snapshot.local/api/reports/details?'+test.query+'&limit=1&offset=1'))));
  assert.equal(detail.total,test.detail.total);assert.equal(detail.total_units,test.detail.total_units);
  assert.deepEqual(detail.lines.map(r=>[r.order_id,r.position,r.quantity]),test.detail.lines.map(r=>[r.order_id,r.position,r.quantity]));
}
console.log(JSON.stringify({cases:fixture.cases.length,matchedApi:true}));
