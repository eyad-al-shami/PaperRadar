"""Offline download handler: run the real Scrapy engine against explicit HTML fixtures."""

from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import urlsplit

from scrapy.http import HtmlResponse

from research_tagger.crawling import SCRAPY_SETTINGS


class FixtureDownloadHandler:
    """Serve only supplied pages; an unexpected network destination fails the test crawl."""

    lazy = True
    pages = {}
    requests = []

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    async def download_request(self, request):
        self.requests.append(request.url)
        response = self.pages[request.url]
        if isinstance(response, str):
            response = HtmlResponse(request.url, body=response, encoding="utf-8")
        return response.replace(request=request)

    async def close(self):
        pass


@contextmanager
def fixture_pages(pages: dict):
    """Provide HTML/HtmlResponse values plus permissive robots files; yield requested URLs."""
    FixtureDownloadHandler.pages = dict(pages)
    FixtureDownloadHandler.requests = []
    for url in pages:
        host = urlsplit(url).netloc
        FixtureDownloadHandler.pages.setdefault(f"https://{host}/robots.txt", "User-agent: *\nAllow: /\n")
    with patch.dict(SCRAPY_SETTINGS, {"DOWNLOAD_HANDLERS": {"https": FixtureDownloadHandler, "http": FixtureDownloadHandler},
                                     "DOWNLOAD_DELAY": 0}), \
         patch("research_tagger.crawling.public_url", side_effect=lambda url: url):
        yield FixtureDownloadHandler.requests
