from pydantic import BaseModel

from urllib.parse import urlsplit, urljoin

class DittoSettings(BaseModel):
    api_url: str = "http://localhost:8080"
    base_api_path: str = "/api/2"
    base_ws_path: str = "/ws/2"


    def get_base_url(self) -> str:
        stripped_url = str(self.api_url).rstrip("/")
        stripped_base_path = self.base_api_path.strip("/")
        return f"{stripped_url}/{stripped_base_path}"
    
    def get_base_ws(self) -> str:
        url = urlsplit(self.api_url)
        url = url._replace(scheme="wss" if url.scheme == "https" else "ws").geturl()
        return urljoin(url, self.base_ws_path).rstrip("/")