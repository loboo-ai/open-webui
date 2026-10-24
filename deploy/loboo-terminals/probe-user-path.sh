#!/usr/bin/env bash
# 验收用户实际用到的路径：经编排服务给一个临时测试用户起终端，检查公网、技能和 loboo-cli，最后删掉它。
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
printf "技能 "; ls ~/.agents/skills/loboo-cli | tr "\n" " "; echo
printf "CLI 本机 "; ~/.loboo/bin/loboo-cli --version | tr -d " \n"; echo
printf "CLI 线上 "; curl -s -m 8 https://service.loboo-ai.com/downloads/loboo-cli/VERSION; echo'
curl -s -m 200 -X POST "$api/p/loboo/execute?wait=90" -H "$auth" -H "X-User-Id: $probe_user" \
  -H 'Content-Type: application/json' --data "$(jq -n --arg c "$cmd" '{command:$c}')" \
  | jq -r '.output[].data' | tr -d '\r' | grep -v '^$'
printf '终端列出的技能 '
curl -s -m 60 "$api/p/loboo/skills" -H "$auth" -H "X-User-Id: $probe_user" | jq -c '[.[].id]'
