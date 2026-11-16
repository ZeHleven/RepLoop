from dataclasses import dataclass

import httpx

from app.config import settings


class WeChatLoginError(Exception):
    """Safe, user-facing base error for WeChat login."""


class WeChatConfigurationError(WeChatLoginError):
    pass


class WeChatInvalidCodeError(WeChatLoginError):
    pass


class WeChatUnavailableError(WeChatLoginError):
    pass


@dataclass(frozen=True)
class WeChatSession:
    app_id: str
    open_id: str
    union_id: str | None = None


async def exchange_code(code: str) -> WeChatSession:
    if not settings.WECHAT_APP_ID or not settings.WECHAT_APP_SECRET:
        raise WeChatConfigurationError("微信登录尚未配置，请联系管理员")

    try:
        async with httpx.AsyncClient(
            timeout=settings.WECHAT_LOGIN_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(
                "https://api.weixin.qq.com/sns/jscode2session",
                params={
                    "appid": settings.WECHAT_APP_ID,
                    "secret": settings.WECHAT_APP_SECRET,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise WeChatUnavailableError("微信登录服务暂时不可用，请稍后重试") from exc

    error_code = payload.get("errcode")
    if error_code:
        if error_code in {40029, 40163}:
            raise WeChatInvalidCodeError("微信登录凭证已失效，请重试")
        raise WeChatUnavailableError("微信登录服务暂时不可用，请稍后重试")

    open_id = payload.get("openid")
    if not isinstance(open_id, str) or not open_id:
        raise WeChatUnavailableError("微信登录服务返回异常，请稍后重试")

    union_id = payload.get("unionid")
    return WeChatSession(
        app_id=settings.WECHAT_APP_ID,
        open_id=open_id,
        union_id=union_id if isinstance(union_id, str) and union_id else None,
    )
