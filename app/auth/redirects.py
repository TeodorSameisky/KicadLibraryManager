"""Where a sign-in is allowed to land.

The redirect that ends a sign-in carries a freshly minted session. Sending it
anywhere but this deployment's own pages hands that session to whoever named
the destination.
"""

from __future__ import annotations

from urllib.parse import urlsplit


def is_internal(url: str, public_url: str) -> bool:
    """Whether an absolute URL points inside this deployment.

    Sharing an origin is not enough when the app is mounted under a path
    prefix: another application on the same host shares the origin too.
    """
    candidate, own = urlsplit(url), urlsplit(public_url)
    if (candidate.scheme, candidate.hostname, candidate.port) != (
        own.scheme,
        own.hostname,
        own.port,
    ):
        return False

    prefix = own.path.rstrip("/")
    return not prefix or candidate.path == prefix or candidate.path.startswith(prefix + "/")


def landing_url(requested: str | None, public_url: str) -> str:
    """The absolute URL to return to after sign-in.

    Whatever is not one of this deployment's own pages becomes the panel.
    """
    panel = f"{public_url}/panel"
    if not requested:
        return panel

    # A bare path is the usual case: the page that refused a request knows its
    # own path, not the address a proxy presents it under. "//host/..." is not
    # a path -- a browser reads it as another site.
    if requested.startswith("/") and not requested.startswith("//"):
        own = urlsplit(public_url)
        prefix = own.path.rstrip("/")
        if prefix and requested != prefix and not requested.startswith(prefix + "/"):
            requested = prefix + requested
        return f"{own.scheme}://{own.netloc}{requested}"

    return requested if is_internal(requested, public_url) else panel
