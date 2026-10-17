"""DeepSeek Search & Fetch as a Hermes web backend.

- ``search``: ``POST https://api.deepseek.com/v1/tools/search`` with ``{"query", "count"}``.
- ``extract``: ``POST https://api.deepseek.com/v1/tools/fetch`` with ``{"url"}``, one request
  per URL, run concurrently.

Both need ``DEEPSEEK_API_KEY`` (https://platform.deepseek.com/api_keys), read through Hermes'
secret scope. It is the same key Hermes uses for DeepSeek models.

DeepSeek bills each successful request ($0.75 per 1,000) and limits each API to 5 requests a
second per account (Fetch also per domain), so inputs the API would reject are refused here,
before a request is made.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

from agent.secret_scope import get_secret
from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

KEY_ENV = "DEEPSEEK_API_KEY"
KEY_URL = "https://platform.deepseek.com/api_keys"
TOP_UP_URL = "https://platform.deepseek.com/top_up"
STATUS_URL = "https://status.deepseek.com/"
SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"

TIMEOUT = 60.0
SEARCH_LIMIT_MAX = 50
QUERY_MAX = 128
URL_MAX = 8192
# Fetch is limited to 5 requests a second per account and per domain; stay under it.
CONCURRENCY = 3
_MESSAGE_MAX = 300

_BASE_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "hermes-deepseek-web/0.1.0",
}

MISSING_KEY = f"DeepSeek Search and Fetch require {KEY_ENV}. Get a key at {KEY_URL} and set it with `hermes tools`."


class DeepSeekError(Exception):
    """A failure whose message is safe to hand to the model."""


def _api_key() -> str:
    return (get_secret(KEY_ENV, "") or "").strip()


def _headers(api_key: str) -> dict[str, str]:
    return {**_BASE_HEADERS, "Authorization": f"Bearer {api_key}"}


def _deepseek_message(response: httpx.Response) -> str:
    """DeepSeek's own error text (it never contains the request's credentials), shortened."""
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else ""
    return str(message or "").strip()[:_MESSAGE_MAX]


def _status_error(response: httpx.Response) -> DeepSeekError:
    code = response.status_code
    if code == 401:
        hint = f"Check {KEY_ENV}."
    elif code == 402:
        hint = f"The DeepSeek account is out of balance; top up at {TOP_UP_URL}."
    elif code == 403:
        hint = "DeepSeek's security policy refused this page."
    elif code == 404:
        hint = "DeepSeek found no usable content at this URL."
    elif code == 429:
        hint = "Rate limit reached (5 requests a second per account, and per site for fetch); try again shortly."
    elif code in (408, 504) or code >= 500:
        hint = f"Try again later or check {STATUS_URL}."
    else:
        hint = ""
    detail = _deepseek_message(response)
    return DeepSeekError(" ".join(part for part in (f"DeepSeek returned HTTP {code}.", hint, detail) if part))


def _transport_error(exc: Exception) -> DeepSeekError:
    if isinstance(exc, httpx.TimeoutException):
        return DeepSeekError("DeepSeek request timed out. Try again later.")
    if isinstance(exc, httpx.RequestError):
        return DeepSeekError("Could not reach DeepSeek. Check your network connection.")
    return DeepSeekError("DeepSeek returned an invalid response.")


def _payload(response: httpx.Response) -> dict[str, Any]:
    """The JSON object of a successful response, or raise :class:`DeepSeekError`."""
    if response.status_code >= 400:
        raise _status_error(response)
    try:
        body = response.json()
    except ValueError as exc:
        raise DeepSeekError("DeepSeek returned a response that is not JSON.") from exc
    if not isinstance(body, dict):
        raise DeepSeekError("DeepSeek returned an invalid response.")
    return body


def _url_problem(url: str) -> str:
    """Why DeepSeek Fetch would reject ``url``, or ``""`` when it is acceptable."""
    if len(url) > URL_MAX:
        return f"URLs longer than {URL_MAX} characters cannot be extracted."
    try:
        parts = urlsplit(url)
    except ValueError:
        parts = None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        return "Only http:// and https:// URLs can be extracted."
    if parts.username is not None or parts.password is not None:
        return "URLs with a username or password cannot be extracted."
    return ""


def _failed(url: str, error: str) -> dict[str, Any]:
    return {"url": url, "title": "", "content": "", "raw_content": "", "error": error, "metadata": {"sourceURL": url}}


def _document(url: str, body: dict[str, Any]) -> dict[str, Any]:
    content = body.get("content") or ""
    if not isinstance(content, str) or not content.strip():
        return _failed(url, "DeepSeek returned no content for this page.")
    title = body.get("title") or ""
    if not isinstance(title, str):
        title = ""
    return {"url": url, "title": title, "content": content, "raw_content": content,
            "metadata": {"sourceURL": url, "title": title}}


def _hit(row: dict[str, Any], position: int) -> dict[str, Any]:
    # ``summary`` is DeepSeek's longer, model-oriented abstract; ``snippet`` is the UI blurb.
    return {
        "title": row.get("title") or "",
        "url": row["url"],
        "description": row.get("summary") or row.get("snippet") or "",
        "position": position,
    }


class DeepSeekWebSearchProvider(WebSearchProvider):
    """DeepSeek Search + Fetch."""

    @property
    def name(self) -> str:
        return "deepseek"

    @property
    def display_name(self) -> str:
        return "DeepSeek Search"

    def is_available(self) -> bool:
        return bool(_api_key())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": "DeepSeek Search",
            "badge": "paid · $0.75 / 1k requests",
            "tag": "Search and page fetch on api.deepseek.com; uses your DEEPSEEK_API_KEY.",
            "env_vars": [{
                "key": KEY_ENV,
                "prompt": "DeepSeek API key",
                "url": KEY_URL,
            }],
        }

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        api_key = _api_key()
        if not api_key:
            return {"success": False, "error": MISSING_KEY}
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "The search query is empty."}
        if len(query) > QUERY_MAX:
            return {"success": False, "error": (
                f"DeepSeek search queries are limited to {QUERY_MAX} characters; this one has {len(query)}. "
                "Shorten it and search again."
            )}
        limit = max(1, min(int(limit or 5), SEARCH_LIMIT_MAX))
        logger.info("DeepSeek search (limit=%d)", limit)
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                # need_content stays off: summaries are enough, and full pages cost tokens and time.
                response = client.post(SEARCH_URL, json={"query": query, "count": limit}, headers=_headers(api_key))
            rows = _payload(response).get("results")
        except DeepSeekError as exc:
            return {"success": False, "error": str(exc)}
        except httpx.HTTPError as exc:
            return {"success": False, "error": str(_transport_error(exc))}
        if not isinstance(rows, list):
            return {"success": False, "error": "DeepSeek returned an invalid search response."}
        hits = [row for row in rows if isinstance(row, dict) and row.get("url")][:limit]
        return {"success": True, "data": {"web": [_hit(row, i + 1) for i, row in enumerate(hits)]}}

    async def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        api_key = _api_key()
        if not api_key:
            return [_failed(url, MISSING_KEY) for url in urls]
        logger.info("DeepSeek extract: %d URL(s)", len(urls))
        semaphore = asyncio.Semaphore(CONCURRENCY)
        headers = _headers(api_key)

        async def one(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
            problem = _url_problem(url)
            if problem:
                return _failed(url, problem)
            async with semaphore:
                try:
                    response = await client.post(FETCH_URL, json={"url": url}, headers=headers)
                    return _document(url, _payload(response))
                except DeepSeekError as exc:
                    return _failed(url, str(exc))
                except httpx.HTTPError as exc:
                    return _failed(url, str(_transport_error(exc)))

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            return list(await asyncio.gather(*(one(client, url) for url in urls)))
