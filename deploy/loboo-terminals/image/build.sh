#!/usr/bin/env bash
# 构建萝卜云终端镜像。loboo-cli 的技能取自 loboo-mono 的检出（与桌面端安装的是同一份），
# 默认 /opt/gits/loboo-mono，可用 LOBOO_MONO 指定；构建时还要能访问 service.loboo-ai.com 下载 CLI。
#   用法：build.sh loboo/open-terminal:0.14.4-r2
set -euo pipefail
cd "$(dirname "$0")"
tag="${1:?用法: build.sh <镜像标签，例如 loboo/open-terminal:0.14.4-r2>}"
mono="${LOBOO_MONO:-/opt/gits/loboo-mono}"
[ -f "$mono/skills/loboo-cli/SKILL.md" ] || { echo "找不到 $mono/skills/loboo-cli，用 LOBOO_MONO 指定 loboo-mono 检出" >&2; exit 1; }

rm -rf skills
mkdir -p skills
cp -r "$mono/skills/loboo-cli" skills/loboo-cli
trap 'rm -rf skills' EXIT
docker build -t "$tag" .
