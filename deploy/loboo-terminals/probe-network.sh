#!/usr/bin/env bash
# 验收萝卜云终端网络：公网要通，宿主机和内网只剩本来就对公网开放的端口。在宿主机上用 root 运行。
#
# 做法：在 loboo-terminals 网络里起一个不带任何过滤的临时容器（与终端容器同网络、同镜像），
# 逐个连宿主机监听的 TCP 端口、Docker 发布的端口和云元数据地址。
# "预期开放"是本来就对公网开放的：iptables-manager 里对任意来源放行的端口，以及发布在 0.0.0.0 的端口。
# 其余能连上的都标成"意外可达"，说明 INPUT 或 LOBOOTERM 漏了。
set -euo pipefail

net=loboo-terminals
image="${1:-$(sed -n 's/^ *TERMINALS_IMAGE: *//p' /opt/loboo-terminals/docker-compose.yml)}"
gateway="$(docker network inspect "$net" --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}')"
wan="$(ip -4 -o route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
public="$(ip -4 -o addr show dev "$wan" scope global | awk '{split($4,a,"/"); print a[1]; exit}')"

# 预期开放：IPTMGR 里注释为 iptmgr:tcp:<端口>:any 的规则，加上发布在 0.0.0.0 的端口
expected=" $(iptables -S IPTMGR 2>/dev/null | sed -n 's/.*iptmgr:tcp:\([0-9]*\):any.*/\1/p' | tr '\n' ' ')"
expected+="$(docker ps --format '{{.Ports}}' | grep -o '0\.0\.0\.0:[0-9]*' | cut -d: -f2 | tr '\n' ' ')"

targets=()
# 宿主机监听的端口（127.* 本来就连不到）：0.0.0.0/:: 的用网关和公网 IP 各测一次
while read -r addr; do
  host="${addr%:*}"; port="${addr##*:}"
  case "$host" in
    127.*|'[::1]') ;;
    0.0.0.0|'*'|'[::]') targets+=("$gateway:$port" "$public:$port") ;;
    \[*) ;;
    *) targets+=("$host:$port") ;;
  esac
done < <(ss -H -tln | awk '{print $4}' | sort -u)
# Docker 发布在非 127 地址上的端口（走 DNAT，不经过 INPUT）
while read -r hp; do targets+=("$hp"); done < <(docker ps --format '{{.Ports}}' | grep -oE '([0-9]+\.){3}[0-9]+:[0-9]+->' | grep -v '^127\.' | sed 's/->//' | sed "s/^0\.0\.0\.0:/$public:/" | sort -u)
targets+=("169.254.169.254:80" "169.254.0.23:80")
mapfile -t targets < <(printf '%s\n' "${targets[@]}" | sort -u)

echo "镜像 $image，网关 $gateway，公网 IP $public；预期开放端口：$expected"
docker run --rm --network "$net" --entrypoint bash -e EXPECTED="$expected" "$image" -c '
  printf "公网 https://example.com -> "; curl -s -o /dev/null -m 8 -w "%{http_code}\n" https://example.com || echo 失败
  printf "service.loboo-ai.com（loboo-cli）-> "; curl -s -o /dev/null -m 8 -w "%{http_code}\n" https://service.loboo-ai.com/downloads/loboo-cli/VERSION || echo 失败
  bad=0
  for t in "$@"; do
    h="${t%:*}"; p="${t##*:}"
    if timeout 3 bash -c "</dev/tcp/$h/$p" 2>/dev/null; then
      case "$EXPECTED " in *" $p "*) echo "开放（预期） $t" ;; *) echo "⚠️ 意外可达  $t"; bad=1 ;; esac
    else
      echo "不通        $t"
    fi
  done
  [ "$bad" = 0 ] && echo "结果：通过" || echo "结果：有意外可达的端口"
' _ "${targets[@]}" 2>&1 | grep -v "unable to resolve"
