// Business configuration lives in D1, isolated by source account; never in browser storage.
export async function handleShopSettings(request, env, scope, sources) {
  const json = (data, status = 200) => Response.json(data, {status, headers:{'Cache-Control':'no-store'}});
  if (!env.DB) return json({detail:'设置存储尚未就绪，请稍后重试'},503);
  if (!['GET','PUT'].includes(request.method)) return json({detail:'请求方式不支持'},405);
  const shops = [...new Map(sources.map(s=>[JSON.stringify([s.platform,String(s.shop_id)]), {...s, shop_id:String(s.shop_id)}])).values()];
  const key = s => JSON.stringify([s.platform,String(s.shop_id)]);
  try {
    if (request.method==='GET') {
      const row = await env.DB.prepare('SELECT revision,payload,updated_at FROM shop_settings WHERE scope=?').bind(scope).first();
      const config = row ? JSON.parse(row.payload) : {groups:[],assignments:[]};
      const sourceKeys = new Set(shops.map(key));
      const retired = config.assignments.filter(s=>!sourceKeys.has(key(s))).map(s=>({...s,active:false}));
      return json({...config,shops:[...shops.map(s=>({...s,active:true})),...retired],revision:row?.revision||0,updated_at:row?.updated_at||null});
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
    const allowed=new Map(shops.map(s=>[key(s),s])),oldAssignments=new Map(old.assignments.map(s=>[key(s),s])),seen=new Set(),assignments=[];
    for(const s of body.assignments){
      if(typeof s.platform!=='string'||typeof s.shop_id!=='string'||s.platform.length>100||s.shop_id.length>100)return json({detail:'来源标识不正确'},422);
      const identity=key(s),source=allowed.get(identity),oldSource=oldAssignments.get(identity);
      if(seen.has(identity)||(!source&&!oldSource)||!(s.group_id===null||groupIds.has(s.group_id)))return json({detail:'店铺或销售小组无效，请刷新来源列表'},422);
      if(!source&&s.group_id!==oldSource.group_id)return json({detail:'已停用的来源只能保留原归属'},422);
      seen.add(identity);assignments.push({platform:s.platform,shop_id:s.shop_id,name:source?.name||oldSource.name,group_id:s.group_id});
    }
    // Full-save must retain old sources, including stopped sources, to avoid accidental loss.
    if(old.assignments.some(s=>!seen.has(key(s))))return json({detail:'店铺配置不完整，请重新载入'},422);
    const updated_at=new Date().toISOString(),payload=JSON.stringify({groups,assignments});
    const stmt=previous
      ?env.DB.prepare('UPDATE shop_settings SET payload=?,revision=revision+1,updated_at=?,updated_by=? WHERE scope=? AND revision=?').bind(payload,updated_at,actor,scope,body.revision)
      :env.DB.prepare('INSERT INTO shop_settings (scope,revision,payload,updated_at,updated_by) VALUES (?,1,?,?,?) ON CONFLICT(scope) DO NOTHING').bind(scope,payload,updated_at,actor);
    const result=await stmt.run();
    if(result.meta.changes!==1)return json({detail:'配置已在其他窗口修改，请重新载入后保存'},409);
    return json({revision:body.revision+1,updated_at});
  }catch{return json({detail:'设置读取或保存失败，原配置保持不变，请稍后重试'},503);}
}
