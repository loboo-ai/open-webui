"""
模型头像：模型没有自己的图时显示厂商的标识，不用站点 logo（站点 logo 是萝卜哥的头像）。

厂商取上游连接给的 owned_by（CPA 的 /v1/models 给 anthropic、openai、google、xai），在模型注册表里
是 model['openai']['owned_by']；顶层的 owned_by 对所有 OpenAI 兼容连接都是 'openai'，不能用。
工作区里基于某个模型建的模型跟它的基础模型走。认不出的厂商用通用图标。图在 static/static/models/。
与萝卜服务无关，没配置萝卜服务时也生效。
"""

from collections.abc import Mapping

VENDORS = frozenset({'anthropic', 'openai', 'google', 'xai'})
UNKNOWN = '/static/models/unknown.svg'


def logo_url(models: Mapping, model_id: str) -> str:
    """models 是 app.state.MODELS（dict 或 RedisDict，二者都只保证 in 和 []）。"""
    vendor = _owned_by(models, model_id)
    return f'/static/models/{vendor}.svg' if vendor in VENDORS else UNKNOWN


def _owned_by(models: Mapping, model_id: str | None) -> str | None:
    model = _get(models, model_id)
    if model is None:
        return None
    owned_by = (model.get('openai') or {}).get('owned_by')
    if owned_by:
        return str(owned_by).lower()
    base_id = (model.get('info') or {}).get('base_model_id')
    if not base_id:
        return None
    base = _get(models, base_id) or _get(models, base_id.split(':')[0])
    owned_by = ((base or {}).get('openai') or {}).get('owned_by')
    return str(owned_by).lower() if owned_by else None


def _get(models: Mapping, model_id: str | None) -> dict | None:
    return models[model_id] if model_id and model_id in models else None
