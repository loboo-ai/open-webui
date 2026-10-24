# 萝卜云：每人一台云端 Linux

萝卜云让 LoboOS（loboo-webui，`https://m.loboo-ai.com`）里的每个成员都有一台自己的 Linux 容器。模型可以在里面跑命令、写文件，并把文件以卡片的形式交给成员预览和下载；loboo-cli 已经装好，并以成员本人的身份登录。

底层是 Open WebUI 官方的 [Open Terminal](https://github.com/open-webui/open-terminal)（MIT）和编排服务 [Terminals](https://github.com/open-webui/terminals)（Open WebUI Enterprise License：开发、测试免费，生产使用要按席位订阅）。开给全体成员是 2026-10-24 为评估做的决定。

## 组成

```
浏览器 ─ loboo-webui（宿主机进程，172.17.0.1:18120）
            │  Bearer TERMINALS_API_KEY + X-User-Id
            ▼
          Terminals 编排服务（容器，127.0.0.1:18210）── socket-proxy ── /var/run/docker.sock
            │  按用户拉起、按空闲回收
            ▼
          每人一个 Open Terminal 容器（loboo-terminals 网络 10.89.0.0/22）
            └─ /home/user ⇐ /opt/loboo-terminals/data/terminals/<Open WebUI 用户 id>
```

| 位置 | 内容 |
| --- | --- |
| `/opt/loboo-terminals/docker-compose.yml` | 编排服务和 socket-proxy，副本来自本目录 |
| `/opt/loboo-terminals/policy-loboo.json` | 终端策略：镜像、1 CPU、1 GiB、空闲 15 分钟回收、给模型的提示词；通过 API 生效 |
| `/opt/loboo-terminals/.env` | `TERMINALS_API_KEY`，权限 600，写法见 `env.example` |
| `/opt/loboo-terminals/data/terminals/<用户 id>/` | 每个人的 home，容器回收、重启、换镜像都保留 |
| `/opt/loboo-terminals/data/terminals.db` | 编排服务的 SQLite：策略、审计 |
| `/usr/local/sbin/loboo-terminals-fw.sh` | 宿主机防护脚本（本目录 `loboo-terminals-fw.sh`） |
| `/etc/systemd/system/loboo-terminals-fw.service` | 开机和 Docker 重启后重新挂防护链 |
| 镜像 `loboo/open-terminal:0.14.4-r2` | `image/build.sh` 在本机构建，不推仓库 |

`/opt/loboo-terminals` 本身权限 700。本目录（仓库 `deploy/loboo-terminals/`）是这些文件的来源，改动先在这里提交，再复制到宿主机。

版本：Terminals `ghcr.io/open-webui/terminals:0.2.5`，socket-proxy `tecnativa/docker-socket-proxy:v0.4.1`，终端镜像基于 `ghcr.io/open-webui/open-terminal:0.14.4`（0.14.2 开出站白名单时起不来，修在 0.14.3）。

## 在 LoboOS 里怎么接

都在 loboo-webui 的配置里（数据库 `config` 表），管理后台能改，迁移时要重新配：

1. **终端连接**（管理后台「设置 → 扩展功能」的 Open Terminal 一节，或 `POST /api/v1/configs/terminal_servers`）：
   ```json
   {
     "id": "loboo-terminals",
     "name": "萝卜云",
     "enabled": true,
     "url": "http://127.0.0.1:18210",
     "key": "<TERMINALS_API_KEY>",
     "auth_type": "bearer",
     "server_type": "orchestrator",
     "policy_id": "loboo",
     "config": { "access_grants": [{ "principal_type": "user", "principal_id": "*", "permission": "read" }] }
   }
   ```
   - `access_grants` 决定谁能用：现在是所有人，新账号自动能用。要收窄就建权限组，改成 `{"principal_type": "group", "principal_id": "<组 id>", "permission": "read"}`；没权限的人看不到云图标，也不会被默认选中。空列表等于只有管理员。
   - `name` 显示在云图标的提示里（「萝卜云已开启」），改名只改这里。
   - `server_type: orchestrator` 和 `policy_id` 决定每人一个容器，以及 loboo-webui 会不会把成员的 Key 放进去（只放进这种终端）。
2. **默认开启**：「设置 → 模型」的模型默认值（`models.default_metadata`）里 `"terminalId": "loboo-terminals"`。每个模型的新对话都默认选中萝卜云；单个模型可以在自己的设置里覆盖。
3. **右上角的云图标**：fork 代码 `src/lib/components/chat/Navbar.svelte`，在临时对话按钮旁边。亮着表示这条对话用萝卜云，点一下关、再点一下开，回复生成中不能切换。是否出现由 `src/lib/loboo.ts` 的 `chatCloudTerminal` 决定：所选模型共有一个默认终端、且这个人有权限。以后要加"什么条件下才给开关"，改这一个函数。云图标出现时，输入框里的终端菜单不再显示。
4. **给模型的说明**：`policy-loboo.json` 的 `OPEN_TERMINAL_INFO`，附在 Open Terminal 自带的提示词后面。写了三件事：交付文件要 `display_file`（`inline=true`）出卡片，不写 `sandbox:` 链接（模型照 ChatGPT 的习惯写这种链接，点不动）；公网全开、内网不通；loboo-cli 已登录，凭据不许输出或外发。改完 `PUT /api/v1/policies/loboo`，新提示词只对新起的容器生效。

## 网络：公网全开，内网不通

终端里跑的是会被网页内容诱导的模型，所以它可以访问整个公网，但不能碰宿主机和内网的服务。这台机器上同时跑着 Postgres、CPA、newapi、clash 代理和萝卜的各个服务。防护分两部分，**缺一不可**：

- **INPUT（访问宿主机自己监听的端口）**：靠 iptables-manager（`/etc/iptables/rules.v4`，开机由 `iptables-manager-restore.service` 恢复）的默认 DROP，以及终端网段在 172.16.0.0/12 **之外**。IPTMGR 对 172.16.0.0/12 放行了 clash（7890–7897）、8080 等端口，对 172.17.0.0/16 放行了 Postgres 和萝卜服务。终端网段要是落在 172.16.0.0/12 里，就能连上这些端口。现在终端只能连到对任意来源开放的公网端口（22、80、443、8443、65534）。
- **FORWARD（访问 Docker 发布在 172.17.0.1 等地址上的端口）**：这些端口走 DNAT 加转发，不经过 INPUT，所以由 `loboo-terminals-fw.sh` 在 DOCKER-USER 第 1 条挂独立链 `LOBOOTERM`，丢掉终端网桥发往 10/8、172.16/12、192.168/16、169.254/16（含云元数据）和 100.64/10 的连接。访问本机公网 IP 例外放行：那是 DNAT 到 openresty 的 80/443/65534，和公网上任何人看到的一样，`service.loboo-ai.com`（loboo-cli 用）就在这台机器上。脚本的约定和 `riven-forward.sh` 一样：只管自己的链，不碰 IPTMGR 和 RIVENFWD。

两点限制：
- 同一网桥内的流量不过 iptables（本机没加载 `br_netfilter`），所以终端之间、终端和编排服务之间能互连，只靠各自的随机 API Key 保护。
- 终端容器发布的端口（`PublishAllPorts`）由网络选项 `host_binding_ipv4=127.0.0.1` 限定只绑本机，公网连不进来。

## 镜像与 loboo-cli

`image/` 下是终端镜像：官方 open-terminal，加上 `fonts-noto-cjk`（Word/PowerPoint 预览在容器里转 PDF，缺字体时中文成方块）、loboo-cli 的种子和技能。

- 构建：`image/build.sh loboo/open-terminal:0.14.4-r2`。它从 loboo-mono 检出（默认 `/opt/gits/loboo-mono`，`LOBOO_MONO` 可改）拷 `skills/loboo-cli`，和桌面端装的是同一份；构建时要能访问 `service.loboo-ai.com` 下载 CLI。
- 容器每次启动时，`loboo-entrypoint.sh` 把技能放进 `~/.agents/skills/loboo-cli`（整个目录随镜像替换）。CLI 只在没有时才放进 `~/.loboo/bin/loboo-cli`（技能写死了这个路径），之后由技能里的版本检查按线上版本自己更新。Open Terminal 会把 `~/.agents/skills` 下的技能列给 loboo-webui，模型看得到，需要时再加载。

**授权**（fork 代码 `backend/open_webui/loboo/cloud.py`，从 `utils/middleware.py` 经 `loboo/hooks.py` 的 `on_terminal_tools` 调用）：

- 每次对话带上萝卜云的工具时，loboo-webui 把成员本人的 Codex Key（萝卜下发、只在 Codex 有效时有）写进他容器里的 `~/.codex/auth.json`。loboo-cli 第一次调用时用它调 `/cli/account/exchangeAccessKey` 换萝卜会话，存到 `~/.loboo/auth.json`。
- 只写进编排服务这种按人隔离的终端；同一个终端每 10 分钟（`LOBOO_KEY_CACHE_TTL_SECONDS`）最多写一次，Key 换了或 home 被清空，最多 10 分钟后补上。写入失败只记日志，不影响对话。
- 没有 Key 的人（Codex 未开通或已到期、没绑定萝卜账号、管理员 owner）不写，loboo-cli 用不了。

**已接受的风险**：
- Key 是全权限的萝卜登录凭据，同时也是 CPA 计费 Key。终端可以访问公网，模型如果被网页内容诱导，能把它发出去。
- CLI 会话也是全权限，闲置 30 天才过期，不能单独撤销，还能换回 Key。
- 萝卜管理员的会话能用 `admin/*`，而 fork 分不出谁是萝卜管理员（`/profile` 没有角色字段）。
- `exchangeAccessKey` 在萝卜文档里是为桌面端升级保留的兼容入口。

根治办法要改萝卜：在 `/account/api/openwebui/*` 加一个接口签发短期、限权的 CLI 会话，`/profile` 带上角色。

## 日常运维

```sh
cd /opt/loboo-terminals
docker compose ps                         # 编排服务和 socket-proxy
docker compose logs --since 1h terminals  # Provisioning / Reaping 记录
docker ps --filter label=app.kubernetes.io/managed-by=terminals \
  --format '{{.Names}} {{.Status}} {{.Label "openwebui.com/user-id"}}'   # 当前的终端容器
/usr/local/sbin/loboo-terminals-fw.sh status   # 防护链与命中计数
du -sh data/terminals/*                   # 每个人的 home 大小
```

- **编排服务一停，所有开着萝卜云的对话都会报错**（"Terminal unavailable" / "Cannot connect to host 127.0.0.1:18210"），成员点右上角的云图标关掉就能继续。重启、换镜像都挑人少的时候，几秒到十几秒。
- 容器在用到时才拉起：发出一条开着萝卜云的消息、打开文件面板、输入 `/`。冷启动约 3.5 秒，热请求约 20 毫秒，空闲容器约 65 MiB。空闲 15 分钟停掉并删除容器，home 保留。
- 改策略：编辑 `policy-loboo.json`，然后
  ```sh
  . ./.env
  curl -s -X PUT http://127.0.0.1:18210/api/v1/policies/loboo -H "Authorization: Bearer $TERMINALS_API_KEY" \
    -H 'Content-Type: application/json' --data @policy-loboo.json
  # 让空闲的容器下次按新策略起；only_idle 为 false 会打断正在用的人
  curl -s -X POST http://127.0.0.1:18210/api/v1/terminals/refresh -H "Authorization: Bearer $TERMINALS_API_KEY" \
    -H 'Content-Type: application/json' -d '{"policy_id":"loboo","only_idle":true}'
  ```
- 换镜像：`image/build.sh` 打新标签，改 `docker-compose.yml`（`TERMINALS_IMAGE`、`TERMINALS_ALLOWED_IMAGES`）和 `policy-loboo.json` 的镜像名，`docker compose up -d`，再按上面应用策略并 refresh。旧标签留着方便回退。
- 一个人的数据：删用户时编排服务不会清他的 home，要手动删 `data/terminals/<用户 id>`（先 `docker rm -f` 他的容器）。没有容量上限，也没有备份。
- 有一个 `system` 用户的容器，是 loboo-webui 拉终端提示词时建的，同样会被空闲回收。
- 防护链在开机时由 `loboo-terminals-fw.service` 挂上；它是 `PartOf=docker.service`，`systemctl restart docker` 时会跟着重新挂。这一点还没实测过：重启 Docker 后先看 `loboo-terminals-fw.sh status`，没挂上就手动执行 `loboo-terminals-fw.sh apply`。
- loboo-webui 写 Key 时调用编排服务，超时 10 秒（`AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA`）。编排服务卡住时，开着萝卜云的消息最多多等这么久；写入失败不会让对话失败。

## 验证

两个脚本都在宿主机上用 root 运行，不碰真实用户的数据：

- `probe-network.sh`：在终端网络里起一个不带过滤的临时容器，测公网、`service.loboo-ai.com`，以及宿主机监听的每个端口、Docker 发布的每个端口和云元数据。只有本来就对公网开放的端口算预期开放（取自 iptables-manager 对任意来源放行的规则和发布在 0.0.0.0 的端口），其余能连上的标为"意外可达"。新机器上还没装 iptables-manager 时，能连上的端口全会被标出来，这正说明 INPUT 还没锁住，不是脚本出错。被挡住的目标每个要等 3 秒超时，跑完要几分钟。2026-10-24 的结果：公网和 `service.loboo-ai.com` 都是 200；能连上的只有 22、80、443、8443、65534；clash、8080、9090–9097、18xxx 的萝卜服务、Postgres 5432、CPA、newapi、旧 open-webui、云元数据全部不通。
- `probe-user-path.sh`：经编排服务给临时用户 `loboo-probe` 起终端，检查公网、pip、技能、CLI 本机版本与线上版本，以及终端列出的技能，最后删掉这个用户。

端到端还要在 LoboOS 里看一眼：
- 新对话右上角的云图标亮着。
- 让它写个网页并说“帮我保存成文件 让我下载”，回复里应该出现带下载按钮的文件卡片。
- 说“用 loboo-cli 看看我的待办”，应该返回本人的数据。

## 迁移到新机器

按顺序做，每步都可以重复执行：

1. **先有 INPUT 默认 DROP。** 恢复 iptables-manager 的 `/etc/iptables/rules.v4` 和 `iptables-manager-restore.service`（或者等价的 INPUT 默认 DROP，只放行对公网开放的端口）。没有这一步，终端能连到宿主机监听的所有端口。
2. 装 Docker；准备 loboo-mono 检出（`/opt/gits/loboo-mono`，构建镜像要用）。
3. 建网络。网段要在 172.16.0.0/12 之外，也不能和新机器上已有的路由冲突（先看 `ip -4 route`）：
   ```sh
   docker network create --driver bridge --subnet 10.89.0.0/22 --gateway 10.89.0.1 \
     -o com.docker.network.bridge.name=br-loboo-term \
     -o com.docker.network.bridge.host_binding_ipv4=127.0.0.1 --label loboo.purpose=terminals loboo-terminals
   ```
4. 装防护：复制 `loboo-terminals-fw.sh` 到 `/usr/local/sbin/`（755），`loboo-terminals-fw.service` 到 `/etc/systemd/system/`，`systemctl daemon-reload && systemctl enable --now loboo-terminals-fw.service`。脚本默认取出口网卡上的公网 IP。如果新机器在 VPC 里、网卡上只有内网地址，就在服务里加 `Environment=LOBOO_TERM_PUBLIC_IPS=<公网 IP>`，或者确认 `service.loboo-ai.com` 不在本机。
5. 准备 `/opt/loboo-terminals`（权限 700）：复制 `docker-compose.yml`、`policy-loboo.json`；`.env` 沿用旧机器的值，这样 loboo-webui 里的连接 key 不用改。
6. 迁数据：先停旧机器的编排服务，再 `rsync -a` 旧机器的 `/opt/loboo-terminals/data/` 过来（home 属主是 uid 1000，要保留；`terminals.db` 一起带上）。
7. 构建镜像：`deploy/loboo-terminals/image/build.sh loboo/open-terminal:0.14.4-r2`。
8. `cd /opt/loboo-terminals && docker compose pull && docker compose up -d`，再按「日常运维」应用策略。
9. loboo-webui 一侧：如果 loboo-webui 也迁到了新机器并且编排服务仍在本机，连接地址 `http://127.0.0.1:18210` 不用改；否则按「在 LoboOS 里怎么接」改连接 `url`。模型默认值 `terminalId` 跟着 loboo-webui 的数据库走。
10. 跑 `probe-network.sh` 和 `probe-user-path.sh`，再做一遍端到端。
