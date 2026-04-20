#!/usr/bin/env bash
# release.sh — tag + push + GitHub release for the version in version.json.
#
# Workflow:
#   1. Edit code, bump version.json, add a ## v<ver> section in CHANGELOG.md
#   2. Commit with message "v<ver>: …"
#   3. Run this script
#
# The script:
#   • reads version from version.json
#   • refuses to run if the working tree is dirty (commit first)
#   • refuses if the current HEAD commit message doesn't start with "v<ver>"
#     (guards against tagging the wrong commit after a context-switch)
#   • extracts the matching ## v<ver> section from CHANGELOG.md
#   • creates the tag, pushes commits + tag, creates a GitHub release
#   • idempotent: rerunning on an already-tagged & released version is a no-op
#
# Why this exists: before this script lived in-repo, the release flow was
# whatever-my-old-mac's-shell-aliases-did. After switching computers the
# aliases didn't follow, so commits piled up untagged and GitHub releases
# stopped firing while the portfolio site still polled /releases/latest.
# Keep the flow checked in so the next machine switch doesn't break it.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)

VER=$(/usr/bin/python3 -c 'import json; print(json.load(open("version.json"))["version"])')
TAG="v$VER"

# --- preflight ---

if [[ -n "$(git status --porcelain)" ]]; then
  echo "✗ working tree dirty — commit or stash first"
  git status --short
  exit 1
fi

HEAD_MSG=$(git log -1 --pretty=%s)
if [[ "$HEAD_MSG" != "$TAG"* && "$HEAD_MSG" != "v$VER"* ]]; then
  echo "✗ HEAD commit message doesn't start with $TAG:"
  echo "  $HEAD_MSG"
  echo "  (did you forget to commit the version bump?)"
  exit 1
fi

if ! grep -q "^## $TAG" CHANGELOG.md; then
  echo "✗ CHANGELOG.md has no '## $TAG' section"
  exit 1
fi

# --- tag ---

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "• tag $TAG already exists locally"
else
  git tag "$TAG"
  echo "✓ tagged $TAG at $(git rev-parse --short HEAD)"
fi

# --- push commits + tag ---

git push origin main
git push origin "$TAG"

# --- GitHub release ---

BODY=$(/usr/bin/python3 - "$TAG" <<'PY'
import re, sys
tag = sys.argv[1]        # e.g. v0.11.12
ver = tag.lstrip("v")
text = open("CHANGELOG.md").read()
m = re.search(rf"(^## v{re.escape(ver)}.*?)(?=^## v|\Z)", text, re.MULTILINE | re.DOTALL)
if not m:
    sys.exit(f"CHANGELOG section {tag} not found")
sys.stdout.write(m.group(1).rstrip())
PY
)

if gh release view "$TAG" >/dev/null 2>&1; then
  echo "• GitHub release $TAG already exists — skipping"
else
  printf "%s\n" "$BODY" | gh release create "$TAG" \
    --title "ShellFrame $TAG" --notes-file -
  echo "✓ released $TAG"
fi
