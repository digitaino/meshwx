#!/bin/bash
# GOES station retention. Runs hourly from goes-cleanup.timer.
#  - images (goes19/goes18/himawari):        keep IMG_DAYS days (1.7 GB/day of full-disk composites)
#  - emwin/dcs/nws/text:                     keep TEXT_DAYS days (350 MB/day; the mesh weather bot
#                                            reads back 48 h of warnings on a restart, so 3 days)
#  - thumbnail cache:                       keep 7 days
#  - emergency: if the disk is over MAX_PCT full, delete the oldest day-folders until it isn't
# Ages are exact: a file older than IMG_DAYS*24 h goes (find -mmin, not -mtime, which rounds down
# and would keep a 1-day setting for two days).
IMG_DAYS=${IMG_DAYS:-1}; TEXT_DAYS=${TEXT_DAYS:-3}; MAX_PCT=${MAX_PCT:-70}
ROOT=/home/digitaino/goes-images; THUMBS=/home/digitaino/goes/thumbs
n=0
for d in goes19 goes18 himawari; do [ -d "$ROOT/$d" ] && n=$((n + $(find "$ROOT/$d" -type f -mmin +$((IMG_DAYS*1440)) -print -delete | wc -l))); done
for d in emwin dcs nws text; do [ -d "$ROOT/$d" ] && n=$((n + $(find "$ROOT/$d" -type f -mmin +$((TEXT_DAYS*1440)) -print -delete | wc -l))); done
[ -d "$THUMBS" ] && find "$THUMBS" -type f -mtime +7 -delete
# emergency guard: oldest dated folders first (YYYY-MM-DD), across all products
while [ "$(df --output=pcent "$ROOT" | tail -1 | tr -dc 0-9)" -gt "$MAX_PCT" ]; do
  oldest=$(find "$ROOT" -mindepth 2 -maxdepth 4 -type d -regex '.*/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]$' -printf '%f %p\n' | sort | head -1 | cut -d' ' -f2-)
  [ -z "$oldest" ] && break
  echo "disk over ${MAX_PCT}%: removing $oldest"; rm -rf "$oldest"; n=$((n+1))
done
find "$ROOT" -mindepth 1 -type d -empty -delete 2>/dev/null
echo "cleanup: removed $n old files/dirs; disk $(df -h "$ROOT" | awk 'NR==2{print $5" used, "$4" free"}')"
