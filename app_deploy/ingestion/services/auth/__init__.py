import httpx
from datetime import datetime, timedelta

from settings.auth import AuthSettings
from settings import config


class AuthenticationError(BaseException):
    """Exeption for authentication error"""

    pass


class AuthenticationService:
    def __init__(self, client: httpx.Client, auth_settings: AuthSettings):
        self._client = client
        self._auth_settings = auth_settings
        self._auth_data = {
            "grant_type": "password",
            "username": self._auth_settings.get_username(),
            "password": self._auth_settings.get_password(),
            "client_id": self._auth_settings.client_id,
        }

        self._token = None
        self._expires_in = datetime.now()

    def seconds_until_expiration(self) -> float:
        """Returns how many seconds are left until the token actually expires."""
        remaining = self._expires_in - datetime.now()
        return max(0.0, remaining.total_seconds())

    def get_token(self) -> str:
        if self._token is not None and self._expires_in > datetime.now():
            return self._token

        return self.refresh()

    def refresh(self) -> str:
        res = self._client.post(
            self._auth_settings.token_endpoint, data=self._auth_data
        )
        if not res.is_success:
            raise AuthenticationError(
                f"Error refreshing authentication token [{res.status_code}]: {res.content}"
            )
        body = res.json()

        self._token = body["access_token"]
        expires_in = body.get("expires_in", 60)
        self._expires_in = datetime.now() + timedelta(seconds=expires_in // 2)

        return self._token

def _new_instance() -> AuthenticationService:
    http_client = httpx.Client(
        verify=config.auth.ca_crt_file
        if config.auth.ca_crt_file is not None
        else True
    )
    return AuthenticationService(client=http_client, auth_settings=config.auth)


auth_service = _new_instance()