#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
: "${CAMPAIGN_DRIVER:?campaign driver path is required}"
exec python3 "${CAMPAIGN_DRIVER}" --point
