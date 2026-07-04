#!/usr/bin/env python3
"""HKJC Odds-Snapshot-Logger — läuft in GitHub Actions (oder lokal).

Loggt an HK-Renntagen WIN- und PLA-Quoten aller Rennen in append-only
JSONL-Dateien (data/YYYY-MM-DD.jsonl) plus einmalig Meta-Daten je Renntag
(data/YYYY-MM-DD_meta.json: Startzeiten, Läufer, gearInfo, Ratings, Draw).

Design:
  - Nur Python-Stdlib (kein pip install im Runner nötig)
  - Whitelisted GraphQL-Queries (HKJC lehnt abweichende Query-Formen ab —
    WHITELIST_ERROR). Queries stammen 1:1 aus github.com/Bobosky2005/hkjc-api.
  - Kein Renntag / nichts Anstehendes → Exit 0 nach Sekunden
  - Adaptive Taktung: 180s Standard, 60s wenn ein Rennen <15 Min vor Start
  - Commit+Push alle ~15 Min (GIT_PUSH=1), damit ein Runner-Abbruch
    höchstens 15 Min Daten kostet
  - Harte Laufzeit-Grenze 5.4h (GitHub-Actions-Job-Limit ist 6h)

Env:
  GIT_PUSH=1   → nach jedem Flush committen+pushen (im Workflow gesetzt)
  MAX_HOURS    → Laufzeit-Deckel (Default 5.4)
  DATE         → Datum überschreiben (Tests), sonst heute in HK-Zeit
"""
import gzip
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENDPOINT = 'https://info.cld.hkjc.com/graphql/base/'
ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
HKT = timezone(timedelta(hours=8))

POLL_NORMAL = 180   # Sekunden
POLL_HOT = 60       # wenn ein Rennen <15 Min vor Start (Syndikat-Fenster)
FLUSH_EVERY = 900   # Commit-Intervall in Sekunden
MAX_HOURS = float(os.environ.get('MAX_HOURS', '5.4'))

# ── Whitelisted Queries (1:1 aus Bobosky2005/hkjc-api, nicht verändern!) ────
ODDS_QUERY = """query racing($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) {
  raceMeetings(date: $date, venueCode: $venueCode) {
    pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) {
      id
      status
      sellStatus
      oddsType
      lastUpdateTime
      guarantee
      minTicketCost
      name_en
      name_ch
      leg {
        number
        races
      }
      cWinSelections {
        composite
        name_ch
        name_en
        starters
      }
      oddsNodes {
        combString
        oddsValue
        hotFavourite
        oddsDropValue
        bankerOdds {
          combString
          oddsValue
        }
      }
    }
  }
}"""

META_QUERY_FILE = ROOT / 'meta_query.graphql'   # volle horseQuery (3 kB)


def gql(query: str, variables: dict, op_name: str) -> dict:
    payload = json.dumps({'operationName': op_name, 'query': query,
                          'variables': variables}).encode()
    req = urllib.request.Request(
        ENDPOINT, data=payload,
        headers={'Content-Type': 'application/json',
                 'Accept-Encoding': 'gzip',
                 'User-Agent': 'Mozilla/5.0 (odds-logger)'})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get('Content-Encoding') == 'gzip' or raw[:2] == b'\x1f\x8b':
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    out = json.loads(raw)
    if out.get('errors'):
        raise RuntimeError(f'GraphQL: {out["errors"][:1]}')
    return out['data']


def fetch_meta(date_str: str) -> list[dict]:
    """Meetings + Rennen + Läufer (inkl. gearInfo). Nur HK-Venues (ST/HV)."""
    q = META_QUERY_FILE.read_text()
    data = gql(q, {'date': date_str}, 'raceMeetings')
    meetings = data.get('raceMeetings') or []
    return [m for m in meetings if m.get('venueCode') in ('ST', 'HV')
            and m.get('date') == date_str]


