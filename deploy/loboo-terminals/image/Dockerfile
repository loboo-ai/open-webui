# 萝卜云终端镜像：官方 open-terminal，加中文字体、loboo-cli 和它的技能。
#   r1：fonts-noto-cjk（Word/PowerPoint 预览在容器里转 PDF，缺字体时中文成方块）
#   r2：loboo-cli 种子 + 技能，容器启动时由 loboo-entrypoint.sh 放进用户 home
# 构建用同目录的 build.sh（它先从 loboo-mono 检出拷技能）：./build.sh loboo/open-terminal:0.14.4-r2
# 升级官方镜像时改 FROM，打新标签，再改 docker-compose.yml 与 policy-loboo.json 里的镜像名。
FROM ghcr.io/open-webui/open-terminal:0.14.4
USER root
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f
# 技能写死了 $HOME/.loboo/bin/loboo-cli，而 home 是运行时挂进来的宿主机目录，所以镜像里只放种子
RUN curl -fsSL https://service.loboo-ai.com/downloads/loboo-cli/setup.sh | HOME=/opt/loboo/seed bash
# 技能与桌面端安装的是同一份：loboo-mono/skills/loboo-cli
COPY skills/loboo-cli /opt/loboo/skills/loboo-cli
COPY loboo-entrypoint.sh /opt/loboo/entrypoint.sh
RUN chmod 755 /opt/loboo/entrypoint.sh
USER user
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/loboo/entrypoint.sh"]
CMD ["run"]
