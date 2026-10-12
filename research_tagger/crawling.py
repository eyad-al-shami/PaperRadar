"""Scrapy conference crawling shared by the synchronous CLI and job worker."""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Literal, TypedDict

import scrapy
from scrapy import signals
from scrapy.crawler import AsyncCrawlerRunner, Crawler
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse
from twisted.python.failure import Failure

from .conferences import is_cvf_paper_page, parse_abstract, parse_index, parse_pdf_link, parse_virtual_index, proceedings_url, parse_neurips_index
from .network import USER_AGENT, public_url
from .web_models import ConferenceConfig

SCRAPY_SETTINGS = {
    "TWISTED_REACTOR_ENABLED": False,
    "USER_AGENT": USER_AGENT,
    "CONCURRENT_REQUESTS": 1,
    "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    "CONCURRENT_ITEMS": 1,
    "DOWNLOAD_DELAY": 0.5,
    "DOWNLOAD_DELAY_JITTER": 0,
    "DOWNLOAD_TIMEOUT": 45,
    "DOWNLOAD_MAXSIZE": 16_000_000,
    "REDIRECT_MAX_TIMES": 5,
    "RETRY_ENABLED": False,
    "COOKIES_ENABLED": False,
    "TELNETCONSOLE_ENABLED": False,
    "LOG_ENABLED": False,
    "ROBOTSTXT_OBEY": True,
    "DOWNLOADER_MIDDLEWARES": {"research_tagger.crawling.PublicSourceMiddleware": 50},
}


class CrawlResult(TypedDict, total=False):
    """Validated index or abstract result; error reports failed requests or content extraction."""

    kind: Literal["index", "abstract", "pdf_links"]
    papers: list[dict]
    paper: dict
    abstract: str
    pdf_url: str
    pdf_error: str
    error: str


class PublicSourceMiddleware:
    """Validate public HTTPS destinations, including redirects, and stop cancelled work."""

    def __init__(self, crawler: Crawler):
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "PublicSourceMiddleware":
        """Use the active spider's cancellation predicate for every download."""
        return cls(crawler)

    async def process_request(self, request: scrapy.Request) -> None:
        """Reject cancelled requests before DNS validation or network transfer."""
        if self.crawler.spider.should_stop():
            raise IgnoreRequest("Crawl cancelled.")
        await asyncio.to_thread(public_url, request.url)


