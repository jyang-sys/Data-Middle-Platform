"""Prepare an existing private Site from an explicitly synthetic standalone snapshot."""
import argparse
import json
import re
from pathlib import Path


def prepare(snapshot_path, site_directory):
    manifest = site_directory / '.openai' / 'hosting.json'
    metadata = json.loads(manifest.read_text())
    if not metadata.get('project_id') or metadata.get('static', {}).get('directory') != 'dist':
        raise ValueError('请使用已配置 project_id 和 dist 的现有演示 Site，不创建或替换站点身份。')
    html = snapshot_path.read_text()
    match = re.search(r'window\.__DATA_SNAPSHOT__ = (.*?);</script>', html, re.S)
    snapshot = json.loads(match.group(1)) if match else None
    if not snapshot or snapshot.get('data_mode') != 'synthetic' or not snapshot.get('analysis'):
        raise ValueError('在线演示仅接受明确标记的合成数据及完整的演示分析明细。')
    html = re.sub(r'<title>.*?</title>', '<title>销售与库存中台 · 在线演示</title>', html, count=1)
    html = html.replace('<style>', '<style>body [data-admin]{display:none!important}', 1)
    html = html.replace('<button id="account-button" class="button">账号</button>', '<span class="home-pill">在线演示</span>')
    html = html.replace('本地开发版', '演示环境')
    html = re.sub(r'(?m)^[\t ]+$', '', html)
    dist = site_directory / 'dist'
    dist.mkdir(parents=True, exist_ok=True)
    (dist / 'index.html').write_text(html)
    print(dist / 'index.html')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--site-directory', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.snapshot, args.site_directory)
