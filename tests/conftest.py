import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Settings are cached, so the environment must be shaped before the app imports.
os.environ.setdefault("PUBLIC_URL", "https://library.example.com")
os.environ.setdefault("AUTH_ENABLED", "true")
os.environ.setdefault("OIDC_METADATA_URL", "https://idp.example.com/.well-known/openid-configuration")
os.environ.setdefault("OIDC_CLIENT_ID", "kicad-library-manager")


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    # The session cookie is Secure, so the client must use an https base URL
    # or it will silently decline to send it back.
    with TestClient(app, base_url="https://library.example.com") as test_client:
        yield test_client
