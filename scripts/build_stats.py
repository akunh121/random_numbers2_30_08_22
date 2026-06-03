#!/usr/bin/env python3
"""Process Lotto history CSV into a compact JSON stats file.

Only draws matching the current format (6 main in 1-37, strong in 1-7) are
kept. Outputs frequency, last appearance, and recent draws.
"""
import csv
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CSV_PATH = REPO / 'public' / 'lotto-history.csv'
OUT_PATH = REPO / 'public' / 'lotto-stats.json'

MAIN_MAX = 37
STRONG_MAX = 7
RECENT_KEEP = 50


def parse_row(row):
    try:
        draw = int(row[0])
        date = row[1]
        dt = datetime.strptime(date, '%d/%m/%Y')
        mains = sorted(int(row[i]) for i in range(2, 8))
        strong = int(row[8])
    except (ValueError, IndexError):
        return None
    if not all(1 <= m <= MAIN_MAX for m in mains):
        return None
    if not (1 <= strong <= STRONG_MAX):
        return None
    if len(set(mains)) != 6:
        return None
    return {'draw': draw, 'date': date, 'dt': dt, 'main': mains, 'strong': strong}


def main():
    with CSV_PATH.open('r', encoding='cp1255', errors='replace') as f:
        reader = csv.reader(f)
        next(reader, None)
        draws = [d for r in reader if (d := parse_row(r))]

    draws.sort(key=lambda d: d['dt'])
    latest_draw_num = draws[-1]['draw']
    latest_dt = draws[-1]['dt']
    for idx, d in enumerate(draws):
        d['order'] = idx

    main_count = {n: 0 for n in range(1, MAIN_MAX + 1)}
    strong_count = {n: 0 for n in range(1, STRONG_MAX + 1)}
    main_last = {n: None for n in range(1, MAIN_MAX + 1)}
    strong_last = {n: None for n in range(1, STRONG_MAX + 1)}
    combos = {}

    total = len(draws)
    for d in draws:
        for m in d['main']:
            main_count[m] += 1
            main_last[m] = {'draw': d['draw'], 'date': d['date'], 'order': d['order']}
        strong_count[d['strong']] += 1
        strong_last[d['strong']] = {'draw': d['draw'], 'date': d['date'], 'order': d['order']}
        key = ','.join(str(x) for x in d['main']) + '|' + str(d['strong'])
        combos[key] = combos.get(key, 0) + 1

    def num_entry(n, count_map, last_map):
        last = last_map[n]
        return {
            'count': count_map[n],
            'lastDraw': last['draw'] if last else None,
            'lastDate': last['date'] if last else None,
            'drawsSince': (total - 1 - last['order']) if last else None,
        }

    main_stats = {str(n): num_entry(n, main_count, main_last) for n in range(1, MAIN_MAX + 1)}
    strong_stats = {str(n): num_entry(n, strong_count, strong_last) for n in range(1, STRONG_MAX + 1)}

    recent = [
        {'draw': d['draw'], 'date': d['date'], 'main': d['main'], 'strong': d['strong']}
        for d in draws[-RECENT_KEEP:][::-1]
    ]

    out = {
        'totalDraws': len(draws),
        'latestDraw': latest_draw_num,
        'firstDate': draws[0]['date'],
        'lastDate': draws[-1]['date'],
        'main': main_stats,
        'strong': strong_stats,
        'recent': recent,
        'totalUniqueCombos': len(combos),
    }

    jackpot_path = REPO / 'public' / 'lotto-jackpot.json'
    if jackpot_path.exists():
        try:
            out['jackpot'] = json.loads(jackpot_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass

    with OUT_PATH.open('w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

    size = OUT_PATH.stat().st_size
    print(f'Processed {len(draws)} draws (latest #{latest_draw_num} on {draws[-1]["date"]})')
    print(f'Output: {OUT_PATH} ({size:,} bytes)')


if __name__ == '__main__':
    main()
