#!/bin/sh
# Pre-commit guard: this repo is public, so block secrets before they are committed.
# Install: cp scripts/pre-commit-secret-guard.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

staged=$(git diff --cached --name-only --diff-filter=ACM)

for f in $staged; do
  case "$f" in
    .env.example) ;;
    .env|*/.env|.env.*|*/.env.*)
      echo "BLOCKED: $f must never be committed (it holds secrets)."
      exit 1 ;;
    data/*)
      echo "BLOCKED: $f is in data/, which holds real financial records."
      exit 1 ;;
  esac
done

if git diff --cached -U0 | grep -E '^\+' | grep -Eiq '(api[_-]?key|secret|token|password)[A-Z_]*[[:space:]]*=[[:space:]]*[^[:space:]]{12,}|sk-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._-]{20,}'; then
  echo "BLOCKED: staged changes look like they contain a secret."
  echo "Review with: git diff --cached"
  exit 1
fi

exit 0
