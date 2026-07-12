#!/usr/bin/env bash
# Run mypy across every AQROS package by its installed module name.
# Passing installed package names (not paths) avoids the "found twice under
# different module names" ambiguity of the src-layout workspace.
set -euo pipefail
cd "$(dirname "$0")/.."

pkgs=()
while IFS= read -r dir; do
    pkgs+=("-p" "$(basename "$dir")")
done < <(find libs backend -maxdepth 3 -type d -name 'aqros_*' | sort)

exec uv run mypy "${pkgs[@]}"
