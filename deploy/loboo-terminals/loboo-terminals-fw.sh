#!/usr/bin/env bash
#
# loboo-terminals-fw.sh — 萝卜云终端网络的宿主机防护
#
#   萝卜云的终端容器（Docker 网络 loboo-terminals，网桥 br-loboo-term）可以自由访问公网，
#   但不能访问宿主机和内网里的服务：
#
#     · INPUT 侧（访问宿主机自己监听的端口）：本脚本不碰。
#       终端网络的网段放在 172.16.0.0/12 之外（见 README），iptables-manager 的 IPTMGR 链里
#       按 172.16.0.0/12、172.17.0.0/16 放行的端口（clash、8080、Postgres、loboo 服务等）对它不生效；
#       INPUT 默认 DROP，终端只能连到 IPTMGR 对任意来源开放的那几个公网端口。
#
#     · FORWARD 侧（本脚本负责）：Docker 发布在 172.17.0.1 等地址上的端口走 DNAT + FORWARD，
#       不经过 INPUT。所以在 DOCKER-USER 里挂独立链 LOBOOTERM，丢掉终端网络发往私有网段、
#       CGNAT 网段和云元数据地址的连接。访问本机公网 IP 例外放行：那是 DNAT 到 openresty 的
#       80/443（service.loboo-ai.com 就是本机，loboo-cli 要用），与公网任何人看到的一样。
#
#   与 riven-forward.sh 的约定一致：只管自己的链；挂在 DOCKER-USER 第 1 条
#   （Docker 保证 DOCKER-USER 先于它自己的规则执行，并且永远不清空）；没有 Docker 时挂 FORWARD。
#   同网桥内的流量（终端 ↔ 编排服务）不经过 iptables（本机没加载 br_netfilter），不受影响。
#
#   动作：
#     apply   应用规则（幂等，可重复执行）
#     remove  移除本脚本创建的一切
#     status  查看当前状态
#
#   可选环境变量：
#     LOBOO_TERM_BRIDGE      终端网桥名，默认 br-loboo-term
#     LOBOO_TERM_PUBLIC_IPS  本机公网 IPv4（空格分隔）；默认取出口网卡上的全局地址
#
set -euo pipefail

BRIDGE="${LOBOO_TERM_BRIDGE:-br-loboo-term}"
CHAIN="LOBOOTERM"
BLOCKED_NETS=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10)

c_g(){ echo -e "\033[32m$*\033[0m"; }
c_y(){ echo -e "\033[33m$*\033[0m"; }
c_r(){ echo -e "\033[31m$*\033[0m"; }
die(){ c_r "[错误] $*" >&2; exit 1; }

public_ips() {
  if [[ -n "${LOBOO_TERM_PUBLIC_IPS:-}" ]]; then
    echo "$LOBOO_TERM_PUBLIC_IPS"
    return
  fi
  local wan
  wan="$(ip -4 -o route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
  [[ -n "$wan" ]] || die "无法探测出口网卡，请设置 LOBOO_TERM_PUBLIC_IPS"
  ip -4 -o addr show dev "$wan" scope global | awk '{split($4, a, "/"); print a[1]}'
}

# 挂载点：有 Docker 用 DOCKER-USER，没有就用 FORWARD（与 riven-forward.sh 相同）
hook_parent() {
  if iptables -n -L DOCKER-USER >/dev/null 2>&1; then echo "DOCKER-USER"; else echo "FORWARD"; fi
}

unhook() {
  local p
  for p in DOCKER-USER FORWARD; do
    if iptables -n -L "$p" >/dev/null 2>&1; then
      while iptables -C "$p" -j "$CHAIN" >/dev/null 2>&1; do
        iptables -D "$p" -j "$CHAIN"
      done
    fi
  done
}

do_apply() {
  [[ $EUID -eq 0 ]] || die "请用 root 运行"
  command -v iptables >/dev/null || die "未找到 iptables"
  local parent ips ip net
  parent="$(hook_parent)"
  ips="$(public_ips)"

  iptables -n -L "$CHAIN" >/dev/null 2>&1 || iptables -N "$CHAIN"
  iptables -F "$CHAIN"

  # 只处理从终端网桥进来的包，其余原样交回父链
  iptables -A "$CHAIN" ! -i "$BRIDGE" -j RETURN
  iptables -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED \
           -m comment --comment "lobooterm:established" -j RETURN
  # FORWARD 看到的是 DNAT 之后的地址，所以按原始目标判断"访问的是本机公网 IP"
  for ip in $ips; do
    iptables -A "$CHAIN" -m conntrack --ctorigdst "$ip" \
             -m comment --comment "lobooterm:public-ip" -j RETURN
  done
  for net in "${BLOCKED_NETS[@]}"; do
    iptables -A "$CHAIN" -d "$net" -m comment --comment "lobooterm:block" -j DROP
  done

  unhook
  iptables -I "$parent" 1 -j "$CHAIN"

  c_g "  ✓ iptables 链 ${CHAIN} 已挂到 ${parent} 第 1 条（网桥 ${BRIDGE}，公网 IP 例外：$(echo $ips)）"
  [[ "$parent" == "FORWARD" ]] && c_y "     （未检测到 Docker；Docker 起来后重跑一次 apply）"
  return 0
}

do_remove() {
  [[ $EUID -eq 0 ]] || die "请用 root 运行"
  unhook
  if iptables -n -L "$CHAIN" >/dev/null 2>&1; then
    iptables -F "$CHAIN"
    iptables -X "$CHAIN"
    c_g "  - iptables 链 ${CHAIN} 已移除"
  fi
  return 0
}

do_status() {
  [[ $EUID -eq 0 ]] || die "请用 root 运行"
  if iptables -n -L "$CHAIN" >/dev/null 2>&1; then
    iptables -n -L "$CHAIN" -v --line-numbers | sed 's/^/  /'
    echo "  挂载点: $(iptables -C DOCKER-USER -j "$CHAIN" 2>/dev/null && echo DOCKER-USER || (iptables -C FORWARD -j "$CHAIN" 2>/dev/null && echo FORWARD || echo '未挂载 ⚠️'))"
  else
    echo "  (链 ${CHAIN} 不存在)"
  fi
}

case "${1:-}" in
  apply)  do_apply ;;
  remove) do_remove ;;
  status) do_status ;;
  *) echo "用法: $0 {apply|remove|status}"; exit 1 ;;
esac
