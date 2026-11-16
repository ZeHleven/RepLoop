from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("密码至少 8 位")
        return v

    @field_validator("email", "phone", mode="before")
    @classmethod
    def at_least_one(cls, v):
        return v

    def model_post_init(self, __context) -> None:
        if not self.email and not self.phone:
            raise ValueError("邮箱或手机号至少填一项")


class LoginRequest(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class WeChatLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("微信登录凭证不能为空")
        return normalized


class WeChatLoginResponse(TokenResponse):
    is_new_user: bool
    onboarding_completed: bool
