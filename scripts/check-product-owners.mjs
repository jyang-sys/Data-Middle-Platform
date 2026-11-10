import assert from 'node:assert/strict';
import {createTestDB} from './check-shop-settings.mjs';
import {handleShopSettings} from '../app/hosted/shop-settings.mjs';
const {DB,sqlite}=createTestDB(),env={DB};
const shops=[{platform:'amazon',shop_id:'1',name:'德国店'},{platform:'ebay',shop_id:'1',name:'另一店'}];
const products=[{id:1,code:'C51-BL-128',name:'C51 蓝色 128G',kind:'sku',active:1,attributes:{base_product_key:'10:100'}},{id:2,code:'C51-BL-512',name:'C51 蓝色 512G',kind:'sku',active:1,attributes:{base_product_key:'10:200'}}];
const req=(method='GET',body)=>new Request('https://fixture.test/api/shop-settings',{method,headers:{Origin:'https://fixture.test','oai-authenticated-user-id':'owner'},...(body?{body:JSON.stringify(body)}:{})});
const call=(body,nodes=products)=>handleShopSettings(req('PUT',body),env,'products',shops,nodes);
const read=async(nodes=products)=>await(await handleShopSettings(req(),env,'products',shops,nodes)).json();
const initial={revision:0,groups:[{id:'g1',name:'一组'},{id:'g2',name:'二组'}],employees:[{id:'p1',code:'XS1',name:'张三',group_id:'g1',active:true},{id:'p2',code:'XS2',name:'李四',group_id:'g1',active:true},{id:'p3',code:'XS3',name:'王五',group_id:'g2',active:true}],assignments:[{platform:'amazon',shop_id:'1',group_id:'g1',employee_id:'p1'},{platform:'ebay',shop_id:'1',group_id:'g1',employee_id:null}],product_owners:[{platform:'amazon',shop_id:'1',product_key:'base:10:100',employee_id:'p2'}]};
try{
 assert.equal((await call(initial)).status,200);let data=await read();assert.equal(data.product_owners[0].employee_id,'p2');assert.equal(data.assignments[0].employee_id,'p1');
 assert.equal((await call({...initial,revision:1,product_owners:[initial.product_owners[0],{...initial.product_owners[0],employee_id:'p1'}]})).status,422);
 assert.equal((await call({...initial,revision:1,product_owners:[{...initial.product_owners[0],employee_id:'p3'}]})).status,422);
 assert.equal((await call({...initial,revision:1,product_owners:[{...initial.product_owners[0],product_key:'company:unknown'}]})).status,422);
 assert.equal((await call({...initial,revision:1,employees:initial.employees.map(p=>p.id==='p2'?{...p,active:false}:p)})).status,422);
 // Old settings clients must preserve product rules rather than silently dropping them.
 const oldClient={...initial,revision:1};delete oldClient.product_owners;assert.equal((await call(oldClient)).status,200);assert.equal((await read()).product_owners.length,1);
 // Product identity uses the explicit Base reference, not list order, node ID, or name.
 const reordered=[{...products[1],id:99},{...products[0],id:101,name:'改名产品'}];data=await read(reordered);assert.equal(data.products.find(p=>p.code==='C51-BL-128').key,'base:10:100');assert.equal(data.product_owners[0].employee_id,'p2');
 const moved={...initial,revision:2,employees:initial.employees.map(p=>p.id==='p2'?{...p,group_id:'g2'}:p),product_owners:[]};assert.equal((await call(moved)).status,200);data=await read();assert.equal(data.product_owners.length,0);assert.equal(data.assignments[0].employee_id,'p1');
 const dual={...data,product_owners:[{platform:'amazon',shop_id:'1',product_key:'base:10:100',employee_id:'p1'},{platform:'ebay',shop_id:'1',product_key:'base:10:100',employee_id:'p1'}]};assert.equal((await call(dual)).status,200);assert.equal((await read()).product_owners.length,2);
 assert.equal((await call({...dual,revision:3})).status,409);
 console.log(JSON.stringify({skuSpecificOwners:true,shopIsolation:true,uniqueRule:true,sameGroupOnly:true,unknownProductRejected:true,oldClientPreservesRules:true,stableProductIdentity:true,staffMoveCleanup:true,defaultPreserved:true,versionConflict:true}));
}finally{sqlite.close();}
