"""Package the private read-only Base test Site with a server-side refresh adapter."""
import argparse
import json
import os
import re
import sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def build(snapshot_path,db_path,site_directory):
    if (site_directory/'scripts/expand-demo.py').exists():raise ValueError('该站点已有扩展演示，请使用 install-shop-settings-site.py 保留现有两种数据模式。')
    manifest_path=site_directory/'.openai/hosting.json'
    manifest=json.loads(manifest_path.read_text())
    if not manifest.get('project_id'):raise ValueError('请使用现有私有 Site，不创建或替换站点身份。')
    html=snapshot_path.read_text()
    match=re.search(r'window\.__DATA_SNAPSHOT__ = (.*?);</script>',html,re.S)
    snapshot=json.loads(match.group(1)) if match else None
    if not snapshot or snapshot.get('data_mode')!='base_test' or not snapshot.get('analysis'):raise ValueError('仅接受独立 Base 测试数据库快照。')
    reference={'snapshot':snapshot,'annotations':{},'line_owners':{}}
    with sqlite3.connect(db_path) as db:
        db.row_factory=sqlite3.Row
        mode=db.execute("SELECT value FROM app_metadata WHERE key='data_mode'").fetchone()
        if not mode or mode['value']!='base_test':raise ValueError('来源数据库必须标记为 base_test。')
        for row in db.execute('SELECT * FROM order_annotations'):
            extra=json.loads(row['extra_json'])
            reference['annotations'][str(row['order_id'])]={**{k:row[k] for k in ('employee_id','group_id','sales_owner','business_group')},**{k:extra[k] for k in ('exclude_from_sales','ownership_locked') if k in extra}}
        for row in db.execute('''SELECT a.*,i.order_product_id,i.storage,i.storage_id,i.product_id,i.variant_id,i.sku
            FROM sales_line_ownership a JOIN base_order_items i ON i.order_id=a.order_id AND i.position=a.position'''):
            from hashlib import sha256
            identity=[str(row[k] or '') for k in ('order_product_id','storage','storage_id','product_id','variant_id','sku')]
            if sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()!=row['fingerprint']:continue
            reference['line_owners'][str(row['order_id'])+':'+str(row['position'])]={**{k:row[k] for k in ('employee_id','group_id','sales_owner','business_group','rule_id','rule_version')},'manual':row['rule_id'] is None,'identity':identity}
    html=re.sub(r'<title>.*?</title>','<title>销售与库存中台 · Base 测试环境</title>',html,count=1)
    html=html.replace('<style>','<style>body [data-admin]{display:none!important}',1)
    html=html.replace('<button id="account-button" class="button">账号</button>','<span class="home-pill">Base 测试环境</span>').replace('本地开发版','测试环境')
    html=html.replace('<div class="sidebar-bottom">','<a class="nav" href="/settings/shops" style="text-decoration:none">⚙ <span>店铺归属设置</span></a><div class="sidebar-bottom">',1)
    bootstrap=(ROOT/'app/static/base-test-bootstrap.js').read_text()
    marker=';</script>\n<script>'
    position=html.index(marker,html.index('window.__DATA_SNAPSHOT__'))
    html=html[:position]+html[position:].replace(marker,';</script>\n<script>'+bootstrap+'</script>\n<script>',1)
    html=re.sub(r'(?m)^[\t ]+$','',html)
    source=(ROOT/'app/hosted/base-test-worker.mjs').read_text().replace("import { handleShopSettings, createSnapshotStore, readShopSources, syncShopSources } from './shop-settings.mjs';",'')
    settings=(ROOT/'app/hosted/shop-settings.mjs').read_text()
    settings_html=(ROOT/'app/hosted/shop-settings.html').read_text()
    people_html=(ROOT/'app/hosted/people-settings.html').read_text()
    worker='const REFERENCE = '+json.dumps(reference,ensure_ascii=True).replace('<',r'\u003c')+';\nconst INDEX_HTML = '+json.dumps(html,ensure_ascii=True).replace('<',r'\u003c')+';\nconst SETTINGS_HTML = '+json.dumps(settings_html,ensure_ascii=True).replace('<',r'\u003c')+';\nconst PEOPLE_HTML = '+json.dumps(people_html,ensure_ascii=True).replace('<',r'\u003c')+';\n'+settings+'\n'+source+'\nexport default createHandler(REFERENCE, INDEX_HTML, {settingsHtml:SETTINGS_HTML,peopleHtml:PEOPLE_HTML});\n'
    # Runtime values are intentionally read from env, never from this package.
    config_path=db_path.parent/'runtime.json'
    if config_path.exists():
        token=json.loads(config_path.read_text()).get('BASELINKER_API_TOKEN','')
        if token and token in worker:raise ValueError('检测到密钥进入部署文件，停止打包。')
    (site_directory/'worker').mkdir(exist_ok=True)
    (site_directory/'worker/index.js').write_text(worker)
    (site_directory/'dist/server').mkdir(parents=True,exist_ok=True)
    (site_directory/'dist/server/index.js').write_text(worker)
    manifest.pop('static',None)
    manifest['d1']='DB'
    migrations=site_directory/'drizzle'
    migrations.mkdir(exist_ok=True)
    for migration in (ROOT/'app/hosted/migrations').glob('*.sql'):
        target=migrations/migration.name
        if target.exists() and target.read_text()!=migration.read_text():raise ValueError('不能修改已发布的迁移文件。')
        target.write_text(migration.read_text())
    temporary=manifest_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest,indent=2)+'\n');os.replace(temporary,manifest_path)
    print(json.dumps({'project_id':manifest['project_id'],'worker_bytes':len(worker.encode()),'secret_embedded':False}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--site-directory',type=Path,required=True)
    args=parser.parse_args();build(args.snapshot,args.db,args.site_directory)
