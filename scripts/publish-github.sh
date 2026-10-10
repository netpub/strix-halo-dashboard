#!/bin/sh
# Publish GitLab main to the GitHub mirror as a single squashed commit.
# Feature branches are refused. The first push is "Initial GitHub release".
# Later pushes fold every GitLab commit since the previous sync into one commit.
#
#   git remote add github git@github.com:ORG/strix-halo-dashboard.git
#   scripts/publish-github.sh                 # first publish, from main
#   scripts/publish-github.sh -m "English summary"
#
# In GitLab CI, set GITHUB_REPOSITORY and GITHUB_TOKEN. The manual job on main
# is the only pipeline that runs this script.

set -eu

remote=github
message=
dry_run=0

usage() {
  cat <<'EOF'
Usage: scripts/publish-github.sh [-m "English summary"] [--dry-run] [--remote NAME]

Publish origin/main to the GitHub mirror. Run it on main after a merge.
The first publish creates "Initial GitHub release". A later publish squashes
everything since that sync into one commit and requires -m.

The GitHub commit records the GitLab SHA in a GitLab-Commit trailer.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -m)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        echo "-m needs a non-empty message." >&2
        exit 2
      fi
      message=$2
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --remote)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        echo "--remote needs a name." >&2
        exit 2
      fi
      remote=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cd "$(git rev-parse --show-toplevel)"

if [ -n "${CI_COMMIT_BRANCH:-}" ]; then
  branch=$CI_COMMIT_BRANCH
else
  branch=$(git rev-parse --abbrev-ref HEAD)
fi
if [ "$branch" != "main" ]; then
  echo "Refusing to publish branch '$branch'." >&2
  echo "Run this on main after a merge. Feature-branch commits are not mirrored." >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree is not clean. Commit and push to GitLab main before publishing." >&2
  exit 1
fi

if ! git config user.name >/dev/null || ! git config user.email >/dev/null; then
  echo "Set git user.name and user.email before publishing." >&2
  exit 1
fi

if ! git remote get-url "$remote" >/dev/null 2>&1; then
  if [ -n "${GITHUB_REPOSITORY:-}" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
    git remote add "$remote" "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
  else
    echo "Remote '$remote' is missing." >&2
    echo "git remote add github git@github.com:ORG/strix-halo-dashboard.git" >&2
    echo "Or set GITHUB_REPOSITORY and GITHUB_TOKEN." >&2
    exit 1
  fi
fi

export GIT_TERMINAL_PROMPT=0
git fetch origin main

head_sha=$(git rev-parse HEAD)
origin_sha=$(git rev-parse origin/main)
if [ "$head_sha" != "$origin_sha" ]; then
  echo "HEAD is not origin/main." >&2
  echo "Push main to GitLab and fast-forward this checkout before publishing." >&2
  exit 1
fi

set +e
git ls-remote --exit-code "$remote" refs/heads/main >/dev/null 2>&1
ls_status=$?
set -e
if [ "$ls_status" -eq 0 ]; then
  has_main=1
elif [ "$ls_status" -eq 2 ]; then
  has_main=0
else
  echo "Cannot reach remote '$remote'." >&2
  exit 1
fi

if [ "$has_main" -eq 0 ]; then
  if [ -n "$message" ]; then
    echo "The first GitHub commit is fixed: Initial GitHub release." >&2
    echo "Run again without -m for that first publish." >&2
    exit 1
  fi
  subject="Initial GitHub release"
else
  git fetch "$remote" main
  synced=$(git log -1 --format=%B "$remote/main" | sed -n 's/^GitLab-Commit: //p' | tail -n 1)
  if [ -z "$synced" ]; then
    echo "GitHub main has no GitLab-Commit trailer. Refusing to guess a sync point." >&2
    exit 1
  fi
  if [ "$synced" = "$origin_sha" ]; then
    echo "GitHub already matches origin/main ($origin_sha)."
    exit 0
  fi
  if ! git cat-file -e "$synced^{commit}" 2>/dev/null; then
    echo "Synced commit $synced is not in this clone." >&2
    exit 1
  fi
  if ! git merge-base --is-ancestor "$synced" origin/main; then
    echo "$synced is not an ancestor of origin/main. Refusing to publish rewritten history." >&2
    exit 1
  fi
  if [ -z "$message" ]; then
    echo "Pass -m \"English summary\" for the squashed GitHub commit." >&2
    echo "GitLab commits since the last sync:" >&2
    git log --oneline "$synced..origin/main" >&2
    exit 1
  fi
  case "$message" in
    *GitLab-Commit:*)
      echo "The message must not contain a GitLab-Commit trailer." >&2
      exit 1
      ;;
  esac
  subject=$message
fi

printf 'Publishing %s\n' "$subject"
if [ "$has_main" -eq 1 ]; then
  git log --oneline "$synced..origin/main"
fi

if [ "$dry_run" -eq 1 ]; then
  echo "Dry run: not pushing."
  exit 0
fi

tree=$(git rev-parse "origin/main^{tree}")
body=$(printf '%s\n\nGitLab-Commit: %s\n' "$subject" "$origin_sha")
if [ "$has_main" -eq 0 ]; then
  new=$(printf '%s' "$body" | git commit-tree "$tree")
else
  parent=$(git rev-parse "$remote/main")
  new=$(printf '%s' "$body" | git commit-tree "$tree" -p "$parent")
fi

git push "$remote" "$new:refs/heads/main"
echo "Pushed $new to $remote main (GitLab $origin_sha)."