def fetch_odds(date_str: str, venue: str) -> list[dict]:
    data = gql(ODDS_QUERY, {'date': date_str, 'venueCode': venue,
                            'oddsTypes': ['WIN', 'PLA']}, 'racing')
    meetings = data.get('raceMeetings') or []
    return meetings[0].get('pmPools', []) if meetings else []


def git(*args) -> int:
    return subprocess.call(['git', '-C', str(ROOT), *args])


def flush(push: bool):
    if not push:
        return
    git('add', 'data')
    if subprocess.call(['git', '-C', str(ROOT), 'diff', '--cached', '--quiet']) == 0:
        return  # nichts Neues
    git('commit', '-m', f'log: {datetime.now(timezone.utc).isoformat(timespec="seconds")}')
    # pull --rebase gegen parallele Läufe, Push mit einem Retry
    git('pull', '--rebase', '--autostash')
    if git('push') != 0:
        git('pull', '--rebase', '--autostash')
        git('push')


def parse_post(t: str) -> datetime:
    return datetime.fromisoformat(t)


def main():
    push = os.environ.get('GIT_PUSH') == '1'
    date_str = os.environ.get('DATE') or datetime.now(HKT).strftime('%Y-%m-%d')
    deadline = time.time() + MAX_HOURS * 3600

    meetings = fetch_meta(date_str)
    if not meetings:
        print(f'{date_str}: kein HK-Meeting — Exit.')
        return

    DATA.mkdir(exist_ok=True)
    for m in meetings:
        meta_path = DATA / f'{date_str}_{m["venueCode"]}_meta.json'
        if not meta_path.exists():
            meta_path.write_text(json.dumps(m, ensure_ascii=False, indent=1))
            print(f'Meta geschrieben: {meta_path.name}')

    venue = meetings[0]['venueCode']
    races = meetings[0].get('races') or []
    posts = sorted(parse_post(r['postTime']) for r in races if r.get('postTime'))
    if not posts:
        print('Keine Startzeiten — Exit.')
        return
    now = datetime.now(timezone.utc)
    first, last = posts[0], posts[-1]
    if now < first - timedelta(minutes=70):
        print(f'Erstes Rennen erst {first} — Exit (späterer Trigger übernimmt).')
        flush(push)
        return
    if now > last + timedelta(minutes=10):
        print(f'Letztes Rennen war {last} — Exit.')
        flush(push)
        return

    out_path = DATA / f'{date_str}.jsonl'
    print(f'Logge {venue} {date_str}: {len(posts)} Rennen, bis {last}')
    last_flush = time.time()
    while time.time() < deadline:
        now = datetime.now(timezone.utc)
        if now > last + timedelta(minutes=10):
            print('Alle Rennen vorbei — fertig.')
            break
        try:
            pools = fetch_odds(date_str, venue)
            ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
            lines = []
            for p in pools:
                rno = (p.get('leg') or {}).get('races', [None])[0]
                for node in p.get('oddsNodes') or []:
                    lines.append(json.dumps({
                        't': ts, 'v': venue, 'r': rno,
                        'pool': p.get('oddsType'),
                        'h': node.get('combString'),
                        'o': node.get('oddsValue'),
                        's': p.get('sellStatus'),
                    }, ensure_ascii=False))
            if lines:
                with open(out_path, 'a', encoding='utf-8') as f:
                    f.write('\n'.join(lines) + '\n')
        except Exception as e:
            print(f'Poll-Fehler (weiter): {e}', flush=True)

        if time.time() - last_flush >= FLUSH_EVERY:
            flush(push)
            last_flush = time.time()

        upcoming = [p for p in posts if p > now]
        hot = upcoming and (upcoming[0] - now) < timedelta(minutes=15)
        time.sleep(POLL_HOT if hot else POLL_NORMAL)

    flush(push)
    print('Logger beendet.')


if __name__ == '__main__':
    main()
