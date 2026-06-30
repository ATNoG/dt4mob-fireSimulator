from typing import Self
from pydantic import BaseModel, model_validator


class AuthSettings(BaseModel):
    token_endpoint: str = (
        "https://localhost:8443/auth/realms/dt4mob/protocol/openid-connect/token"
    )

    username: str | None = None
    password: str | None = None

    username_file: str | None = None
    password_file: str | None = None

    ca_crt_file: str | None = None

    client_id: str = "ditto"

    @model_validator(mode="after")
    def username_config_valid(self) -> Self:
        if self.username is None and self.username_file is None:
            raise ValueError("One of AUTH_USERNAME or AUTH_USERNAME_FILE must be set")

        if self.username_file is not None and self.username is not None:
            raise ValueError(
                "AUTH_USERNAME and AUTH_USERNAME_FILE cannot be set at the same time"
            )

        return self

    @model_validator(mode="after")
    def password_config_valid(self) -> Self:
        if self.password is None and self.password_file is None:
            raise ValueError("One of AUTH_PASSWORD or AUTH_PASSWORD_FILE must be set")

        if self.password_file is not None and self.password is not None:
            raise ValueError(
                "AUTH_PASSWORD and AUTH_PASSWORD_FILE cannot be set at the same time"
            )

        return self

    def get_username(self) -> str:
        if self.username_file is not None:
            with open(self.username_file) as f:
                return f.read()

        assert self.username is not None
        return self.username

    def get_password(self) -> str:
        if self.password_file is not None:
            with open(self.password_file) as f:
                return f.read()

        assert self.password is not None
        return self.password