class ConferenceSpider(scrapy.Spider):
    """Discover a complete official index or read conference abstracts."""

    name = "conference"

    def __init__(self, config: ConferenceConfig, papers: list[dict] | None,
                 should_stop: Callable[[], bool], links_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        self.papers = papers
        self.should_stop = should_stop
        self.links_only = links_only

    async def start(self) -> AsyncIterator[scrapy.Request]:
        """Schedule the index or selected papers; Scrapy owns throttling and downloads."""
        if self.papers is None:
            if not self.should_stop():
                yield scrapy.Request(str(self.config.index_url), callback=self.archive if self.config.source == "neurips" else self.index, errback=self.failed)
        else:
            for paper in self.papers:
                if self.should_stop():
                    break
                yield scrapy.Request(paper["source_url"], callback=self.abstract,
                                     cb_kwargs={"paper": paper}, errback=self.failed)

    def archive(self, response: HtmlResponse) -> scrapy.Request:
        """Resolve the selected NeurIPS main volume from the archive before discovery."""
        url = proceedings_url(response, self.config)
        return scrapy.Request(url, callback=self.index, errback=self.failed)

    def index(self, response: HtmlResponse) -> CrawlResult:
        """Read the full paper list directly from the official edition's HTML."""
        parser = {"virtual": parse_virtual_index, "cvf": parse_index, "neurips": parse_neurips_index}[self.config.source]
        return {"kind": "index", "papers": parser(response, self.config)}

    def abstract(self, response: HtmlResponse, paper: dict) -> CrawlResult | scrapy.Request:
        """Read a paper's metadata and PDF URL; only CVF HTML links are followed."""
        result = {"kind": "pdf_links" if self.links_only else "abstract", "paper": paper}
        if not self.links_only and not paper.get("abstract"):
            try:
                result["abstract"] = parse_abstract(response, self.config, paper)
            except ValueError as error:
                result["error"] = str(error)
        if paper.get("pdf_url"):
            return result
        try:
            url = parse_pdf_link(response, self.config, paper)
            if is_cvf_paper_page(url, self.config):
                return scrapy.Request(url, callback=self.pdf_page, errback=self.failed,
                                      cb_kwargs={"paper": paper, "result": result})
            result["pdf_url"] = url
        except ValueError as error:
            result["pdf_error"] = str(error)
        return result

    def pdf_page(self, response: HtmlResponse, paper: dict, result: CrawlResult) -> CrawlResult:
        """Extract the explicit pdf link from a title-verified CVF HTML page."""
        try:
            result["pdf_url"] = parse_pdf_link(response, self.config, paper, proceedings=True)
        except ValueError as error:
            result["pdf_error"] = str(error)
        return result

    def failed(self, failure: Failure) -> CrawlResult | None:
        """Surface download/HTTP failures without retries; cancellation is not a failure."""
        if self.should_stop():
            return None
        request = failure.request
        message = f"{request.url}: {failure.getErrorMessage()}"
        response = getattr(failure.value, "response", None)
        if response is not None:
            message = f"HTTP {response.status}: {message}"
        if "paper" not in request.cb_kwargs:
            raise ValueError(message)
        if "result" in request.cb_kwargs:
            return {**request.cb_kwargs["result"], "pdf_error": message}
        if self.links_only:
            return {"kind": "pdf_links", "paper": request.cb_kwargs["paper"], "pdf_error": message}
        paper = request.cb_kwargs["paper"]
        return {"kind": "abstract", "paper": paper, "error": message,
                **({"pdf_error": message} if not paper.get("pdf_url") else {})}


def crawl_conference(config: ConferenceConfig, papers: list[dict] | None,
                     on_result: Callable[[CrawlResult], None],
                     cancelled: Callable[[], bool] = lambda: False, *, links_only: bool = False) -> None:
    """Run Scrapy to completion in a synchronous worker, emitting results as they arrive.

    None selects index discovery; a list selects abstract downloads. Consumer and
    spider errors propagate. Each call owns an asyncio loop, so successive jobs do
    not attempt to restart Twisted's process-global reactor. Do not call on an
    already-running event loop; the web app invokes this in its background worker.
    links_only reads PDF URLs for existing papers without changing saved text.
    """
    if papers is not None and len({paper["source_url"] for paper in papers}) != len(papers):
        raise ValueError("A crawl cannot contain duplicate paper URLs.")
    errors = []

    async def run() -> None:
        runner = AsyncCrawlerRunner(settings=SCRAPY_SETTINGS)
        crawler = runner.create_crawler(ConferenceSpider)

        def receive(item, **kwargs):
            if errors:
                return
            try:
                on_result(item)
            except Exception as error:
                errors.append(error)

        def failed(failure, **kwargs):
            errors.append(failure.value)

        crawler.signals.connect(receive, signal=signals.item_scraped)
        crawler.signals.connect(failed, signal=signals.spider_error)
        await runner.crawl(crawler, config=config, papers=papers, links_only=links_only,
                           should_stop=lambda: bool(errors) or cancelled())

    asyncio.run(run())
    if errors:
        raise errors[0]


def discover_conference(config: ConferenceConfig,
                        cancelled: Callable[[], bool] = lambda: False) -> list[dict]:
    """Return one complete index, or an empty list only on explicit cancellation."""
    results = []
    crawl_conference(config, None, results.append, cancelled)
    if cancelled():
        return []
    if len(results) != 1 or results[0]["kind"] != "index":
        raise ValueError("Scrapy did not return a complete conference index.")
    return results[0]["papers"]
