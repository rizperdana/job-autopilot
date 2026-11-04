#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
HITS=0

ALLOWLIST="$ROOT/.pii-allowlist"
ALLOWED_PATHS=()
if [ -f "$ALLOWLIST" ]; then
  while IFS= read -r line; do
    line="${line%%#*}"
    line="$(echo "$line" | xargs)"
    [ -n "$line" ] && ALLOWED_PATHS+=("$line")
  done < "$ALLOWLIST"
fi

is_allowed() {
  local f="$1"
  for a in "${ALLOWED_PATHS[@]}"; do
    [ "$f" = "$a" ] && return 0
  done
  case "$f" in
    */__pycache__/*|*.pyc) return 0 ;;
    *pii_audit.sh) return 0 ;;
  esac
  return 1
}

HARD='(\+62|8960|Rizki Perdana|GOCSPX|ya29\.|1//0|ghp_|github_pat_|sk-|/home/[a-zA-Z0-9_]+/)'
EMAIL='[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'

scan_file() {
  local f="$1"
  [ -f "$f" ] || return 0
  is_allowed "$f" && return 0
  if grep -nE "$HARD" "$f" >/dev/null 2>&1; then
    echo "HIT (hard) in $f"; grep -nE "$HARD" "$f" | head; HITS=1
  fi
  if grep -noE "$EMAIL" "$f" 2>/dev/null | grep -vE "example\.(com|org)|candidate@example" >/dev/null 2>&1; then
    echo "HIT (email) in $f"; grep -noE "$EMAIL" "$f" 2>/dev/null | grep -vE "example\.(com|org)|candidate@example" | head; HITS=1
  fi
}

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "git repo detected — scanning staged tree + history"
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    scan_file "$f"
  done < <(git diff --cached --name-only)
  if git log -p HEAD 2>/dev/null | grep -nE "$HARD" >/dev/null 2>&1; then
    echo "HIT (hard) in git history"; HITS=1
  fi
else
  echo "no git repo — scanned working tree only"
  while IFS= read -r f; do
    scan_file "$f"
  done < <(find . -type f -not -path './.git/*')
fi

while IFS= read -r f; do
  scan_file "$f"
done < <(find . -type f -not -path './.git/*' 2>/dev/null)

if [ "$HITS" -ne 0 ]; then
  exit 1
fi
exit 0
