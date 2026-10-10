"""Build a self-contained, data-free UI preview for desktop file viewers."""
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(destination):
    static = ROOT / 'app' / 'static'
    html = (static / 'index.html').read_text()
    for asset in ('style.css', 'management.css', 'reports.css'):
        css = (static / asset).read_text()
        html = html.replace(f'<link rel="stylesheet" href="./{asset}">', f'<style>{css}</style>')
    for asset in ('app.js', 'management.js', 'reports.js'):
        html = html.replace(f'<script src="./{asset}" defer></script>', '')
    scripts = '<script>window.__STATIC_PREVIEW__ = true;</script>\n'
    for asset in ('app.js', 'management.js', 'reports.js'):
        # Inline source must not close its HTML script element prematurely.
        code = re.sub(r'</script', r'<\\/script', (static / asset).read_text(), flags=re.I)
        scripts += f'<script>{code}</script>\n'
    html = html.replace('</body>', scripts + '</body>')
    html = html.replace('<title>销售数据中台 · BaseLinker</title>', '<title>数据中台界面预览</title>')
    html = html.replace('id="preview-banner" class="banner" role="status" hidden',
                        'id="preview-banner" class="banner" role="status"')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html)
    print(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'preview.html')
    build(parser.parse_args().output)
