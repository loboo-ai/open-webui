#!/usr/bin/env bash
# 验收用户实际用到的路径：经编排服务给一个临时测试用户起终端，检查公网、shell 与工具、OOM 优先级、技能和 loboo-cli，
# 最后删掉它。
# 在宿主机上用 root 运行；不碰任何真实用户的容器和数据。
set -euo pipefail
cd /opt/loboo-terminals
. ./.env
api=http://127.0.0.1:18210
auth="Authorization: Bearer $TERMINALS_API_KEY"
probe_user=loboo-probe

cleanup() {
  ids=$(docker ps -aq --filter "label=openwebui.com/user-id=$probe_user")
  [ -n "$ids" ] && docker rm -f $ids >/dev/null
  rm -rf "/opt/loboo-terminals/data/terminals/$probe_user"
}
trap cleanup EXIT

cmd='printf "公网 "; curl -s -o /dev/null -m 8 -w "%{http_code}\n" https://example.com
printf "pip "; pip download -q --no-deps -d /tmp/p requests >/dev/null 2>&1 && echo ok || echo 失败
printf "/bin/sh（应为 /usr/bin/bash） "; readlink -f /bin/sh
printf "bash 语法（应为 1 2 3） "; [[ 1 == 1 ]] && echo {1..3}
rg --version | head -1
fd --version
printf "oom_score_adj（应为 500） "; cat /proc/self/oom_score_adj
printf "PATH 里的 CLI "; command -v loboo-cli || echo 找不到
printf "技能 "; ls ~/.agents/skills/loboo-cli | tr "\n" " "; echo
sleep 5 # 等入口脚本在后台更新完 CLI
echo "CLI 更新日志："; sed "s/^/  /" ~/.loboo/update.log
printf "CLI 本机 "; ~/.loboo/bin/loboo-cli --version | tr -d " \n"; echo
printf "CLI 线上 "; curl -s -m 8 https://service.loboo-ai.com/downloads/loboo-cli/VERSION; echo'
curl -s -m 200 -X POST "$api/p/loboo/execute?wait=90" -H "$auth" -H "X-User-Id: $probe_user" \
  -H 'Content-Type: application/json' --data "$(jq -n --arg c "$cmd" '{command:$c}')" \
  | jq -r '.output[].data' | tr -d '\r' | grep -v '^$'
printf '终端镜像 '
docker ps --filter "label=openwebui.com/user-id=$probe_user" --format '{{.Image}}'
printf '终端列出的技能 '
curl -s -m 60 "$api/p/loboo/skills" -H "$auth" -H "X-User-Id: $probe_user" | jq -c '[.[].id]'
# loboo-webui 的文件面板按这一行判断容器醒着没有（loboo/cloud.py terminal_state）：应为 running、policy loboo、context default
printf '实例列表里的这一行 '
curl -s -m 10 "$api/api/v1/terminals" -H "$auth" \
  | jq -c --arg u "$probe_user" '[.[] | select(.user_id == $u) | {status, policy_id, context_id, idle_seconds, idle_timeout_minutes}]'
