#!/usr/bin/env bash
# One-time per clone: point git at the versioned hooks in .githooks/.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
chmod +x "$root/.githooks/"*
git -C "$root" config core.hooksPath .githooks
echo "core.hooksPath set to .githooks ($(git -C "$root" config core.hooksPath))"
