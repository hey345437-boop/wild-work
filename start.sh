#!/usr/bin/env bash
# 起 wild-work（多渠道聚合网关，:7866）。给 launchd 的 run 模式用。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
mkdir -p data auths
exec "$HERE/wild-work" -config "$HERE/config.json"
