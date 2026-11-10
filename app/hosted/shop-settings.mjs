// Business configuration lives in D1, isolated by source account; never in browser storage.
export async function handleShopSettings(request, env, scope, sources, productNodes = []) {
  const json = (data, status = 200) => Response.json(data, {status, headers:{'Cache-Control':'no-store'}});
  if (!env.DB) return json({detail:'设置存储尚未就绪，请稍后重试'},503);
  if (!['GET','PUT'].includes(request.method)) return json({detail:'请求方式不支持'},405);
  const shops = [...new Map(sources.map(s=>[JSON.stringify([s.platform,String(s.shop_id)]), {...s, shop_id:String(s.shop_id)}])).values()];
  const products=productNodes.filter(p=>p.kind==='sku').map(p=>({key:p.attributes?.base_product_key?'base:'+p.attributes.base_product_key:'company:'+p.code,code:p.code,name:p.name,source_sku:p.attributes?.source_sku||'',active:Boolean(p.active)}));
  const key = s => JSON.stringify([s.platform,String(s.shop_id)]);
  try {
    if (request.method==='GET') {
      const row = await env.DB.prepare('SELECT revision,payload,updated_at FROM shop_settings WHERE scope=?').bind(scope).first();
      const config = row ? JSON.parse(row.payload) : {groups:[],assignments:[]};
      const sourceKeys = new Set(shops.map(key));
      const retired = config.assignments.filter(s=>!sourceKeys.has(key(s))).map(s=>({...s,active:false}));
      return json({...config,employees:config.employees||[],product_owners:config.product_owners||[],products,shops:[...shops.map(s=>({...s,active:true})),...retired],revision:row?.revision||0,updated_at:row?.updated_at||null});
    }
    const actor=request.headers.get('oai-authenticated-user-id');
    if (!actor) return json({detail:'请登录后维护店铺归属'},401);
    if (request.headers.get('Origin')!==new URL(request.url).origin) return json({detail:'请从当前页面保存设置'},403);
    if (Number(request.headers.get('Content-Length')||0)>512000) return json({detail:'配置内容过大'},413);
    const text=await request.text();
    if(new TextEncoder().encode(text).length>512000) return json({detail:'配置内容过大'},413);
    let body;try{body=JSON.parse(text);}catch{return json({detail:'配置格式不正确'},422);}
    if(!body||typeof body!=='object'||!Number.isSafeInteger(body.revision)||body.revision<0||!Array.isArray(body.groups)||body.groups.length>200||!Array.isArray(body.assignments)||body.assignments.length>10000)
      return json({detail:'配置格式不正确'},422);
    const groupIds=new Set(),names=new Set(),groups=[];
    for(const g of body.groups){
      const name=typeof g.name==='string'?g.name.trim():'';
      if(typeof g.id!=='string'||!/^[a-zA-Z0-9_-]{1,80}$/.test(g.id)||groupIds.has(g.id)||!name||name.length>80||names.has(name.toLocaleLowerCase()))return json({detail:'小组名称不能重复或为空，最多 80 个字符'},422);
      groupIds.add(g.id);names.add(name.toLocaleLowerCase());groups.push({id:g.id,name});
    }
    const previous=await env.DB.prepare('SELECT revision,payload FROM shop_settings WHERE scope=?').bind(scope).first();
    if((previous?.revision||0)!==body.revision)return json({detail:'配置已在其他窗口修改，请重新载入后保存'},409);
    const old=previous?JSON.parse(previous.payload):{groups:[],assignments:[]};
    // Current page supports creation/renaming, not silent deletion of groups.
    if(old.groups.some(g=>!groupIds.has(g.id)))return json({detail:'不能删除已有小组；可将店铺调整为其他小组或未分配'},422);
    const employees=body.employees===undefined?(old.employees||[]):body.employees;
    if(!Array.isArray(employees)||employees.length>2000)return json({detail:'人员配置无效，最多维护 2000 人'},422);
    const people=[],peopleIds=new Set(),codes=new Set(),previousPeople=new Map((old.employees||[]).map(p=>[p.id,p]));
    for(const p of employees){
      const code=typeof p.code==='string'?p.code.trim():'',name=typeof p.name==='string'?p.name.trim():'';
      if(typeof p.id!=='string'||!/^[a-zA-Z0-9_-]{1,80}$/.test(p.id)||peopleIds.has(p.id)||!code||code.length>40||!name||name.length>80||codes.has(code.toLowerCase())||typeof p.active!=='boolean'||!(p.group_id===null||groupIds.has(p.group_id)))return json({detail:'请检查人员编号、姓名、所属小组及状态；员工编号不能重复'},422);
      if(previousPeople.has(p.id)&&previousPeople.get(p.id).code!==code)return json({detail:'已有员工编号不能修改，请保留原编号'},422);
      peopleIds.add(p.id);codes.add(code.toLowerCase());people.push({id:p.id,code,name,group_id:p.group_id,active:p.active});
    }
    if((old.employees||[]).some(p=>!peopleIds.has(p.id)))return json({detail:'人员不能删除，请将离职人员设为停用'},422);
    const peopleById=new Map(people.map(p=>[p.id,p]));
    const allowed=new Map(shops.map(s=>[key(s),s])),oldAssignments=new Map(old.assignments.map(s=>[key(s),s])),seen=new Set(),assignments=[];
    for(const s of body.assignments){
      if(typeof s.platform!=='string'||typeof s.shop_id!=='string'||s.platform.length>100||s.shop_id.length>100)return json({detail:'来源标识不正确'},422);
      const identity=key(s),source=allowed.get(identity),oldSource=oldAssignments.get(identity);
      if(seen.has(identity)||(!source&&!oldSource)||!(s.group_id===null||groupIds.has(s.group_id)))return json({detail:'店铺或销售小组无效，请刷新来源列表'},422);
      if(!source&&s.group_id!==oldSource.group_id)return json({detail:'已停用的来源只能保留原归属'},422);
      const employee_id=s.employee_id===undefined?(oldSource?.employee_id||null):s.employee_id;
      const employee=peopleById.get(employee_id);
      if(employee_id!==null&&(!employee||!employee.active||!s.group_id||employee.group_id!==s.group_id))return json({detail:'店铺负责人必须是在职且属于该销售小组的人员；调组或停用前请清空原店铺负责人'},422);
      if(!source&&employee_id!==null&&employee_id!==(oldSource.employee_id||null))return json({detail:'已停用来源的负责人只能保留原配置'},422);
      seen.add(identity);assignments.push({platform:s.platform,shop_id:s.shop_id,name:source?.name||oldSource.name,group_id:s.group_id,employee_id});
    }
    // Full-save must retain old sources, including stopped sources, to avoid accidental loss.
    if(old.assignments.some(s=>!seen.has(key(s))))return json({detail:'店铺配置不完整，请重新载入'},422);
    const rawRules=body.product_owners===undefined?(old.product_owners||[]):body.product_owners;
    if(!Array.isArray(rawRules)||rawRules.length>10000)return json({detail:'产品负责人配置无效，最多维护 10000 条'},422);
    const ruleKey=r=>JSON.stringify([r.platform,r.shop_id,r.product_key]),productMap=new Map(products.map(p=>[p.key,p])),assignmentMap=new Map(assignments.map(a=>[key(a),a])),oldRules=new Map((old.product_owners||[]).map(r=>[ruleKey(r),r])),ruleKeys=new Set(),product_owners=[];
    for(const r of rawRules){
      if(!r||typeof r.platform!=='string'||typeof r.shop_id!=='string'||typeof r.product_key!=='string'||r.product_key.length>200||typeof r.employee_id!=='string')return json({detail:'产品负责人配置格式无效'},422);
      const identity=ruleKey(r),shopKey=key(r),assignment=assignmentMap.get(shopKey),person=peopleById.get(r.employee_id),product=productMap.get(r.product_key),previousRule=oldRules.get(identity);
      if(ruleKeys.has(identity))return json({detail:'同一店铺的同一规格只能配置一位负责人'},422);
      if(!assignment?.group_id||!person?.active||person.group_id!==assignment.group_id)return json({detail:'产品负责人必须是在职且属于店铺销售小组的人员；调组或停用时请清理不匹配的产品归属'},422);
      if((!allowed.has(shopKey)||!product?.active)&&(!previousRule||previousRule.employee_id!==r.employee_id))return json({detail:'店铺或产品已失效，请同步并重新核对；原配置可以移除'},422);
      ruleKeys.add(identity);product_owners.push({platform:r.platform,shop_id:r.shop_id,product_key:r.product_key,employee_id:r.employee_id});
    }
    const updated_at=new Date().toISOString(),payload=JSON.stringify({groups,assignments,employees:people,product_owners});
    const stmt=previous
      ?env.DB.prepare('UPDATE shop_settings SET payload=?,revision=revision+1,updated_at=?,updated_by=? WHERE scope=? AND revision=?').bind(payload,updated_at,actor,scope,body.revision)
      :env.DB.prepare('INSERT INTO shop_settings (scope,revision,payload,updated_at,updated_by) VALUES (?,1,?,?,?) ON CONFLICT(scope) DO NOTHING').bind(scope,payload,updated_at,actor);
    const result=await stmt.run();
    if(result.meta.changes!==1)return json({detail:'配置已在其他窗口修改，请重新载入后保存'},409);
    return json({revision:body.revision+1,updated_at});
  }catch{return json({detail:'设置读取或保存失败，原配置保持不变，请稍后重试'},503);}
}

