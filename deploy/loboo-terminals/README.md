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
| 镜像 `loboo/open-terminal:0.14.4-r3` | `image/build.sh` 在本机构建，不推仓库；上一版 r2 留着回滚 |

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
     "config": {
       "access_grants": [{ "principal_type": "user", "principal_id": "*", "permission": "read" }],
       "working_directory_context": false
     }
   }
   ```
   - `access_grants` 决定谁能用：现在是所有人，新账号自动能用。要收窄就建权限组，改成 `{"principal_type": "group", "principal_id": "<组 id>", "permission": "read"}`；没权限的人看不到云图标，也不会被默认选中。空列表等于只有管理员。
   - `working_directory_context: false`：不在每条消息前问容器当前目录（管理后台编辑连接时的 "Working Directory Context" 开关，中文界面没有翻译）。问一次就会把休眠的容器拉起来；`run_command` 的返回里本来就带当前目录。
   - `name` 显示在云图标的提示里（「萝卜云已开启」），改名只改这里。
   - `server_type: orchestrator` 和 `policy_id` 决定每人一个容器，以及 loboo-webui 会不会把成员的 Key 放进去（只放进这种终端）。
2. **默认开启**：「设置 → 模型」的模型默认值（`models.default_metadata`）里 `"terminalId": "loboo-terminals"`。每个模型的新对话都默认选中萝卜云；单个模型可以在自己的设置里覆盖。
3. **右上角的云图标**：fork 代码 `src/lib/components/chat/Navbar.svelte`，在临时对话按钮旁边。亮着表示这条对话用萝卜云，点一下关、再点一下开，回复生成中不能切换。是否出现由 `src/lib/loboo.ts` 的 `chatCloudTerminal` 决定：所选模型共有一个默认终端、且这个人有权限。以后要加"什么条件下才给开关"，改这一个函数。云图标出现时，输入框里的终端菜单不再显示。
4. **给模型的说明**：`policy-loboo.json` 的 `OPEN_TERMINAL_INFO`，附在 Open Terminal 自带的提示词后面。写了四件事：
   - 交付文件要 `display_file`（`inline=true`）出卡片，不写 `sandbox:` 链接（模型照 ChatGPT 的习惯写这种链接，点不动）；
   - 公网全开、内网不通；
   - loboo-cli 装在 `~/.loboo/bin/loboo-cli`（也在 PATH 上）并已登录。CLI 由环境托管，每次启动时按线上版本更新，所以让模型跳过技能里的版本检查和安装说明（INSTALL.md）；凭据不许输出或外发；
   - 装了哪些工具：搜索用 rg 和 fd，另有 jq、git、pandoc、LibreOffice、poppler、ImageMagick、ffmpeg。

   shell 不用交代，`/bin/sh` 已经是 bash。改完按「日常运维」里改策略的步骤生效。

## 网络：公网全开，内网不通

终端里跑的是会被网页内容诱导的模型，所以它可以访问整个公网，但不能碰宿主机和内网的服务。这台机器上同时跑着 Postgres、CPA、newapi、clash 代理和萝卜的各个服务。防护分两部分，**缺一不可**：

- **INPUT（访问宿主机自己监听的端口）**：靠 iptables-manager（`/etc/iptables/rules.v4`，开机由 `iptables-manager-restore.service` 恢复）的默认 DROP，以及终端网段在 172.16.0.0/12 **之外**。IPTMGR 对 172.16.0.0/12 放行了 clash（7890–7897）、8080 等端口，对 172.17.0.0/16 放行了 Postgres 和萝卜服务。终端网段要是落在 172.16.0.0/12 里，就能连上这些端口。现在终端只能连到对任意来源开放的公网端口（22、80、443、8443、65534）。
- **FORWARD（访问 Docker 发布在 172.17.0.1 等地址上的端口）**：这些端口走 DNAT 加转发，不经过 INPUT，所以由 `loboo-terminals-fw.sh` 在 DOCKER-USER 第 1 条挂独立链 `LOBOOTERM`，丢掉终端网桥发往 10/8、172.16/12、192.168/16、169.254/16（含云元数据）和 100.64/10 的连接。访问本机公网 IP 例外放行：那是 DNAT 到 openresty 的 80/443/65534，和公网上任何人看到的一样，`service.loboo-ai.com`（loboo-cli 用）就在这台机器上。脚本的约定和 `riven-forward.sh` 一样：只管自己的链，不碰 IPTMGR 和 RIVENFWD。

两点限制：
- 同一网桥内的流量不过 iptables（本机没加载 `br_netfilter`），所以终端之间、终端和编排服务之间能互连，只靠各自的随机 API Key 保护。
- 终端容器发布的端口（`PublishAllPorts`）由网络选项 `host_binding_ipv4=127.0.0.1` 限定只绑本机，公网连不进来。

## 镜像与 loboo-cli

`image/` 下是终端镜像：官方 open-terminal，加上 `fonts-noto-cjk`（Word/PowerPoint 预览在容器里转 PDF，缺字体时中文成方块）、搜索工具、loboo-cli 的种子和技能，并把 `/bin/sh` 改指 bash。

- 构建：`image/build.sh loboo/open-terminal:0.14.4-r3`。它从 loboo-mono 检出（默认 `/opt/gits/loboo-mono`，`LOBOO_MONO` 可改）拷 `skills/loboo-cli`，和桌面端装的是同一份；构建时要能访问 `service.loboo-ai.com` 下载 CLI。镜像只在生产机上构建，开发机（Mac）构建出来是 arm64。
- 搜索工具：`ripgrep` 和 `fd-find`（`fd` 链到 `fdfind`）。Codex 等模型习惯先用 rg；open-terminal 的 `match_files` 有 rg 才不跳过大于 1 MiB 的文件。
- `/bin/sh` 是 bash：open-terminal 的 `run_command` 走 `/bin/sh`，模型写的却是 bash。dash 会把 `cmd &> f` 静默拆成"后台运行 cmd、清空 f"，不报错。trixie 上用 debconf 选 sh 已经不起作用，所以按 `dash.preinst` 的说明用 `dpkg-divert` 改：dpkg 记着这处改动，dash 升级或重装也不会改回去。以 `sh` 的名字启动时 bash 处于 POSIX 模式，和平常的 bash 有两处不同：`source` 不带斜杠的文件名会失败；`source` 失败会让整条命令中止。
- `PATH` 最前面是 `/home/user/.loboo/bin`，模型直接写 `loboo-cli` 也能找到。生产上唯一一次非 0 退出，就是模型跑 `command -v loboo-cli` 找不到命令。
- 容器每次启动时，`loboo-entrypoint.sh` 把技能放进 `~/.agents/skills/loboo-cli`（整个目录随镜像替换）。CLI 没有时先放种子到 `~/.loboo/bin/loboo-cli`（技能写死了这个路径）；然后每次启动都在后台跑一遍线上的 `setup.sh`（60 秒超时；比对校验和，相同就不下载），不拖慢启动，输出在 `~/.loboo/update.log`。Open Terminal 会把 `~/.agents/skills` 下的技能列给 loboo-webui，模型看得到，需要时再加载。
- **CLI 托管要和策略一起看**：策略 INFO 里那句"跳过技能的版本检查"，依赖的就是 r3 入口脚本的后台更新。回滚到 r2 时，INFO 要在同一次 PUT 里恢复原文，否则 CLI 不再更新，模型也不会去更新。
- 成员进程的 `oom_score_adj` 是 500：入口脚本最先把自己调成 500，open-terminal 和模型跑的命令都继承它。整机内存耗尽时内核先杀成员进程，保住宿主上的服务（不调的话，按分数最先被杀的是 gpt.loboo-ai.com、CPA、loboo-webui）。这不是隔离手段，进程自己可以改回去；单个容器仍由策略的 1 GiB 上限管着。

**授权**（fork 代码 `backend/open_webui/loboo/cloud.py`，从 `utils/middleware.py` 经 `loboo/hooks.py` 的 `on_terminal_tools` 调用）：

- 每轮对话第一次调用萝卜云的终端工具之前，以及成员在文件面板里唤醒或打开萝卜云时，loboo-webui 把成员本人的 Codex Key（萝卜下发、只在 Codex 有效时有）写进他容器里的 `~/.codex/auth.json`。loboo-cli 第一次调用时用它调 `/cli/account/exchangeAccessKey` 换萝卜会话，存到 `~/.loboo/auth.json`。
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

- **编排服务一停，所有开着萝卜云的对话都会报错**（"Terminal unavailable" / "Cannot connect to host 127.0.0.1:18210"），成员点右上角的云图标关掉就能继续。重启、换镜像都挑人少的时候，几秒到十几秒。重建编排服务不影响在跑的成员容器：2026-10-25 07:27 实测，2 个在跑的成员容器被正常接管。
- 日志：编排服务和 socket-proxy 用 `local` 驱动轮转（compose 里的 `x-logging`，50m × 10，每个服务最多约 95 MB）。Terminals 0.2.5 每转发一个请求都会多打一条约 41 KB 的 `AttributeError` 堆栈（`BackgroundTasks` 用法与镜像里的 FastAPI 不兼容，请求本身成功），看拉起和回收时用 `docker compose logs --since 1h terminals | grep -E 'Provision|Reap'`。成员容器的日志随容器删除。
- 容器在真用到终端时才拉起：模型调用终端工具（回复上方提示「萝卜云启动中」）、在文件面板里点唤醒或从文件卡片打开、输入 `/` 选终端技能。普通消息不碰容器：每条消息要的技能列表和 AGENTS.md 用 loboo-webui 按人缓存的那份（对话里用 `$` 点名过的技能正文也是），容器被用过之后在后台刷新（在面板或 Shell 里直接改技能目录、AGENTS.md，要等下一次调用终端工具后才进系统提示；兜底是缓存满 6 小时、容器又醒着时顺手刷新；用 `$` 点名缓存里没有的技能时当场重取）。文件面板 10 分钟没操作就暂停刷新，容器休眠时显示卡片、点了才唤醒。冷启动 3.5–4.8 秒，热请求约 20 毫秒，空闲容器约 69 MiB，宿主侧另有约 16 MiB。空闲 15 分钟停掉并删除容器，home 保留。
- 改策略：编辑 `policy-loboo.json`，然后按顺序生效：
  1. PUT 整份 `policy-loboo.json`，**必须带全部字段**：漏了 `memory_limit`，容器就不限内存（Terminals `docker.py:110-113`）。
  2. 重建 system 容器：refresh 时指定 `user_id` 为 `system`（下面第二条命令）。工具描述和兜底提示词都来自这个容器。
  3. 让 loboo-webui 重新取终端信息：没有 Redis 时，它把工具列表和终端提示词在进程里缓存 1 小时（fork 的 `utils/tools.py`）。在管理后台重新保存一次「萝卜云」连接立即生效，否则最多 1 小时。
  4. 成员容器在下一次新建时才用上新镜像和新 env，也就是空闲 15 分钟被回收之后。
  ```sh
  . ./.env
  curl -s -X PUT http://127.0.0.1:18210/api/v1/policies/loboo -H "Authorization: Bearer $TERMINALS_API_KEY" \
    -H 'Content-Type: application/json' --data @policy-loboo.json
  curl -s -X POST http://127.0.0.1:18210/api/v1/terminals/refresh -H "Authorization: Bearer $TERMINALS_API_KEY" \
    -H 'Content-Type: application/json' -d '{"user_id":"system","policy_id":"loboo","only_idle":false}'
  ```
  refresh 的其他用法：`only_idle=true` 几乎不起作用，只拆本来就该回收的容器（Terminals `base.py:291-322`）；不带 `user_id` 时，`only_idle=false` 会立刻拆掉在跑的成员容器，打断正在跑的命令；`reset=true` 会连 home 一起清空。
- 换镜像：`image/build.sh` 打新标签；`docker-compose.yml` 里改 `TERMINALS_IMAGE`，把新标签加进 `TERMINALS_ALLOWED_IMAGES`（逗号分隔的 fnmatch 模式，只在 PUT 策略时校验），`docker compose up -d`；再改 `policy-loboo.json` 的镜像名，按上面的步骤生效。上一版镜像别删，白名单里也留着它：回滚时只把策略的镜像（以及依赖新镜像的 INFO）改回去，按上面的步骤生效，compose 不用动。
- 一个人的数据：删用户时编排服务不会清他的 home，要手动删 `data/terminals/<用户 id>`（先 `docker rm -f` 他的容器）。没有容量上限，也没有备份。
- 有一个 `system` 用户的容器，是 loboo-webui 拉工具列表和终端提示词时建的（每小时最多一次），同样会被空闲回收。
- 防护链在开机时由 `loboo-terminals-fw.service` 挂上；它是 `PartOf=docker.service`，`systemctl restart docker` 时会跟着重新挂。这一点还没实测过：重启 Docker 后先看 `loboo-terminals-fw.sh status`，没挂上就手动执行 `loboo-terminals-fw.sh apply`。
- 生产没开 live-restore，重启 Docker 或宿主机后，成员容器都停在 Exited，不会自动清理：编排服务等这个人下次用到时才删掉重建，不再回来的人，他的 Exited 容器会一直留着。重启后清一次（home 是挂载的宿主机目录，不受影响）：
  ```sh
  docker ps -a --filter label=app.kubernetes.io/managed-by=terminals --filter status=exited
  docker ps -aq --filter label=app.kubernetes.io/managed-by=terminals --filter status=exited | xargs -r docker rm
  ```
- loboo-webui 写 Key 时调用编排服务，超时 10 秒（`AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA`）。编排服务卡住时，那一轮第一次调用终端工具最多多等这么久；写入失败不会让对话失败。文件面板问容器醒着没有，用的是编排服务的实例列表（`GET /api/v1/terminals`，不拉起也不续命，loboo-webui 缓存 10 秒）。

## 验证

两个脚本都在宿主机上用 root 运行，不碰真实用户的数据：

- `probe-network.sh`：在终端网络里起一个不带过滤的临时容器，测公网、`service.loboo-ai.com`，以及宿主机监听的每个端口、Docker 发布的每个端口和云元数据。只有本来就对公网开放的端口算预期开放（取自 iptables-manager 对任意来源放行的规则和发布在 0.0.0.0 的端口），其余能连上的标为"意外可达"。新机器上还没装 iptables-manager 时，能连上的端口全会被标出来，这正说明 INPUT 还没锁住，不是脚本出错。被挡住的目标每个要等 3 秒超时，跑完要几分钟。2026-10-24 的结果：公网和 `service.loboo-ai.com` 都是 200；能连上的只有 22、80、443、8443、65534；clash、8080、9090–9097、18xxx 的萝卜服务、Postgres 5432、CPA、newapi、旧 open-webui、云元数据全部不通。
- `probe-user-path.sh`：经编排服务给临时用户 `loboo-probe` 起终端，检查公网、pip、`/bin/sh` 指向 `/usr/bin/bash`（`[[ ]]` 和 `{1..3}` 能用）、rg 和 fd 的版本、`oom_score_adj` 是 500、PATH 上能找到 loboo-cli、技能；等几秒让后台更新跑完，再输出 `~/.loboo/update.log`、CLI 本机版本与线上版本。最后看这个终端用的镜像、它列出的技能，以及编排服务实例列表里它那一行（loboo-webui 的文件面板按这行判断容器醒着没有，应为 `running`、`loboo`、`default`），删掉这个用户。

端到端还要在 LoboOS 里看一眼：
- 新对话右上角的云图标亮着。
- 让它写个网页并说“帮我保存成文件 让我下载”，回复里应该出现带下载按钮的文件卡片。
- 说“用 loboo-cli 看看我的待办”，应该返回本人的数据。

## 迁移到新机器

按顺序做，每步都可以重复执行：

1. **先有 INPUT 默认 DROP。** 恢复 iptables-manager 的 `/etc/iptables/rules.v4` 和 `iptables-manager-restore.service`（或者等价的 INPUT 默认 DROP，只放行对公网开放的端口）。没有这一步，终端能连到宿主机监听的所有端口。
2. 装 Docker，第一次启动 dockerd 之前在 `/etc/docker/daemon.json` 写上 `{"log-driver": "local"}`，让所有容器（包括成员容器）的日志默认轮转；以后再改要重启 Docker，而没开 live-restore 时那会停掉所有容器。准备 loboo-mono 检出（`/opt/gits/loboo-mono`，构建镜像要用）。
3. 建网络。网段要在 172.16.0.0/12 之外，也不能和新机器上已有的路由冲突（先看 `ip -4 route`）：
   ```sh
   docker network create --driver bridge --subnet 10.89.0.0/22 --gateway 10.89.0.1 \
     -o com.docker.network.bridge.name=br-loboo-term \
     -o com.docker.network.bridge.host_binding_ipv4=127.0.0.1 --label loboo.purpose=terminals loboo-terminals
   ```
4. 装防护：复制 `loboo-terminals-fw.sh` 到 `/usr/local/sbin/`（755），`loboo-terminals-fw.service` 到 `/etc/systemd/system/`，`systemctl daemon-reload && systemctl enable --now loboo-terminals-fw.service`。脚本默认取出口网卡上的公网 IP。如果新机器在 VPC 里、网卡上只有内网地址，就在服务里加 `Environment=LOBOO_TERM_PUBLIC_IPS=<公网 IP>`，或者确认 `service.loboo-ai.com` 不在本机。
5. 准备 `/opt/loboo-terminals`（权限 700）：复制 `docker-compose.yml`、`policy-loboo.json`；`.env` 沿用旧机器的值，这样 loboo-webui 里的连接 key 不用改。
6. 迁数据：先停旧机器的编排服务，再 `rsync -a` 旧机器的 `/opt/loboo-terminals/data/` 过来（home 属主是 uid 1000，要保留；`terminals.db` 一起带上）。
7. 构建镜像：`deploy/loboo-terminals/image/build.sh loboo/open-terminal:0.14.4-r3`。
8. `cd /opt/loboo-terminals && docker compose pull && docker compose up -d`，再按「日常运维」应用策略。
9. loboo-webui 一侧：如果 loboo-webui 也迁到了新机器并且编排服务仍在本机，连接地址 `http://127.0.0.1:18210` 不用改；否则按「在 LoboOS 里怎么接」改连接 `url`。模型默认值 `terminalId` 跟着 loboo-webui 的数据库走。
10. 跑 `probe-network.sh` 和 `probe-user-path.sh`，再做一遍端到端。
