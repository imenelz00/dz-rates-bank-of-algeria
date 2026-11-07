"""Polite HTTP client: honours robots.txt (really don't wanna risk a lawsuit which i cannot aford), identifies itself, rate-limits."""
import os
import ssl
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .. import config
from .base import RobotsDisallowed, SourceError

_last_request = 0.0


def _client():
    # Read the env vars at call time so run_live.py / the shell always take effect.
    if os.getenv("BOA_INSECURE_TLS") == "1":
        print("WARNING: TLS certificate verification is DISABLED (BOA_INSECURE_TLS=1)")
        verify = False
    elif os.getenv("BOA_CA_BUNDLE"):
        verify = ssl.create_default_context(cafile=os.environ["BOA_CA_BUNDLE"])
    else:
        verify = True
    return httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=30, follow_redirects=True, verify=verify)


def _permission() -> bool:
    return os.getenv("BOA_PERMISSION_GRANTED") == "1" or getattr(config, "BOA_PERMISSION_GRANTED", False)


def allowed_by_robots(url: str, client: httpx.Client) -> bool:
    p = urlparse(url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        r = client.get(robots_url)
    except httpx.HTTPError as e:
        raise SourceError(f"cannot read robots.txt: {e}") from e
    if r.status_code >= 500:
        raise SourceError(f"robots.txt unavailable (HTTP {r.status_code})")
    if r.status_code >= 400:
        return True  # no robots.txt => no restrictions
    rp = RobotFileParser()
    rp.parse(r.text.splitlines())
    return rp.can_fetch(config.USER_AGENT, url)


def polite_get(url: str) -> httpx.Response:
    global _last_request
    with _client() as client:
        if not _permission() and not allowed_by_robots(url, client):
            raise RobotsDisallowed(f"robots.txt disallows automated access to {url}")
        wait = config.REQUEST_DELAY_SECONDS - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            r = client.get(url)
            _last_request = time.monotonic()
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SourceError(f"GET {url} failed: {e}") from e
        return r
