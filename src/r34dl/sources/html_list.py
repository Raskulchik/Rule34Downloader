"""The rule34.paheal.net HTML post-list source.

The JSON ``dapi`` endpoint is now dead on both ``rule34.xxx`` (anti-bot wall)
and ``rule34.paheal.net`` (404). What still works - and what this module reads -
is the ordinary paginated HTML list, which embeds every result as::

    <div class='shm-thumb thumb' data-ext='png' data-tags='...' data-post-id='7451852'>
      <a class='shm-thumb-link' href='/post/view/7451852'> ... </a>
      <a href='https://r34i.paheal-cdn.net/89/e1/89e1...'>File Only</a>
    </div>

Parsing is done with the stdlib :mod:`html.parser` so that no HTML-parsing
dependency is pulled in.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from urllib.parse import quote

from ..errors import SourceUnavailableError
from ..models import Post, PostPage
from ..net.client import HttpClient
from ..tagging import TagSet
from .base import PostSource, SourceInfo

log = logging.getLogger(__name__)

BASE_URL = "https://rule34.paheal.net"
LIST_URL = f"{BASE_URL}/post/list"
#: Upstream renders up to this many thumbnails per page.
PAGE_SIZE = 40
FILE_LINK_HOST = "r34i.paheal-cdn.net"
#: The thumbnail's title attribute carries metadata as:
#: ``{tags}\n4000x3000 // 1.4MB // png\nSeptember 19, 2026; 20:30``
_META_RE = re.compile(
    r"^\s*(?P<w>\d{1,6})\s*[x×]\s*(?P<h>\d{1,6})\s*//\s*"
    r"(?P<size>[\d.]+\s*[KMGT]?B)\s*//\s*(?P<ext>[A-Za-z0-9]{1,8})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([KMGT]?)B\s*$", re.IGNORECASE)
_SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


class PahealHtmlSource(PostSource):
    """Paginated HTML listings from rule34.paheal.net."""

    name = "paheal"
    page_size = PAGE_SIZE

    def __init__(self, client: HttpClient, *, base_url: str = LIST_URL) -> None:
        super().__init__(client)
        self.base_url = base_url.rstrip("/")

    async def fetch_page(self, tags: TagSet, page: int) -> PostPage:
        url = self._url_for(tags, page)
        try:
            html = await self.client.get_text(url)
        except SourceUnavailableError as exc:
            # Past the last page - and equally, a tag with no matches - the
            # upstream answers 404. That is an empty result, not a failure.
            if exc.status != 404:
                raise
            return PostPage(posts=(), page=page, has_more=False)

        posts = parse_listing(html)
        log.debug("%s page %d -> %d posts", self.name, page, len(posts))
        # A short page means we have walked off the end.
        has_more = len(posts) >= PAGE_SIZE
        return PostPage(posts=posts, page=page, has_more=has_more)

    def _url_for(self, tags: TagSet, page: int) -> str:
        """Build the list URL. Page 1 has no explicit segment."""
        query = tags.query()
        base = f"{self.base_url}/{quote(query, safe='+-_')}" if query else self.base_url
        return f"{base}/{page}" if page > 1 else base

    def info(self) -> SourceInfo:
        return SourceInfo(
            name=self.name,
            description="rule34.paheal.net HTML listing (no cookie needed)",
        )


class _ListingParser(HTMLParser):
    """Extracts post records from the thumbnail list markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.posts: list[Post] = []
        self._current: dict[str, str] | None = None
        self._depth = 0
        # Track the anchor currently being consumed so we can read its href.
        self._in_thumb = False
        self._pending_href: str | None = None

    # -- tag dispatch -----------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: (value or "") for key, value in attrs}
        if tag == "div" and "shm-thumb" in attr.get("class", "").split():
            self._current = {
                "id": attr.get("data-post-id", "").strip(),
                "ext": attr.get("data-ext", "").strip(),
                "tags": attr.get("data-tags", "").strip(),
                "width": "",
                "height": "",
                "size": "",
                "file_url": "",
            }
            self._depth = 1
            self._pending_href = None
            return
        if self._current is not None:
            if tag == "div":
                self._depth += 1
            if tag == "a":
                self._in_thumb = True
                self._pending_href = attr.get("href", "")
            if tag == "img" and "title" in attr:
                _apply_metadata(self._current, attr["title"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            # The "File Only" anchor immediately follows the thumb link; take
            # the first href that points at the media CDN.
            href = self._pending_href or ""
            if (
                self._current is not None
                and FILE_LINK_HOST in href
                and not self._current.get("file_url")
            ):
                self._current["file_url"] = href
            self._pending_href = None
            self._in_thumb = False
            return
        if self._current is not None and tag == "div":
            self._depth -= 1
            if self._depth == 0:
                self._finish()

    def _finish(self) -> None:
        assert self._current is not None
        record = self._current
        tags = tuple(part for part in record.get("tags", "").split() if part)
        post = Post(
            id=record.get("id", ""),
            file_url=record.get("file_url") or None,
            ext=record.get("ext") or None,
            width=_as_int(record.get("width")),
            height=_as_int(record.get("height")),
            size=_as_int(record.get("size")),
            tags=tags,
            source="paheal",
        )
        # Only keep entries we can actually act on.
        if post.id and post.file_url:
            self.posts.append(post)
        self._current = None


def _apply_metadata(record: dict[str, str], title: str) -> None:
    """Pull dimensions / size / extension out of a thumbnail title attribute."""
    match = _META_RE.search(title or "")
    if not match:
        return
    record["width"] = match.group("w")
    record["height"] = match.group("h")
    record["size"] = _parse_size(match.group("size"))
    # data-ext is authoritative, but the title is a useful fallback.
    record.setdefault("ext", match.group("ext"))


def _parse_size(text: str) -> str:
    match = _SIZE_RE.match(text)
    if not match:
        return ""
    value, unit = float(match.group(1)), match.group(2).upper()
    return str(int(value * _SIZE_UNITS.get(unit, 1)))


def _as_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    return int(text)


def parse_listing(html: str) -> tuple[Post, ...]:
    """Parse a listing page into normalised posts."""
    parser = _ListingParser()
    parser.feed(html)
    parser.close()
    return tuple(parser.posts)


__all__ = ["PahealHtmlSource", "parse_listing"]