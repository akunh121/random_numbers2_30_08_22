#!/usr/bin/env python3
"""Best-effort scrape of the latest Mifal HaPayis lotto jackpot.

Reads the main lotto homepage, extracts the "first prize" amount and the
"distributed prizes" total, and writes them into public/lotto-jackpot.json
so the website can pick them up next time stats are built.

This is best-effort: if the page layout changes or the request fails the
script exits 0 with no output and the existing jackpot file (if any)
remains untouched.
"""
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_PATH = REPO / 'public' / 'lotto-jackpot.json'
URL = 'https://www.pais.co.il/lotto/'
UA = 'Mozilla/5.0 (compatible; lotto-stats-updater)'

AMOUNT = r'[0-9.,]+(?:\s*(?:מיליון|אלף|₪))+'
PATTERNS = {
    ('firstPrize', 'firstPrizeDouble'):
        rf'פרס ראשון בהגרלה זו בלוטו עמד על\s*({AMOUNT})\s*ועד\s*({AMOUNT})\s*בדאבל לוטו',
    ('secondPrize', 'secondPrizeDouble'):
        rf'פרס שני בהגרלה זו בלוטו עמד על\s*({AMOUNT})\s*ועד\s*({AMOUNT})\s*בדאבל לוטו',
    ('firstPrize',): r'פרס ראשון בהגרלה זו בלוטו עמד על\s*(' + AMOUNT + ')',
    ('secondPrize',): r'פרס שני בהגרלה זו בלוטו עמד על\s*(' + AMOUNT + ')',
    ('distributed',): r'פרסים שחולקו בהגרלה:\s*([0-9,]+\s*₪)',
}


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('cp1255', errors='replace')


def normalize(s):
    return re.sub(r'\s+', ' ', s).strip()


def main():
    try:
        html = fetch(URL)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f'fetch failed: {e}', file=sys.stderr)
        return 0

    data = {}
    for keys, pattern in PATTERNS.items():
        m = re.search(pattern, html)
        if not m:
            continue
        for idx, key in enumerate(keys):
            if key in data:
                continue
            data[key] = normalize(m.group(idx + 1))

    if not data:
        print('no jackpot data extracted', file=sys.stderr)
        return 0

    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'wrote {OUT_PATH}: {data}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
