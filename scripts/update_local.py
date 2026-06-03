#!/usr/bin/env python3
"""Daily lotto stats updater for a 24/7 Windows machine.

Downloads the latest CSV from pais.co.il, rebuilds the stats JSON, scrapes
the current jackpot, and pushes all three files to GitHub via the Contents
API — no git installation required, only Python 3.

Configuration (in priority order):
  1. environment variable LOTTO_GITHUB_TOKEN
  2. environment variable GITHUB_TOKEN
  3. config.json next to this script: {"github_token": "ghp_..."}

The GitHub token needs `Contents: write` on the target repository.
Create one at:
  https://github.com/settings/tokens?type=beta
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = 'akunh121/random_numbers2_30_08_22'
BRANCH = 'claude/website-app-development-68IT9'
PAIS_CSV = 'https://www.pais.co.il/Lotto/lotto_resultsDownload.aspx'
PAIS_HOME = 'https://www.pais.co.il/lotto/'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')


def load_token() -> str:
    t = os.environ.get('LOTTO_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if t:
        return t.strip()
    cfg = Path(__file__).resolve().parent / 'config.json'
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding='utf-8'))['github_token'].strip()
        except (KeyError, json.JSONDecodeError) as e:
            sys.exit(f'config.json is malformed: {e}')
    sys.exit('No GitHub token found. See the docstring at the top of this file.')


TOKEN = load_token()


def http(url: str, *, method: str = 'GET', data: bytes | None = None,
         headers: dict | None = None, timeout: int = 30) -> bytes:
    req_headers = {'User-Agent': UA}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def gh(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {
        'Authorization': f'Bearer {TOKEN}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    if data:
        headers['Content-Type'] = 'application/json'
    raw = http(f'https://api.github.com{path}', method=method, data=data, headers=headers)
    return json.loads(raw) if raw else {}


def build_stats(csv_bytes: bytes) -> dict:
    text = csv_bytes.decode('cp1255', errors='replace')
    reader = csv.reader(io.StringIO(text))
    next(reader, None)
    draws = []
    for row in reader:
        try:
            draw = int(row[0])
            date = row[1]
            dt = datetime.strptime(date, '%d/%m/%Y')
            mains = sorted(int(row[i]) for i in range(2, 8))
            strong = int(row[8])
        except (ValueError, IndexError):
            continue
        if not all(1 <= m <= 37 for m in mains):
            continue
        if not (1 <= strong <= 7):
            continue
        if len(set(mains)) != 6:
            continue
        draws.append({'draw': draw, 'date': date, 'dt': dt,
                      'main': mains, 'strong': strong})

    if not draws:
        sys.exit('No valid draws found in CSV — aborting.')

    draws.sort(key=lambda d: d['dt'])
    total = len(draws)
    latest = draws[-1]

    main_count = {n: 0 for n in range(1, 38)}
    strong_count = {n: 0 for n in range(1, 8)}
    main_last: dict = {n: None for n in range(1, 38)}
    strong_last: dict = {n: None for n in range(1, 8)}

    for idx, d in enumerate(draws):
        for m in d['main']:
            main_count[m] += 1
            main_last[m] = {'draw': d['draw'], 'date': d['date'], 'order': idx}
        strong_count[d['strong']] += 1
        strong_last[d['strong']] = {'draw': d['draw'], 'date': d['date'], 'order': idx}

    def entry(n, cnt, last):
        rec = last[n]
        return {
            'count': cnt[n],
            'lastDraw': rec['draw'] if rec else None,
            'lastDate': rec['date'] if rec else None,
            'drawsSince': (total - 1 - rec['order']) if rec else None,
        }

    return {
        'totalDraws': total,
        'latestDraw': latest['draw'],
        'firstDate': draws[0]['date'],
        'lastDate': latest['date'],
        'main': {str(n): entry(n, main_count, main_last) for n in range(1, 38)},
        'strong': {str(n): entry(n, strong_count, strong_last) for n in range(1, 8)},
        'recent': [{'draw': d['draw'], 'date': d['date'],
                    'main': d['main'], 'strong': d['strong']}
                   for d in draws[-50:][::-1]],
        'totalUniqueCombos': len({(tuple(d['main']), d['strong']) for d in draws}),
    }


JACKPOT_PATTERNS = {
    'firstPrize':  r'פרס ראשון בהגרלה זו בלוטו עמד על\s*([0-9.,]+\s*(?:מיליון|אלף|₪))',
    'secondPrize': r'פרס שני בהגרלה זו בלוטו עמד על\s*([0-9.,]+\s*(?:מיליון|אלף|₪))',
    'distributed': r'פרסים שחולקו בהגרלה:\s*([0-9,]+\s*₪)',
}


def scrape_jackpot() -> dict:
    try:
        html = http(PAIS_HOME).decode('utf-8', errors='replace')
    except Exception as e:
        print(f'  jackpot fetch failed: {e}')
        return {}
    out = {}
    for key, pattern in JACKPOT_PATTERNS.items():
        m = re.search(pattern, html)
        if m:
            out[key] = re.sub(r'\s+', ' ', m.group(1)).strip()
    return out


def push_file(path: str, content: bytes, message: str) -> bool:
    sha = None
    try:
        existing = gh('GET', f'/repos/{REPO}/contents/{path}?ref={BRANCH}')
        sha = existing.get('sha')
        existing_bytes = base64.b64decode(existing.get('content', '').encode('ascii'))
        if existing_bytes.rstrip() == content.rstrip():
            print(f'  {path}: unchanged')
            return False
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    payload = {
        'message': message,
        'content': base64.b64encode(content).decode('ascii'),
        'branch': BRANCH,
    }
    if sha:
        payload['sha'] = sha
    gh('PUT', f'/repos/{REPO}/contents/{path}', payload)
    print(f'  {path}: pushed')
    return True


def main() -> int:
    print('Downloading CSV from pais.co.il ...')
    csv_bytes = http(PAIS_CSV)
    print(f'  got {len(csv_bytes):,} bytes')

    print('Building stats ...')
    stats = build_stats(csv_bytes)
    print(f'  {stats["totalDraws"]} draws · latest #{stats["latestDraw"]} on {stats["lastDate"]}')

    print('Scraping jackpot ...')
    jackpot = scrape_jackpot()
    if jackpot:
        print(f'  {jackpot}')
        stats['jackpot'] = jackpot

    print('Pushing to GitHub ...')
    tag = f'#{stats["latestDraw"]} ({stats["lastDate"]})'
    changes = 0
    if push_file('public/lotto-history.csv', csv_bytes,
                 f'auto: refresh CSV {tag}'):
        changes += 1
    stats_json = json.dumps(stats, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if push_file('public/lotto-stats.json', stats_json,
                 f'auto: rebuild stats {tag}'):
        changes += 1
    if jackpot:
        jp_json = json.dumps(jackpot, ensure_ascii=False, indent=2).encode('utf-8')
        if push_file('public/lotto-jackpot.json', jp_json,
                     f'auto: refresh jackpot {tag}'):
            changes += 1

    print(f'Done. {changes} file(s) changed.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:300]
        sys.exit(f'GitHub API {e.code}: {body}')
    except Exception as e:
        sys.exit(f'ERROR: {type(e).__name__}: {e}')
