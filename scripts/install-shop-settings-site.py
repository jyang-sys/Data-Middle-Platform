"""Add settings to the existing expanded-demo Site without replacing its fixtures."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent

def install(site):
    if not (site/'scripts/expand-demo.py').exists():raise ValueError('需要已有的扩展演示 Site，不能覆盖其他站点源代码。')
    for name in ('shop-settings.mjs','shop-settings.html','people-settings.html','product-owners.html'):
        (site/'worker'/name).write_text((ROOT/'app/hosted'/name).read_text())
    target=site/'worker/base-reader.js'
    original=target.read_text()
    actual=(ROOT/'app/hosted/base-test-worker.mjs').read_text()
    # Replace only the request-handler tail; preserve the Site's Base ingestion implementation.
    marker='let refreshInFlight = null;'
    if marker not in original or marker not in actual:raise ValueError('Base 路由结构已变化，请核对后集成。')
    target.write_text(original[:original.index(marker)]+actual[actual.index(marker):])
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
    source=generator.read_text()
    if 'const PEOPLE_HTML=' not in source:
        source=source.replace("js((ROOT/'worker/shop-settings.html').read_text())+';\\n'+reader", "js((ROOT/'worker/shop-settings.html').read_text())+';\\nconst PEOPLE_HTML='+js((ROOT/'worker/people-settings.html').read_text())+';\\n'+reader")
        source=source.replace('{settingsHtml:SETTINGS_HTML}', '{settingsHtml:SETTINGS_HTML,peopleHtml:PEOPLE_HTML}')
        source=source.replace('basehtml=html', "if '/settings/people' not in html:\n    html=html.replace('<div class=\"sidebar-bottom\">','<a class=\"nav\" href=\"/settings/people\" style=\"text-decoration:none\">♧ <span>销售人员管理</span></a><div class=\"sidebar-bottom\">',1)\nbasehtml=html",1)
        generator.write_text(source)
    source=generator.read_text()
    if 'const PRODUCT_OWNERS_HTML=' not in source:
        source=source.replace("js((ROOT/'worker/people-settings.html').read_text())+';\\n'+reader", "js((ROOT/'worker/people-settings.html').read_text())+';\\nconst PRODUCT_OWNERS_HTML='+js((ROOT/'worker/product-owners.html').read_text())+';\\n'+reader")
        source=source.replace('peopleHtml:PEOPLE_HTML}', 'peopleHtml:PEOPLE_HTML,productOwnersHtml:PRODUCT_OWNERS_HTML}')
        source=source.replace('basehtml=html', "if '/settings/product-owners' not in html:\n    html=html.replace('<div class=\"sidebar-bottom\">','<a class=\"nav\" href=\"/settings/product-owners\" style=\"text-decoration:none\">▦ <span>产品负责人</span></a><div class=\"sidebar-bottom\">',1)\nbasehtml=html",1)
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
