"""Named URL-signer strategies for SAS-protected catalogs.

A *signer* maps an asset href to an authorised href. A plain callable works
everywhere a signer is accepted, but callables cannot be stored in a JSON
monitor config. Named strategies solve that::

    MonitorConfig(..., signer="planetary-computer")

round-trips through JSON and the ``monitor`` CLI, and needs no extra
dependency — signing is plain HTTPS against the provider's token endpoint.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

import requests

PC_SAS_ENDPOINT = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
DEFAULT_TIMEOUT = 60
MAX_RETRIES = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
CACHE_SIZE = 512
CACHE_SAFETY_MARGIN_S = 120  # stop reusing a signed URL 2 min before expiry
CACHE_FALLBACK_TTL_S = 1800  # when the signed URL carries no expiry

Signer = Callable[[str], str]
SignerSpec = Optional[Union[str, Signer]]


class SigningError(RuntimeError):
    """Raised when a URL cannot be signed or a strategy name is unknown."""


# href -> (signed_href, expires_at_epoch). SAS tokens are typically valid
# for about an hour; reusing them avoids hammering the signing endpoint
# (which rate-limits aggressive callers) across scenes and re-runs.
_sign_cache: Dict[str, Tuple[str, float]] = {}


def _expiry_of_signed_href(signed_href: str) -> float:
    """Epoch seconds when a signed URL stops working (best effort)."""
    try:
        query = parse_qs(urlparse(signed_href).query)
        se = (query.get("se") or [""])[0]
        if se:
            dt = datetime.fromisoformat(se.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    except (ValueError, TypeError):
        pass
    return time.time() + CACHE_FALLBACK_TTL_S


def _cached_sign(href: str) -> Optional[str]:
    entry = _sign_cache.get(href)
    if entry is None:
        return None
    signed_href, expires_at = entry
    if time.time() > expires_at - CACHE_SAFETY_MARGIN_S:
        _sign_cache.pop(href, None)
        return None
    return signed_href


def _cache_sign(href: str, signed_href: str) -> None:
    if len(_sign_cache) >= CACHE_SIZE:
        # Evict the entry expiring soonest; the cache is tiny by design.
        oldest = min(_sign_cache, key=lambda k: _sign_cache[k][1])
        _sign_cache.pop(oldest, None)
    _sign_cache[href] = (signed_href, _expiry_of_signed_href(signed_href))


def clear_sign_cache() -> None:
    """Empty the SAS signature cache (mainly useful in tests)."""
    _sign_cache.clear()


def planetary_computer_signer(
    href: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> str:
    """Sign one href with Planetary Computer's anonymous SAS endpoint.

    Handles both response shapes the endpoint has used: the current
    ``{"href": "<fully-signed-url>"}`` and the older ``{"token": "<sas>"}``.
    Signed URLs are cached until just before their ``se=`` expiry, and
    transient failures (429/5xx) are retried with exponential backoff.
    """
    cached = _cached_sign(href)
    if cached is not None:
        return cached
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(
                PC_SAS_ENDPOINT, params={"href": href}, timeout=timeout
            )
            if resp.status_code in RETRYABLE_STATUS and attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            body = resp.json()
        except SigningError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            raise SigningError(f"could not sign {href!r}: {exc}") from exc
        if isinstance(body, dict) and body.get("href"):
            signed = str(body["href"])
            _cache_sign(href, signed)
            return signed
        token = body.get("token") if isinstance(body, dict) else None
        if token:
            signed = f"{href}{'&' if '?' in href else '?'}{token}"
            _cache_sign(href, signed)
            return signed
        raise SigningError(
            f"SAS endpoint returned no usable signature for {href!r}"
        )
    raise SigningError(f"could not sign {href!r}: {last_exc}")


SIGNERS: Dict[str, Signer] = {
    "planetary-computer": planetary_computer_signer,
}


def list_signers() -> List[str]:
    """Names of the registered signer strategies."""
    return sorted(SIGNERS)


def resolve_signer(spec: SignerSpec) -> Optional[Signer]:
    """Normalise a signer spec to a callable (or ``None``).

    ``None`` passes through, a callable is returned unchanged, and a
    registered strategy name is looked up. Anything else raises
    :class:`SigningError`.
    """
    if spec is None or callable(spec):
        return spec
    if isinstance(spec, str):
        try:
            return SIGNERS[spec]
        except KeyError as exc:
            raise SigningError(
                f"unknown signer {spec!r}; choose from {list_signers()}"
            ) from exc
    raise SigningError(
        f"signer must be None, a callable, or one of {list_signers()}; "
        f"got {type(spec).__name__}"
    )
