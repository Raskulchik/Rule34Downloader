"""The legacy JSON ``dapi`` endpoint on rule34.xxx.

This is what the original single-file tool used. It still exists, but upstream
now frequently answers it with an anti-bot interstitial, so:

* a session cookie may be supplied to get through (``--cookie``), and
* if the user prefers, the pipeline can fall back to another source.

The response shape is coerced in :func:`r34dl.models.coerce_post` because the
upstream is a PHP app that encodes URL fields inconsistently between
deployments.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from ..errors import SourceError
from ..models import PostPage, coerce_post
from ..net.client import HttpClient
from ..tagging import TagSet
from .base import PostSource, SourceInfo

log = logging.getLogger(__name__)

API_URL = "https://rule34.xxx/index.php"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


class JsonApiSource(PostSource):
    """Paged JSON listings from ``?page=dapi``."""

    name = "json"
    page_size = 100

    def __init__(
        self,
        client: HttpClient,
        *,
        base_url: str = API_URL,
        max_pages: int = 200,
    ) -> None:
        super().__init__(client)
        self.base_url = base_url
        self.max_pages = max_pages

    async def fetch_page(self, tags: TagSet, page: int) -> PostPage:
        """Fetch a single 0-based page, as the upstream expects."""
        params = {
            "page": "dapi",
            "s": "post",
            "q": "index",
            "json": "1",
            "tags": tags.legacy_query(),
            "pid": str(page - 1),
            "limit": str(self.page_size),
        }
        payload = await self.client.get_json(f"{self.base_url}?{_query(params)}")
        records = _as_records(payload)
        posts = tuple(coerce_post(item, self.name) for item in records)
        # Upstream signals the end by returning a short page.
        has_more = len(posts) >= self.page_size
        return PostPage(posts=posts, page=page, has_more=has_more)

    def info(self) -> SourceInfo:
        return SourceInfo(
            name=self.name,
            description="rule34.xxx JSON API (needs a session cookie if CAPTCHA-walled)",
            needs_cookie=True,
        )


def _as_records(payload: Any) -> list[Any]:
    """Coerce the upstream payload into a list of post objects."""
    if isinstance(payload, list):
        return payload
    # Some deployments answer with {"posts": [...]} or an error object.
    if isinstance(payload, dict):
        for key in ("posts", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        raise SourceError(
            f"unexpected JSON shape: keys={sorted(payload)[:8]}"
        )
    raise SourceError(f"unexpected JSON payload type: {type(payload).__name__}")


def _query(params: dict[str, str]) -> str:
    return "&".join(f"{key}={quote(str(value), safe='+-_.')}" for key, value in params.items())