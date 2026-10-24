#!/usr/bin/env bash
# 萝卜云：每次启动先把 loboo-cli 的技能和 CLI 放进用户 home，再交给官方入口 /app/entrypoint.sh。
# home 是宿主机目录挂进来的（/opt/loboo-terminals/data/terminals/<用户 id>），第一次启动时可能还属于 root，
# 官方入口稍后才 chown，所以这里用 sudo（镜像里 user 有免密 sudo）。
set -euo pipefail

skills="$HOME/.agents/skills"
bin="$HOME/.loboo/bin"

# 技能随镜像更新：只整体替换 loboo-cli 这一个目录
sudo mkdir -p "$skills"
sudo rm -rf "$skills/loboo-cli"
sudo cp -r /opt/loboo/skills/loboo-cli "$skills/loboo-cli"

# CLI 只在没有时放种子，之后由技能里的版本检查按线上版本更新
if [ ! -x "$bin/loboo-cli" ]; then
  sudo mkdir -p "$bin"
  sudo install -m 755 /opt/loboo/seed/.loboo/bin/loboo-cli "$bin/loboo-cli"
fi

sudo chown -R user:user "$HOME/.agents" "$HOME/.loboo"

exec /app/entrypoint.sh "$@"
