#!/usr/bin/env bash
# Print a table of observers known to CoreScope.
#
# Pulls /api/observers from the local CoreScope and formats the result.
# Use to confirm a newly-added observer has actually started publishing,
# or to spot ones that have gone silent.
#
# Usage:
#   ./scripts/list-observers.sh [scope-url]

set -euo pipefail

URL="${1:-http://localhost:8082}"

if ! curl -sf "$URL/api/stats" >/dev/null 2>&1; then
  echo "error: cannot reach CoreScope at $URL" >&2
  echo "       (pass a different URL as the first argument)" >&2
  exit 1
fi

JSON=$(curl -s "$URL/api/observers")

python3 -c "
import json, datetime, sys

data = json.loads('''$JSON''')
now = datetime.datetime.now(datetime.timezone.utc)

rows = []
for o in data.get('observers', []):
    last = o.get('last_seen') or ''
    try:
        dt = datetime.datetime.fromisoformat(last.replace('Z', '+00:00'))
        secs = int((now - dt).total_seconds())
        if secs < 90:
            ago = f'{secs}s'
        elif secs < 5400:
            ago = f'{secs // 60}m'
        elif secs < 86400 * 2:
            ago = f'{secs // 3600}h'
        else:
            ago = f'{secs // 86400}d'
    except Exception:
        ago = '?'
    rows.append({
        'iata':   o.get('iata') or '-',
        'name':   o.get('name') or '?',
        'id':     (o.get('id') or '')[:12],
        'pkts1h': o.get('packetsLastHour', 0),
        'total':  o.get('packet_count', 0),
        'ago':    ago,
    })

if not rows:
    print('(no observers known yet)')
    sys.exit(0)

rows.sort(key=lambda r: (-r['pkts1h'], -r['total']))

w_name = max(4, max(len(r['name']) for r in rows))
fmt = f'{{iata:<5}} {{name:<{w_name}}} {{id:<14}} {{pkts1h:>7}} {{total:>9}} {{ago:>6}}'
print(fmt.format(iata='IATA', name='NAME', id='ID', pkts1h='1H_PKTS', total='TOTAL', ago='AGE'))
print('-' * (5 + 1 + w_name + 1 + 14 + 1 + 7 + 1 + 9 + 1 + 6))
for r in rows:
    print(fmt.format(**r))
"
