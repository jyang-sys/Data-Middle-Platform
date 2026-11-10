"""Add settings to the existing expanded-demo Site without replacing its fixtures."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def install(site):
    if not (site/'scripts/expand-demo.py').exists():raise ValueError('需要已有的扩展演示 Site，不能覆盖其他站点源代码。')
    for name in ('shop-settings.mjs','shop-settings.html'):
        (site/'worker'/name).write_text((ROOT/'app/hosted'/name).read_text())
    target=site/'worker/base-reader.js'
    source=target.read_text()
    if "url.pathname === '/api/shop-settings'" not in source:
        source=source.replace('{ transport = fetch, cache = globalThis.caches?.default, pause } = {},','{ transport = fetch, cache = globalThis.caches?.default, pause, settingsHtml = \'\' } = {},')
        marker='      if (url.pathname === "/favicon.ico")'
        actual=(ROOT/'app/hosted/base-test-worker.mjs').read_text()
        routes=actual[actual.index("      if (url.pathname === '/settings/shops')"):actual.index('      if (\n        !["/api/base-test/snapshot"')]
        if marker not in source:raise ValueError('Base 路由源代码已变化，请核对后集成。')
        source=source.replace(marker,routes+marker,1)
        target.write_text(source)
    generator=site/'scripts/expand-demo.py'
    source=generator.read_text()
    marker="reader=(ROOT/'worker/base-reader.js').read_text()"
    if "worker/shop-settings.mjs" not in source:
        source=source.replace(marker,marker+"\nreader=(ROOT/'worker/shop-settings.mjs').read_text()+'\\nconst SETTINGS_HTML='+js((ROOT/'worker/shop-settings.html').read_text())+';\\n'+reader")
        source=source.replace('const baseHandler=createHandler(REFERENCE,INDEX_HTML);','const baseHandler=createHandler(REFERENCE,INDEX_HTML,{settingsHtml:SETTINGS_HTML});')
        source=source.replace('basehtml=html', '''# Independent persisted business settings; preserve both demo and Base modes.
if '/settings/shops' not in html:
    html=html.replace('<div class="sidebar-bottom">','<a class="nav" href="/settings/shops" style="text-decoration:none">⚙ <span>店铺归属设置</span></a><div class="sidebar-bottom">',1)
basehtml=html''',1)
        generator.write_text(source)
    manifest_path=site/'.openai/hosting.json'
    manifest=json.loads(manifest_path.read_text())
    manifest['d1']='DB'
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    directory=site/'drizzle';directory.mkdir(exist_ok=True)
    for migration in (ROOT/'app/hosted/migrations').glob('*.sql'):
        dest=directory/migration.name
        if dest.exists() and dest.read_text()!=migration.read_text():raise ValueError('不能修改已有迁移。')
        dest.write_text(migration.read_text())
    result=subprocess.run([sys.executable,str(generator)],cwd=site,capture_output=True,text=True)
    if result.returncode:raise RuntimeError('站点生成失败：'+result.stderr[-1500:])
    print(json.dumps({'settings_installed':True,'preserved_demo_and_base':True}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--site-directory',type=Path,required=True)
    install(p.parse_args().site_directory)
