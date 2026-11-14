"""The entry point: `uvicorn app.main:app`."""

from app.application import create_app
from app.config import Settings
from app.observability import configure_logging

settings = Settings.from_env()
configure_logging(settings.log_level, json_output=settings.log_json)

app = create_app(settings)
