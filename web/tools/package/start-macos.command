#!/bin/sh
# Double-click this to start MeshWX.
#
# A Terminal window opens and stays open while MeshWX is running. Closing that window, or pressing
# Control-C in it, stops MeshWX. Nothing is installed and nothing is left running afterwards.
cd "$(dirname "$0")" || exit 1
if ! command -v node >/dev/null 2>&1; then
  echo "MeshWX needs Node to hand these files to your browser."
  echo "Install it from https://nodejs.org (the green LTS button), then double-click this again."
  echo
  echo "Press Return to close this window."
  read -r _
  exit 1
fi
exec node serve.mjs --open
