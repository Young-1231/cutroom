#!/bin/zsh
# Demo driver for the README GIF — every command runs for real.
#   asciinema rec --cols 100 --rows 30 -c "zsh scripts/record_demo.sh <video_id>" demo.cast
#   agg --speed 6 --idle-time-limit 2 demo.cast docs/demo.gif
set -e
VID=${1:?usage: record_demo.sh <video_id>}
TASK='a punchy ~20s teaser: hook, build, cliffhanger'

show() {
  printf '\033[1;35m❯\033[0m \033[1m%s\033[0m\n' "$1"
  sleep 1
}

show "cutroom list"
uv run --quiet cutroom list
printf '\n'; sleep 1

show "cutroom cut $VID \"$TASK\" --plan --budget 30000"
uv run --quiet cutroom cut "$VID" "$TASK" --plan --budget 30000
printf '\n'; sleep 1

show "cutroom checkpoints $VID"
uv run --quiet cutroom checkpoints "$VID"
printf '\n'
printf '\033[1;35m❯\033[0m every cut ships with receipts — see renders/receipts.md\n'
sleep 2