// Sites Workers do not expose Cloudflare's default cache. Use the declared D1 binding.
export function createSnapshotStore(db) {
  if(!db)return null;
  const scope=key=>new URL(key.url).pathname;
  return {
    async match(key){const row=await db.prepare('SELECT payload FROM base_test_snapshots WHERE scope=?').bind(scope(key)).first();return row?Response.json(JSON.parse(row.payload)):undefined;},
    async put(key,response){const payload=await response.text();await db.prepare('INSERT INTO base_test_snapshots (scope,payload,updated_at) VALUES (?,?,?) ON CONFLICT(scope) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at').bind(scope(key),payload,new Date().toISOString()).run();}
  };
}
export async function readShopSources(db,scope){
  return db?await db.prepare('SELECT payload,updated_at FROM base_shop_sources WHERE scope=?').bind(scope).first():null;
}
export async function syncShopSources(request,env,scope,transport=fetch){
  const json=(data,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store'}});
  if(request.method!=='POST')return json({detail:'请求方式不支持'},405);
  if(!request.headers.get('oai-authenticated-user-id'))return json({detail:'请登录后同步店铺来源'},401);
  if(request.headers.get('Origin')!==new URL(request.url).origin)return json({detail:'请从当前页面同步店铺'},403);
  if(!env.DB)return json({detail:'设置存储尚未就绪'},503);
  try{
    const response=await transport('https://api.baselinker.com/connector.php',{method:'POST',headers:{'X-BLToken':env.BASELINKER_TEST_API_TOKEN,'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({method:'getOrderSources',parameters:'{}'}),signal:AbortSignal.timeout(35000)});
    if(!response.ok)throw Error('upstream');
    const result=await response.json();
    if(result.status!=='SUCCESS'||!result.sources||typeof result.sources!=='object')throw Error('upstream');
    const shops=[];
    for(const [platform,entries] of Object.entries(result.sources)){
      if(!entries||typeof entries!=='object')throw Error('invalid sources');
      for(const [id,name] of Object.entries(entries)){
        if(typeof name!=='string')throw Error('invalid shop');
        shops.push({platform,shop_id:String(id),name});
      }
    }
    const updated_at=new Date().toISOString();
    await env.DB.prepare('INSERT INTO base_shop_sources (scope,payload,updated_at) VALUES (?,?,?) ON CONFLICT(scope) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at').bind(scope,JSON.stringify(shops),updated_at).run();
    return json({count:shops.length,sources_synced_at:updated_at});
  }catch{return json({detail:'Base 店铺同步失败，请检查接口权限或稍后重试；已有归属保留'},502);}
}
