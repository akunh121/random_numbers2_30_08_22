#!/usr/bin/env python3
"""Self-scheduling lotto stats updater for a 24/7 Windows machine.

Default mode is a daemon: the script stays alive and runs the update by
itself every Tuesday, Thursday and Saturday at 23:55 local time
(roughly two hours after each Israeli Lotto draw). Pass --once for a
single immediate run, useful for testing or for Task Scheduler.

The work itself: downloads the latest CSV from pais.co.il, rebuilds
the stats JSON, scrapes the current jackpot, and pushes all three
files to GitHub via the Contents API — no git installation required.

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
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# === Hard-coded schedule (local time on the host machine) ============
# Python weekday(): Monday=0, Tuesday=1, Wednesday=2, Thursday=3,
# Friday=4, Saturday=5, Sunday=6.
SCHEDULE_DAYS = {1, 3, 5}      # Tuesday, Thursday, Saturday
SCHEDULE_HOUR = 23
SCHEDULE_MIN = 55
# After a run, sleep at least this long so the same minute window does
# not trigger us twice.
POST_RUN_SLEEP = 180           # seconds
# When the next slot is far away, wake up periodically to recompute it
# (handles DST, sleep/resume, clock changes, etc.).
MAX_SLEEP = 1800               # seconds (30 minutes)
DAY_NAMES_HE = {0: 'שני', 1: 'שלישי', 2: 'רביעי', 3: 'חמישי',
                4: 'שישי', 5: 'שבת', 6: 'ראשון'}
# ====================================================================

REPO = 'akunh121/random_numbers2_30_08_22'
BRANCH = 'claude/website-app-development-68IT9'
PAIS_CSV = 'https://www.pais.co.il/Lotto/lotto_resultsDownload.aspx'
PAIS_HOME = 'https://www.pais.co.il/lotto/'
SELF_URL = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/scripts/update_local.py'
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


JACKPOT_AMOUNT = r'[0-9.,]+(?:\s*(?:מיליון|אלף|₪))+'
JACKPOT_PATTERNS = [
    (('firstPrize', 'firstPrizeDouble'),
     rf'פרס ראשון בהגרלה זו בלוטו עמד על\s*({JACKPOT_AMOUNT})\s*ועד\s*({JACKPOT_AMOUNT})\s*בדאבל לוטו'),
    (('secondPrize', 'secondPrizeDouble'),
     rf'פרס שני בהגרלה זו בלוטו עמד על\s*({JACKPOT_AMOUNT})\s*ועד\s*({JACKPOT_AMOUNT})\s*בדאבל לוטו'),
    (('firstPrize',),
     rf'פרס ראשון בהגרלה זו בלוטו עמד על\s*({JACKPOT_AMOUNT})'),
    (('secondPrize',),
     rf'פרס שני בהגרלה זו בלוטו עמד על\s*({JACKPOT_AMOUNT})'),
    (('distributed',),
     r'פרסים שחולקו בהגרלה:\s*([0-9,]+\s*₪)'),
]


def scrape_jackpot() -> dict:
    try:
        html = http(PAIS_HOME).decode('utf-8', errors='replace')
    except Exception as e:
        print(f'  jackpot fetch failed: {e}')
        return {}
    out: dict = {}
    for keys, pattern in JACKPOT_PATTERNS:
        m = re.search(pattern, html)
        if not m:
            continue
        for idx, key in enumerate(keys):
            if key in out:
                continue
            out[key] = re.sub(r'\s+', ' ', m.group(idx + 1)).strip()
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


_self_updated = False


def self_update() -> bool:
    """Pull the latest version of this script from GitHub. Returns True if
    the file on disk was replaced — caller should exit so the bat wrapper
    relaunches us with the new code."""
    cfg = Path(__file__).resolve().parent / 'config.json'
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding='utf-8'))
            if data.get('self_update', True) is False:
                return False
        except (json.JSONDecodeError, OSError):
            pass

    self_path = Path(__file__).resolve()
    try:
        latest = http(SELF_URL)
    except Exception as e:
        print(f'  self-update check failed: {e}')
        return False
    if not latest or len(latest) < 1000:
        return False

    current = self_path.read_bytes()
    if latest == current:
        return False

    try:
        compile(latest, str(self_path), 'exec')
    except SyntaxError as e:
        print(f'  [self-update] new version has syntax error, keeping current: {e}')
        return False

    backup = self_path.with_suffix('.py.bak')
    backup.write_bytes(current)
    self_path.write_bytes(latest)
    print(f'  [self-update] replaced {self_path.name} (backup: {backup.name})')
    global _self_updated
    _self_updated = True
    return True


def do_update() -> int:
    if self_update():
        print('Exiting so the launcher can restart with the new code.')
        return 0

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


def next_scheduled(now: datetime) -> datetime:
    """Return the next datetime matching the hard-coded schedule (>= now+1s)."""
    today = now.replace(hour=SCHEDULE_HOUR, minute=SCHEDULE_MIN,
                        second=0, microsecond=0)
    if now.weekday() in SCHEDULE_DAYS and today > now:
        return today
    for ahead in range(1, 8):
        candidate = (now + timedelta(days=ahead)).replace(
            hour=SCHEDULE_HOUR, minute=SCHEDULE_MIN,
            second=0, microsecond=0)
        if candidate.weekday() in SCHEDULE_DAYS:
            return candidate
    raise RuntimeError('No valid scheduled day in the next week (impossible).')


def run_once_safe() -> None:
    try:
        do_update()
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:300]
        print(f'GitHub API {e.code}: {body}')
    except urllib.error.URLError as e:
        print(f'Network error: {e}')
    except Exception:
        print('Update crashed:')
        traceback.print_exc()


def daemon() -> int:
    days_he = ' / '.join(DAY_NAMES_HE[d] for d in sorted(SCHEDULE_DAYS))
    print(f'Daemon started. Schedule (local time): {days_he} at '
          f'{SCHEDULE_HOUR:02d}:{SCHEDULE_MIN:02d}.')
    # Pick up new versions on every (re)start — fast no-op if already current.
    if self_update():
        print('Self-updated on startup. Exiting so the .bat relaunches.')
        return 0
    while True:
        try:
            now = datetime.now()
            target = next_scheduled(now)
            wait = (target - now).total_seconds()
            if wait <= 0:
                print(f'\n=== {now:%Y-%m-%d %H:%M:%S} — running scheduled update ===')
                run_once_safe()
                if _self_updated:
                    print('Self-updated. Exiting so the .bat wrapper relaunches with the new code.')
                    return 0
                time.sleep(POST_RUN_SLEEP)
                continue
            sleep_for = min(wait, MAX_SLEEP)
            print(f'Next run: {target:%a %Y-%m-%d %H:%M} '
                  f'(in {int(wait)}s; sleeping {int(sleep_for)}s)',
                  flush=True)
            time.sleep(sleep_for)
        except KeyboardInterrupt:
            print('\nStopped by user.')
            return 0
        except Exception:
            print('Scheduler loop crashed:')
            traceback.print_exc()
            time.sleep(60)


if __name__ == '__main__':
    args = set(sys.argv[1:])
    if args & {'--once', '-1', 'once'}:
        try:
            sys.exit(do_update())
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')[:300]
            sys.exit(f'GitHub API {e.code}: {body}')
        except Exception as e:
            sys.exit(f'ERROR: {type(e).__name__}: {e}')
    elif args & {'--help', '-h'}:
        print(__doc__)
        sys.exit(0)
    else:
        sys.exit(daemon())
