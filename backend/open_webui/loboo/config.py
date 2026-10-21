"""萝卜接入的配置，只读环境变量：都是部署时定下的值，不放进 config 表让管理员在界面上改。"""

import os
from urllib.parse import urlsplit


def normalize_url(url: str) -> str:
    """比较接入地址用：scheme 与主机小写，去掉末尾的 /，忽略查询和片段。"""
    parts = urlsplit(url.strip())
    return f'{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip("/")}'


# 萝卜账号服务的内网地址（不带 /account），如 http://172.17.0.1:18101。
LOBOO_SERVICE_URL = os.environ.get('LOBOO_SERVICE_URL', '').strip().rstrip('/')
# 与萝卜服务的 LOBOO_WEBUI_SERVICE_TOKEN 相同。
LOBOO_SERVICE_TOKEN = os.environ.get('LOBOO_SERVICE_TOKEN', '').strip()
# 只有发往这个地址的连接才带上萝卜下发的 Key，如 https://ootd.haina-api.net/v1。
LOBOO_CPA_BASE_URL = normalize_url(os.environ['LOBOO_CPA_BASE_URL']) if os.environ.get('LOBOO_CPA_BASE_URL') else ''
# 取到的配置用多久再重新取；萝卜服务不可达时，旧的 Key 最多再用多久。
KEY_CACHE_TTL_SECONDS = int(os.environ.get('LOBOO_KEY_CACHE_TTL_SECONDS', '600'))
STALE_MAX_SECONDS = int(os.environ.get('LOBOO_STALE_MAX_SECONDS', '21600'))
# 从这些源站（萝卜的页面）跳来免密登录时不用确认，逗号分隔，如 https://service.loboo-ai.com。
LOBOO_TRUSTED_ORIGINS = frozenset(
    origin.strip().rstrip('/').lower()
    for origin in os.environ.get('LOBOO_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
)
# 整页打开时在后台刷新配置，同一个人最多这么久一次。
REFRESH_INTERVAL_SECONDS = 60
# 调萝卜服务的超时；小程序进入时用户在等，不能太长。
REQUEST_TIMEOUT_SECONDS = 5

ENABLED = bool(LOBOO_SERVICE_URL and LOBOO_SERVICE_TOKEN)
