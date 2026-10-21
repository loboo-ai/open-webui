#!/usr/bin/env bash
# loboo-webui 运维：/opt/loboo-webui/ctl <命令>
#   start | stop | restart | status   systemd 起停
#   logs [行数]                        服务日志末尾，默认 200 行；logs -f 持续跟随
#   health                             等健康接口返回 200，最多 2 分钟
#   update                             拉 origin/main：依赖或前端变了才重装、重新构建，然后重启并等健康
#   build                              只重新构建前端（npm ci + npm run build），不重启
set -euo pipefail

ROOT=${LOBOO_WEBUI_ROOT:-/opt/loboo-webui}
APP=$ROOT/app
UNIT=loboo-webui
LOG=$ROOT/logs/loboo-webui.log
HEALTH=http://172.17.0.1:18120/health
# 前端构建固定用这份 Node 22：.npmrc 开了 engine-strict，要求 Node <= 22。
NODE_BIN=$ROOT/node/bin

health() {
  for _ in $(seq 1 60); do
    if curl -fsS --max-time 3 "$HEALTH" >/dev/null 2>&1; then
      echo "healthy: $HEALTH"
      return 0
    fi
    sleep 2
  done
  echo "2 分钟内健康检查没通过，看 $LOG" >&2
  return 1
}

build() {
  cd "$APP"
  PATH=$NODE_BIN:$PATH npm ci --no-audit --no-fund --loglevel=error
  # Open WebUI 的打包在 4G 堆下会内存不足。
  PATH=$NODE_BIN:$PATH NODE_OPTIONS=--max-old-space-size=8192 npm run build
}

update() {
  cd "$APP"
  local before after changed
  before=$(git rev-parse HEAD)
  git fetch -q origin main
  git merge --ff-only -q origin/main
  after=$(git rev-parse HEAD)
  if [ "$before" = "$after" ]; then
    echo "已是最新：$(git log --oneline -1)"
    return 0
  fi
  changed=$(git diff --name-only "$before" "$after")
  echo "$before -> $after"
  if grep -q '^backend/requirements.txt$' <<<"$changed"; then
    "$ROOT/venv/bin/pip" install -q -r backend/requirements.txt
  fi
  if grep -qE '^(src/|static/|package(-lock)?\.json$|svelte\.config\.js$|vite\.config\.ts$|tailwind\.config\.js$|postcss\.config\.js$|scripts/)' <<<"$changed"; then
    build
  fi
  # 单元文件跟着代码走：变了就装上并重新加载。
  if ! cmp -s "$APP/deploy/loboo-webui/loboo-webui.service" "/etc/systemd/system/$UNIT.service"; then
    install -m 644 "$APP/deploy/loboo-webui/loboo-webui.service" "/etc/systemd/system/$UNIT.service"
    systemctl daemon-reload
  fi
  systemctl restart "$UNIT"
  health
}

case "${1:-}" in
  start | stop | restart) systemctl "$1" "$UNIT" ;;
  status) systemctl status "$UNIT" --no-pager ;;
  logs)
    if [ "${2:-}" = "-f" ]; then tail -f "$LOG"; else tail -n "${2:-200}" "$LOG"; fi
    ;;
  health) health ;;
  update) update ;;
  build) build ;;
  *)
    sed -n '2,8p' "$0"
    exit 2
    ;;
esac
