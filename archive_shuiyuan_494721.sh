#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export PYTHONUTF8="${PYTHONUTF8:-1}"

TARGET_URL="https://shuiyuan.sjtu.edu.cn/t/topic/494721"
OUT_DIR="${OUT_DIR:-$HOME/shuiyuan_topic_494721_main_posts_recursive_archive}"
MAX_DEPTH="${MAX_DEPTH:-2}"
DELAY="${DELAY:-0.5}"
MAX_PAGES="${MAX_PAGES:-0}"

if [[ -z "${DISCOURSE_USER_API_KEY:-}" && -z "${COOKIES_FILE:-}" ]]; then
  printf 'Set DISCOURSE_USER_API_KEY or COOKIES_FILE before running.\n' >&2
  printf 'See README.md for the User API key flow.\n' >&2
  exit 2
fi

args=(
  "$TARGET_URL"
  --root "https://shuiyuan.sjtu.edu.cn"
  --out "$OUT_DIR"
  --max-depth "$MAX_DEPTH"
  --first-post-only
  --topic-links-only
  --delay "$DELAY"
  --max-pages "$MAX_PAGES"
)

if [[ -n "${COOKIES_FILE:-}" ]]; then
  args+=(--cookies "$COOKIES_FILE")
fi

uv run python discourse_archiver.py "${args[@]}"
