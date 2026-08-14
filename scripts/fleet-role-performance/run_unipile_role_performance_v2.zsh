#!/bin/zsh
set -euo pipefail

keychain_service="${UNIPILE_V2_KEYCHAIN_SERVICE:-com.dynamism.unipile.v2.api-key}"

if [[ -z "${UNIPILE_V2_API_KEY:-}" ]]; then
  export UNIPILE_V2_API_KEY="$(/usr/bin/security find-generic-password -s "$keychain_service" -w)"
fi

export UNIPILE_V2_BASE_URL="https://api.unipile.com"

script_dir="${0:A:h}"

exec /opt/homebrew/bin/python3.12 \
  "$script_dir/run_unipile_role_performance_report.py" \
  "$@"
