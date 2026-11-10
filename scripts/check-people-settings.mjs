import assert from 'node:assert/strict';
import {createTestDB} from './check-shop-settings.mjs';
import {handleShopSettings} from '../app/hosted/shop-settings.mjs';
const {DB,sqlite}=createTestDB(),env={DB},sources=[{platform:'amazon',shop_id:'1',name:'德国店'}];
const req=(method='GET',body)=>new Request('https://fixture.test/api/shop-settings',{method,headers:{Origin:'https://fixture.test','oai-authenticated-user-id':'owner'},...(body?{body:JSON.stringify(body)}:{})});
const call=body=>handleShopSettings(req('PUT',body),env,'people-account',sources);
const read=async()=>await(await handleShopSettings(req(),env,'people-account',sources)).json();
const initial={revision:0,groups:[{id:'g1',name:'一组'},{id:'g2',name:'二组'}],assignments:[{...sources[0],group_id:'g1',employee_id:'p1'}],employees:[{id:'p1',code:'XS001',name:'张三',group_id:'g1',active:true}]};
try{
 assert.equal((await call(initial)).status,200);let data=await read();assert.equal(data.employees[0].code,'XS001');assert.equal(data.assignments[0].employee_id,'p1');
 assert.equal((await call({...initial,revision:1,employees:[...initial.employees,{id:'p2',code:'xs001',name:'李四',group_id:'g1',active:true}]})).status,422);
 assert.equal((await call({...initial,revision:1,employees:[{...initial.employees[0],group_id:'g2'}]})).status,422);
 assert.equal((await call({...initial,revision:1,employees:[{...initial.employees[0],active:false}]})).status,422);
 assert.equal((await call({...initial,revision:1,employees:[]})).status,422);
 assert.equal((await call({...initial,revision:1,employees:[{...initial.employees[0],code:'XS002'}]})).status,422);
 const moved={...initial,revision:1,employees:[{...initial.employees[0],name:'张三丰',group_id:'g2'}],assignments:[{...initial.assignments[0],employee_id:null}]};assert.equal((await call(moved)).status,200);
 data=await read();assert.equal(data.assignments[0].group_id,'g1');assert.equal(data.assignments[0].employee_id,null);assert.equal(data.employees[0].group_id,'g2');
 const oldClient={revision:2,groups:data.groups,assignments:data.assignments};assert.equal((await call(oldClient)).status,200);assert.equal((await read()).employees[0].name,'张三丰');
 assert.equal((await call({...moved,revision:2})).status,409);
 const latest=await read();assert.equal((await call({...latest,employees:latest.employees.map(p=>({...p,active:false}))})).status,200);assert.equal((await read()).employees[0].active,false);
 assert.equal((await handleShopSettings(req(),env,'other-account',sources)).status,200);
 console.log(JSON.stringify({peoplePersistence:true,uniqueCode:true,stableCode:true,groupConsistency:true,disable:true,retention:true,oldClientCompatibility:true,concurrentConfig:true}));
}finally{sqlite.close();}
