"""萝卜（loboo）账号接入：小程序免密登录、按萝卜账号下发每个人的 Codex Key。

萝卜服务是账号与 Key 的唯一来源；这里只在进程内缓存 Key，不写库、不进浏览器。
挂进上游的位置只有 main.py（挂路由）、utils/headers.py（auth_type='loboo'）、
routers/auths.py（密码登录与整页打开时取配置）、routers/images.py（图片用本人的 Key）、
routers/models.py（模型头像回落到厂商标识，见 model_logos.py）、
utils/tools.py（对话里的联网搜索，见 web_search.py）。
"""
