import assert from 'node:assert/strict';
import {createTestDB} from './check-shop-settings.mjs';
import {createHandler} from '../app/hosted/base-test-worker.mjs';
import {createSnapshotStore} from '../app/hosted/shop-settings.mjs';
const {DB,sqlite}=createTestDB();
const original=Object.getOwnPropertyDescriptor(globalThis,'caches');
Object.defineProperty(globalThis,'caches',{configurable:true,get(){throw Error('This Worker is not permitted to access the default cache.');}});
const snapshot={responses:{'/api/reports/options':{shops:[{platform:'personal',shop_id:'0',name:'手工来源'}]}},analysis:{lines:[]}};
let fail=false;
const transport=async(url,opts)=>{assert.equal(new URLSearchParams(opts.body).get('method'),'getOrderSources');assert.equal(opts.headers['X-BLToken'],'fixture-key');if(fail)throw Error('secret-upstream-failure');return Response.json({status:'SUCCESS',sources:{personal:{0:'手工来源'},amazon:{123:'德国店'}}});};
const handler=createHandler({snapshot},'fixture',{transport}),env={DB,BASELINKER_TEST_API_TOKEN:'fixture-key'};
const request=(path,method='GET',body)=>new Request('https://fixture.test'+path,{method,headers:{Origin:'https://fixture.test','oai-authenticated-user-id':'fixture-owner'},...(body?{body:JSON.stringify(body)}:{})});
try{
 const first=await handler.fetch(request('/api/shop-settings'),env,{});assert.equal(first.status,200);assert.match(first.headers.get('Content-Type'),/json/);const config=await first.json();assert.equal(config.shops.length,1);
 const draft={revision:0,groups:[{id:'group-a',name:'一组'}],assignments:[{platform:'personal',shop_id:'0',group_id:'group-a'}]};assert.equal((await handler.fetch(request('/api/shop-settings','PUT',draft),env,{})).status,200);
 const sync=await handler.fetch(request('/api/shop-settings/sync','POST'),env,{});assert.equal(sync.status,200);assert.equal((await sync.json()).count,2);
 const after=await (await handler.fetch(request('/api/shop-settings'),env,{})).json();assert.equal(after.shops.length,2);assert.equal(after.assignments[0].group_id,'group-a');assert.ok(after.sources_synced_at);
 fail=true;const failed=await handler.fetch(request('/api/shop-settings/sync','POST'),env,{});assert.equal(failed.status,502);assert.ok(!(await failed.text()).includes('secret-upstream'));
 const retained=await (await handler.fetch(request('/api/shop-settings'),env,{})).json();assert.equal(retained.shops.length,2);assert.equal(retained.assignments[0].group_id,'group-a');
 const key=new Request('https://fixture.test/key'),store=createSnapshotStore(DB);await store.put(key,Response.json({test:'persisted'}));assert.equal((await (await store.match(key)).json()).test,'persisted');
 const base=await handler.fetch(request('/api/base-test/snapshot'),env,{});assert.equal(base.status,200);assert.deepEqual(await base.json(),snapshot);
 console.log(JSON.stringify({deniedDefaultCacheRegression:true,jsonResponse:true,d1Snapshots:true,realSourceSync:true,ownershipPreserved:true,syncFailurePreservesData:true}));
}finally{if(original)Object.defineProperty(globalThis,'caches',original);else delete globalThis.caches;sqlite.close();}
