from pydantic import BaseModel

class s3Settings(BaseModel):
    url_internal: str = "http://localhost:8888"
    url_external: str = "http://localhost:8888"
    bucket: str = "fire-simulator"
    access_key: str = ""
    secret_key: str = ""
