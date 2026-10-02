"""Shared test fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Allow tests to import the sibling ``helpers`` module.
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


#: A trimmed, verbatim excerpt of the real rule34.paheal.net listing markup.
LISTING_HTML = """
<html><body>
<section id='image-list'><div class='blockbody'>
<div class='shm-image-list ' data-query='#search=cute'>
  <div class='shm-thumb thumb' data-ext='png' data-tags='1girl cute youtube'
       data-post-id='7451852'>
    <a class='shm-thumb-link' href='/post/view/7451852'>
      <img id='thumb_7451852' height='144' width='192'
           src='https://r34t.paheal.net/89/e1/89e109021ec0a151f3fbe17d767b2527'
           title='1girl Cute Egilea_ASMR YouTube
4000x3000 // 1.4MB // png
September 19, 2026; 20:30' /></a><br />
    <a href='https://r34i.paheal-cdn.net/89/e1/89e109021ec0a151f3fbe17d767b2527'>File Only</a>
  </div>
  <div class='shm-thumb thumb' data-ext='jpg'
       data-tags='cute edit mechafetus screenshot_edit'
       data-post-id='4663507'>
    <a class='shm-thumb-link' href='/post/view/4663507'>
      <img id='thumb_4663507' height='144' width='192'
           src='https://r34t.paheal.net/eb/62/eb6212affd0dbac2745034bcbe6aa126'
           title='Cute edit Mechafetus
1512x855 // 662KB // jpg
October 31, 2021; 13:54' /></a><br />
    <a href='https://r34i.paheal-cdn.net/eb/62/eb6212affd0dbac2745034bcbe6aa126'>File Only</a>
  </div>
  <div class='shm-thumb thumb' data-ext='png' data-tags='banned_tag' data-post-id='999'>
    <a class='shm-thumb-link' href='/post/view/999'></a>
  </div>
</div>
</div></section>
</body></html>
"""

EMPTY_LISTING_HTML = """
<html><body><section id='image-list'><div class='blockbody'>
<div class='shm-image-list'></div>
</div></section>
<div>Nothing found</div></body></html>
"""

CAPTCHA_HTML = """<html><head><title>Rule34.xxx CAPTCHA</title>
<meta http-equiv="refresh" content="360"></head><body>
<div class="captcha-container">Please prove you are human</div>
</body></html>"""


@pytest.fixture
def listing_html() -> str:
    return LISTING_HTML


@pytest.fixture
def empty_listing_html() -> str:
    return EMPTY_LISTING_HTML


@pytest.fixture
def captcha_html() -> str:
    return CAPTCHA_HTML