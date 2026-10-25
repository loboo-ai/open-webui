#!/usr/bin/env bash
# 萝卜云：每次启动先把 loboo-cli 的技能和 CLI 放进用户 home，再交给官方入口 /app/entrypoint.sh。
# CLI 只在没有时放种子，每次启动在后台按线上版本更新，所以策略的 OPEN_TERMINAL_INFO 让模型跳过技能的版本检查。
# home 是宿主机目录挂进来的（/opt/loboo-terminals/data/terminals/<用户 id>），第一次启动时可能还属于 root，
# 官方入口稍后才 chown，所以这里用 sudo（镜像里 user 有免密 sudo）。
set -euo pipefail

# 整机内存耗尽时先杀成员进程、保住宿主服务（之后起的进程都继承这个值）。这不是隔离手段，进程自己可以改回去
{ echo 500 > /proc/self/oom_score_adj; } 2>/dev/null || true

skills="$HOME/.agents/skills"
bin="$HOME/.loboo/bin"

# 技能随镜像更新：只整体替换 loboo-cli 这一个目录
sudo mkdir -p "$skills"
sudo rm -rf "$skills/loboo-cli"
sudo cp -r /opt/loboo/skills/loboo-cli "$skills/loboo-cli"

# CLI 只在没有时放种子，每次启动在后台按线上版本更新（在 exec 之前）
if [ ! -x "$bin/loboo-cli" ]; then
  sudo mkdir -p "$bin"
  sudo install -m 755 /opt/loboo/seed/.loboo/bin/loboo-cli "$bin/loboo-cli"
fi

sudo chown -R user:user "$HOME/.agents" "$HOME/.loboo"

# CLI 由环境托管。每次启动在后台按线上版本更新（setup.sh 比对校验和，相同就不下载），不拖慢启动。
# 外层括号让后台进程脱离本 shell，exec 之后由 tini 回收
( timeout 60 bash -c 'set -o pipefail; curl -fsSL "${LOBOO_DOWNLOAD_BASE:-https://service.loboo-ai.com/downloads/loboo-cli}/setup.sh" | bash' >"$HOME/.loboo/update.log" 2>&1 & )

exec /app/entrypoint.sh "$@"
