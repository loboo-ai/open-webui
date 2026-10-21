# loboo-webui 部署与运维

loboo-webui 是萝卜二开的 Open WebUI，对外地址 `https://m.loboo-ai.com`；小程序里用 `https://m.haina-api.net`（小程序只能打开它后台配置的业务域名，业务域名要求已备案）。两个域名是同一个服务。萝卜服务首页和小程序首页的「ChatGPT」卡片免密进入这里，对话用成员本人的 CPA Key。萝卜接入的代码在 `backend/open_webui/loboo/`。

## 布局

所有文件都在 `/opt/loboo-webui`，不用容器：

| 路径 | 内容 |
| --- | --- |
| `app/` | 本仓库的检出（`origin/main`），`ctl update` 从这里拉代码 |
| `app/build/` | 前端构建产物 |
| `venv/` | Python 3.12 虚拟环境 |
| `node/` | 只给前端构建用的 Node 22（`.npmrc` 开了 `engine-strict`，要求 Node ≤ 22） |
| `data/` | `DATA_DIR`：上传文件、向量库、模型缓存、`static/` |
| `logs/` | 服务日志 `loboo-webui.log` 与安装日志 |
| `loboo-webui.env` | 运行配置与密钥，权限 600，写法见 `loboo-webui.env.example` |
| `ctl` | 指向 `app/deploy/loboo-webui/ctl.sh` |
| `README.md` | 这台机器上的运维记录 |

数据库是本机 PostgreSQL 实例里的 `loboo-webui` 库（角色 `loboo_webui`），与存量 Open WebUI 的库分开。

## 运行方式

- systemd 单元 `loboo-webui`（`loboo-webui.service`），uvicorn 单进程监听 `172.17.0.1:18120`。萝卜 Key 缓存在进程内，不能开多个 worker。
- openresty 的 `m.loboo-ai.com`、`m.haina-api.net` 两个站点都转发到这个端口，要透传 WebSocket（`Upgrade`、`Connection`），不缓冲响应，读超时放长。新增对外域名时，同时加进 `CORS_ALLOW_ORIGIN`，并在根目录放小程序业务域名的校验文件。
- 模型连接在管理后台配：地址 `https://ootd.haina-api.net/v1`，key 留空，`auth_type` 为 `loboo`，不填模型 ID（从 CPA 动态拉取）。key 留空时，就算有人在界面上把认证方式改掉，最坏也只是不带 key，不会用错别人的 key。

## 日常操作

```bash
/opt/loboo-webui/ctl status      # 状态
/opt/loboo-webui/ctl restart     # 重启并不等待；之后可以跑 health
/opt/loboo-webui/ctl health      # 等健康接口返回 200，最多 2 分钟
/opt/loboo-webui/ctl logs        # 日志末尾 200 行；logs -f 持续跟随
/opt/loboo-webui/ctl update      # 拉 origin/main，按需重装依赖、重建前端，重启并等健康
```

`update` 只在 `backend/requirements.txt` 变了时重装 Python 依赖，只在前端相关文件（`src/`、`static/`、`package*.json`、构建配置）变了时重新构建前端；单元文件变了会装到 `/etc/systemd/system/` 并重新加载。

## 第一次安装

1. 建库：角色 `loboo_webui`，库 `"loboo-webui"`，属主是这个角色。
2. `git clone https://github.com/loboo-ai/open-webui.git /opt/loboo-webui/app`。
3. `python3 -m venv /opt/loboo-webui/venv`（Ubuntu 要先装 `python3.12-venv`），先装 CPU 版 torch：`pip install 'torch<=2.9.1' torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu`，再 `pip install -r app/backend/requirements.txt`。不先装 CPU 版会拉几个 GB 的 CUDA 依赖。
4. 把 Node 22 放进 `/opt/loboo-webui/node`，然后 `ctl build`。
5. 照 `loboo-webui.env.example` 写 `loboo-webui.env`。`WEBUI_SECRET_KEY` 定下后不能改。Open WebUI 的多数开关只在第一次启动时写进数据库，之后改环境变量不生效，要到管理后台或数据库里改。
6. 安装单元、启动：`install -m 644 app/deploy/loboo-webui/loboo-webui.service /etc/systemd/system/`，`systemctl daemon-reload && systemctl enable --now loboo-webui`，`ln -s app/deploy/loboo-webui/ctl.sh /opt/loboo-webui/ctl`。
7. 第一次启动按 `WEBUI_ADMIN_*` 建好管理员。用管理员登录后：建一把 API key，交给萝卜服务作 `LOBOO_WEBUI_ADMIN_TOKEN`（只放开了 `/api/v1/users` 与 `/api/v1/auths/add`）；按上面配好模型连接。
8. 萝卜服务 `.env` 设 `LOBOO_WEBUI_BASE_URL`（`http://172.17.0.1:18120`）、`LOBOO_WEBUI_ADMIN_TOKEN`、`LOBOO_WEBUI_PUBLIC_URL`（`https://m.loboo-ai.com`）、`LOBOO_WEBUI_ALIAS_URLS`（`https://m.haina-api.net`，对应小程序用的别名域名）、`LOBOO_WEBUI_SERVICE_TOKEN`（与这里的 `LOBOO_SERVICE_TOKEN` 相同），重启账号应用。网关对公网屏蔽萝卜的 `/account/api/openwebui/redeem` 与 `/profile`。

## 排查

| 现象 | 先看 |
| --- | --- |
| 进入 ChatGPT 停在「确认登录」 | 日志里 `loboo: sign-in for user … awaits confirmation`：不是从小程序、也不是从 `LOBOO_TRUSTED_ORIGINS` 里的萝卜页面进来的 |
| 对话提示「暂时取不到模型配置」 | 萝卜账号应用是否在跑、`LOBOO_SERVICE_TOKEN` 与萝卜的 `LOBOO_WEBUI_SERVICE_TOKEN` 是否一致 |
| 对话提示「Codex 未开通或已到期」 | 这个成员在萝卜里的 Codex 状态 |
| 模型列表是空的 | CPA 是否可达；重启后还没有成员登录过时，没有可借用的 key 去拉列表 |
| 页面能开、聊天没输出 | 网关是否透传 WebSocket |
