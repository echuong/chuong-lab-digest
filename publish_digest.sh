#!/usr/bin/env bash
# Publish a digest to GitHub Pages.
#
# Single-repo layout: the pipeline code lives at the repo root and the published
# site is served from site/ (see .github/workflows/deploy.yml). Publishing is
# therefore an in-repo commit — no cross-repo push and no extra credentials, so
# this works identically on a laptop and in a Claude Code cloud session.
#
# Usage: bash publish_digest.sh [YYYY-MM-DD]    (default: today)
set -euo pipefail

cd "$(cd "$(dirname "$0")" && pwd)"

DATE="${1:-$(date +%Y-%m-%d)}"
SRC="reports/digest_${DATE}.html"

if [ ! -f "$SRC" ]; then
    echo "Error: $SRC not found — run the digest for ${DATE} first" >&2
    exit 1
fi

mkdir -p site
cp "$SRC" site/index.html
cp "$SRC" "site/digest_${DATE}.html"

git add -A site
if git diff --cached --quiet; then
    echo "No changes to publish."
    exit 0
fi

git -c user.name="${GIT_AUTHOR_NAME:-chuong-lab-digest bot}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-echuong@gmail.com}" \
    commit -m "Update digest: ${DATE}"

# Push HEAD, never the local `main` ref. A cloud session checks the repo out at
# a detached HEAD with a stale local `main`, so `git push origin main` pushes an
# old commit and is rejected; HEAD:main is correct detached *and* on a branch.
if ! git push origin HEAD:main; then
    # Remote moved while the digest was being built — replay onto it and retry.
    git fetch origin main
    git rebase origin/main ||
        { git rebase --abort
          echo "Error: site/ conflicts with origin/main — resolve manually" >&2
          exit 1; }
    git push origin HEAD:main
fi

echo "Published digest to https://echuong.github.io/chuong-lab-digest/"
