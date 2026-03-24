import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    listmonk_url: str = os.getenv("LISTMONK_URL", "http://localhost:9000")
    listmonk_user: str = os.getenv("LISTMONK_USER", "listmonk")
    listmonk_api_key: str = os.getenv("LISTMONK_API_KEY", "")

    # IMAP settings for unsubscribe monitoring
    imap_host: str = os.getenv("IMAP_HOST", "")
    imap_port: int = int(os.getenv("IMAP_PORT", "993"))
    imap_user: str = os.getenv("IMAP_USER", "")
    imap_pass: str = os.getenv("IMAP_PASS", "")
    imap_use_ssl: bool = os.getenv("IMAP_USE_SSL", "true").lower() == "true"

    @property
    def imap_configured(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.imap_pass)

    @property
    def base_url(self) -> str:
        return self.listmonk_url.rstrip("/")


settings = Settings()
