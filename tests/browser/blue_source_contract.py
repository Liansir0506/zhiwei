"""Verify source entry points and JavaScript syntax without loading services."""
import argparse
import json
import re
import subprocess
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / '.runtime/test/reports/ui-blue-p2-20261021'


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.scripts, self.styles = [], [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.append(attrs['id'])
        if tag == 'script' and attrs.get('src'):
            self.scripts.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.styles.append(attrs['href'])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    html = (ROOT / 'static/index.html').read_text(encoding='utf-8')
    before = Page((ROOT / '.runtime/test/ui-blue-p2/before/static/index.html').read_text(encoding='utf-8'))
    current = Page(html)
    checks = []
    def check(name, passed):
        checks.append({'name': name, 'pass': bool(passed)})
    check('all prior DOM IDs retained', not (set(before.ids) - set(current.ids)))
    check('no duplicate HTML IDs', not [key for key, count in Counter(current.ids).items() if count > 1])
    check('all business script entry points retained', set(before.scripts) <= set(current.scripts))
    for url in current.scripts + current.styles:
        path = ROOT / url.lstrip('/')
        check('asset exists: ' + url, path.is_file())
        if path.suffix == '.js':
            result = subprocess.run(['node', '--check', str(path)], capture_output=True, text=True)
            check('JS syntax: ' + url, result.returncode == 0)
            if result.returncode:
                print(result.stderr)
        elif path.is_file():
            check('no patch markers in CSS: ' + url, not re.search(r'^\+', path.read_text(encoding='utf-8'), re.M))
    for index, match in enumerate(re.finditer(r'<script\b[^>]*>(.*?)</script>', html, re.S)):
        if not match.group(1).strip():
            continue
        result = subprocess.run(['node', '--check', '-'], input=match.group(1), capture_output=True, text=True)
        check('inline JS syntax ' + str(index), result.returncode == 0)
    data = {'before_ids': len(before.ids), 'current_ids': len(current.ids), 'checks': checks}
    (OUT / 'source-contract.json').write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'passed': sum(row['pass'] for row in checks), 'failed': [row for row in checks if not row['pass']], 'before_ids': len(before.ids), 'current_ids': len(current.ids)}))
    raise SystemExit(0 if all(row['pass'] for row in checks) else 1)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',choices=['p2','p3'],default='p2')
    args=parser.parse_args()
    OUT=ROOT / ('.runtime/test/reports/ui-blue-'+args.phase+'-20261021')
    main()
