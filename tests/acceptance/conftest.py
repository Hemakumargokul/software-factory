"""Black-box acceptance harness. The service under test is a live HTTP
server whose base URL arrives in SHORTENER_URL (set by the acceptance
stage). No product code is imported — this suite never enters the sandbox
and the implementing agent never sees it."""

import os

import httpx
import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("SHORTENER_URL")
    if not url:
        pytest.skip("SHORTENER_URL not set — run via the acceptance stage "
                    "or export it manually")
    return url.rstrip("/")


@pytest.fixture()
def client(base_url) -> httpx.Client:
    # Redirects must be observable, not followed: the suite asserts on them.
    with httpx.Client(base_url=base_url, follow_redirects=False,
                      timeout=10.0) as c:
        yield c
