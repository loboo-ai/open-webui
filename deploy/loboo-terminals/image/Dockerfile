# 萝卜云终端镜像：官方 open-terminal，加中文字体、loboo-cli 和它的技能。
#   r1：fonts-noto-cjk（Word/PowerPoint 预览在容器里转 PDF，缺字体时中文成方块）
#   r2：loboo-cli 种子 + 技能，容器启动时由 loboo-entrypoint.sh 放进用户 home
#   r3：rg/fd；/bin/sh 改指 bash；loboo-cli 进 PATH；CLI 由环境托管（每次启动在后台更新）；成员进程 oom_score_adj=500
# 构建用同目录的 build.sh（它先从 loboo-mono 检出拷技能）：./build.sh loboo/open-terminal:0.14.4-r3
# 升级官方镜像时改 FROM，打新标签，再改 docker-compose.yml 与 policy-loboo.json 里的镜像名。
FROM ghcr.io/open-webui/open-terminal:0.14.4
USER root
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f
# r3：搜索工具。Codex 等模型习惯先用 rg；open-terminal 的 match_files 有 rg 才不跳过大于 1 MiB 的文件
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends ripgrep fd-find \
 && rm -rf /var/lib/apt/lists/* \
 && ln -s /usr/bin/fdfind /usr/local/bin/fd
# r3：/bin/sh 改指 bash。run_command 走 /bin/sh，模型写的是 bash；dash 会把 `cmd &> f` 静默拆成后台运行加清空文件。
# trixie 上用 debconf 选 sh 已经不起作用，按 dash.preinst 的说明改用 diversion
RUN dpkg-divert --local --no-rename --divert /usr/bin/sh.distrib --add /usr/bin/sh \
 && ln -s bash /usr/bin/sh.tmp && mv -T /usr/bin/sh.tmp /usr/bin/sh \
 && [ "$(readlink -f /bin/sh)" = /usr/bin/bash ]
# 技能写死了 $HOME/.loboo/bin/loboo-cli，而 home 是运行时挂进来的宿主机目录，所以镜像里只放种子
RUN curl -fsSL https://service.loboo-ai.com/downloads/loboo-cli/setup.sh | HOME=/opt/loboo/seed bash
# 技能与桌面端安装的是同一份：loboo-mono/skills/loboo-cli
COPY skills/loboo-cli /opt/loboo/skills/loboo-cli
COPY loboo-entrypoint.sh /opt/loboo/entrypoint.sh
RUN chmod 755 /opt/loboo/entrypoint.sh
# r3：模型直接写 loboo-cli 也能找到（技能里用的是绝对路径）
ENV PATH="/home/user/.loboo/bin:${PATH}"
USER user
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/loboo/entrypoint.sh"]
CMD ["run"]
