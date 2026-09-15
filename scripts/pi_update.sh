#!/usr/bin/env bash
# Update the receiver Pi's bot to a commit from GitHub and restart it.
#
#   ssh digitaino@mesh-wx.digitaino.com meshcore-weather/scripts/pi_update.sh [commit]
#
# Without an argument it goes to origin/main; with one (the commit it printed last time) it
# rolls back. The checkout must be clean: a tracked file edited on the Pi stops the update
# rather than being overwritten. Files git ignores (.env, data/, .venv, CoreScope's config,
# passwords and bundles) are never touched.
set -euo pipefail
cd "$(dirname "$0")/.."
service=meshcore-weather

git fetch --quiet origin
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Tracked files were changed on the Pi; commit them upstream or discard them first:" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

current=$(git rev-parse HEAD)
target=$(git rev-parse --verify "${1:-origin/main}^{commit}")
if [ "$target" = "$current" ]; then
  echo "Already at $(git log -1 --format='%h %s')"
  exit 0
fi
echo "Updating $(git rev-parse --short "$current") -> $(git rev-parse --short "$target")"
git log --oneline "$current..$target"          # empty when rolling back

# Stopped while the files change: a running bot imports some modules lazily and must not mix
# old and new code. Whatever happens below, the service is started again on exit.
stopped=0
trap '[ "$stopped" = 1 ] && sudo systemctl start "$service"' EXIT
sudo systemctl stop "$service"
stopped=1

git checkout --quiet -B main "$target"
git branch --quiet --set-upstream-to=origin/main main
if ! git diff --quiet "$current" "$target" -- pyproject.toml; then
  .venv/bin/pip install --quiet -e ".[portal]"
fi
if ! .venv/bin/python -c "import meshcore_weather.main"; then
  echo "The new code does not import; going back to $(git rev-parse --short "$current")" >&2
  git checkout --quiet -B main "$current"
  exit 1
fi

sudo systemctl start "$service"
stopped=0
echo "$service $(systemctl is-active "$service") at $(git log -1 --format='%h %s')"
echo "Roll back with: $0 $current"
