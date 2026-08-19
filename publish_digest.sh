#!/usr/bin/env bash
# Publish the latest digest to GitHub Pages.
#
# Single-repo layout: the pipeline code lives at the repo root and the published
# site is served from site/ (see .github/workflows/deploy.yml). Publishing is
# therefore an in-repo commit — no cross-repo push and no extra credentials, so
# this works identically on a laptop and in a Claude Code cloud session.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SITE_DIR="$SCRIPT_DIR/site"
REPORTS_DIR="$SCRIPT_DIR/reports"
DATE="${1:-$(date +%Y-%m-%d)}"

if [ ! -f "$REPORTS_DIR/latest_digest.html" ]; then
    echo "Error: reports/latest_digest.html not found — run the digest first" >&2
    exit 1
fi

cd "$SCRIPT_DIR"

# Only rebase when a remote is reachable; a cloud runner may already be current.
git pull --rebase origin main 2>/dev/null || true

mkdir -p "$SITE_DIR"
cp "$REPORTS_DIR/latest_digest.html" "$SITE_DIR/index.html"
cp "$REPORTS_DIR/latest_digest.html" "$SITE_DIR/digest_${DATE}.html"

git add -A "$SITE_DIR"
if git diff --cached --quiet; then
    echo "No changes to publish."
    exit 0
fi

git -c user.name="${GIT_AUTHOR_NAME:-chuong-lab-digest bot}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-echuong@gmail.com}" \
    commit -m "Update digest: ${DATE}"
git push origin main

echo "Published digest to https://echuong.github.io/chuong-lab-digest/"
