import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    listmonk_url: str = os.getenv("LISTMONK_URL", "http://localhost:9000")
    listmonk_user: str = os.getenv("LISTMONK_USER", "listmonk")
    listmonk_api_key: str = os.getenv("LISTMONK_API_KEY", "")

    @property
    def base_url(self) -> str:
        return self.listmonk_url.rstrip("/")


settings = Settings()
