"""Build a self-contained UI preview, optionally a private read-only real-data snapshot."""
import argparse
import json
from datetime import datetime, timedelta
from urllib.request import urlopen
from urllib.parse import urlencode, urlsplit, parse_qsl
from zoneinfo import ZoneInfo
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def canonical(path):
    url = urlsplit(path)
    return url.path + ('?' + urlencode(sorted(parse_qsl(url.query, keep_blank_values=True))) if url.query else '')


def capture(base_url):
    responses = {}
    def get(path):
        with urlopen(base_url.rstrip('/') + path, timeout=30) as response:
            value = json.load(response)
        responses[canonical(path)] = value
        return value
    options = get('/api/reports/options')
    get('/api/management/groups')
    get('/api/management/employees')
    latest = options['order_dates']['last_confirmed_created'] or options['order_dates']['last_created']
    end = datetime.fromtimestamp(latest, ZoneInfo('Asia/Shanghai')).date() if latest else datetime.now(ZoneInfo('Asia/Shanghai')).date()
    filters = {'start': (end-timedelta(days=13)).isoformat(), 'end': end.isoformat(), 'timezone': 'Asia/Shanghai',
               'date_basis': 'base_created', 'confirmed_only': 'true', 'granularity': 'source_product'}
    query = urlencode(filters)
    report = get('/api/reports/sales?' + query)
    scopes = [{}] + [{'month': row['month'], 'key_json': json.dumps(row['key'], ensure_ascii=False, separators=(',', ':'))} for row in report['monthly']]
    for scope in scopes:
        params = {**filters, **scope, 'limit': 50, 'offset': 0}
        details = get('/api/reports/details?' + urlencode(params))
        for offset in range(50, details['total'], 50):
            get('/api/reports/details?' + urlencode({**params, 'offset': offset}))
    get('/api/reports/mappings?' + urlencode({'limit':50, 'offset':0, 'search':''}))
    stocks = get('/api/stock-dashboard/options')
    if stocks['default_catalog']:
        params = {'inventory_id': stocks['default_catalog'], 'nonzero_only':'false', 'search':'', 'offset':0, 'limit':50}
        stock = get('/api/stock-dashboard/summary?' + urlencode(params))
        for offset in range(50, stock['total_rows'], 50):
            get('/api/stock-dashboard/summary?' + urlencode({**params, 'offset': offset}))
    return {'filters': filters, 'captured_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'responses': responses}


def build(destination, live_url=None):
    static = ROOT / 'app' / 'static'
    html = (static / 'index.html').read_text()
    for asset in ('style.css', 'management.css', 'reports.css'):
        css = (static / asset).read_text()
        html = html.replace(f'<link rel="stylesheet" href="./{asset}">', f'<style>{css}</style>')
    for asset in ('app.js', 'management.js', 'reports.js'):
        html = html.replace(f'<script src="./{asset}" defer></script>', '')
    snapshot = capture(live_url) if live_url else None
    data = json.dumps(snapshot, ensure_ascii=True).replace('<', r'\u003c') if snapshot else 'null'
    scripts = '<script>window.__STATIC_PREVIEW__ = true;window.__DATA_SNAPSHOT__ = ' + data + ';</script>\n'
    for asset in ('app.js', 'management.js', 'reports.js'):
        # Inline source must not close its HTML script element prematurely.
        code = re.sub(r'</script', r'<\\/script', (static / asset).read_text(), flags=re.I)
        scripts += f'<script>{code}</script>\n'
    html = html.replace('</body>', scripts + '</body>')
    html = html.replace('<title>销售数据中台 · BaseLinker</title>', '<title>数据中台界面预览</title>')
    html = html.replace('id="preview-banner" class="banner" role="status" hidden',
                        'id="preview-banner" class="banner" role="status"')
    if snapshot:
        html = html.replace('界面预览未连接数据服务。此处可查看页面布局；真实数据查询与维护需通过已启动的中台服务打开。', '真实系统只读快照 · ' + snapshot['captured_at'] + '。显示最近有已确认订单的 14 天；筛选、同步和保存请打开运行中的中台服务。')
        html = html.replace('<title>数据中台界面预览</title>', '<title>数据中台 · 真实系统数据快照</title>')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html)
    print(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'preview.html')
    parser.add_argument('--live-url', help='从已运行中台读取无客户资料的报表快照；生成文件不得提交 Git')
    args = parser.parse_args()
    build(args.output, args.live_url)
