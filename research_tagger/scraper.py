"""Scrapy-backed ECCV corpus collection for the CLI, without downloading PDFs."""

from scrapy.http import HtmlResponse

from .conferences import required_text
from .crawling import CrawlResult, crawl_conference, discover_conference
from .models import Paper
from .web_models import ConferenceSelection

def parse_paper(html: str, source_url: str) -> Paper:
    """Normalize visible title/abstract text; reject missing or numeric placeholders."""
    response = HtmlResponse(source_url, body=html, encoding="utf-8")
    return Paper(source_url=source_url, title=required_text(response, "h1.event-title", "title"),
                 abstract=required_text(response, "#abstractText .abstract-text-inner", "abstract"))


def scrape_eccv(year: int, limit: int | None = None) -> list[Paper]:
    """Return a complete edition or explicit positive sample in index order.

    Scrapy owns requests and rate limits. Any failed paper aborts the CLI corpus;
    the web worker instead persists per-paper successes and failures for resuming.
    This synchronous function must run outside an active asyncio event loop.
    """
    if limit is not None and limit < 1:
        raise ValueError("The sample limit must be positive.")
    config = ConferenceSelection(name="ECCV", year=year).to_config()
    indexed = discover_conference(config)[:limit]
    saved = {}

    def receive(result: CrawlResult) -> None:
        if "error" in result:
            raise ValueError(result["error"])
        paper = result["paper"]
        saved[paper["source_url"]] = Paper(**paper, abstract=result["abstract"])

    crawl_conference(config, indexed, receive)
    if len(saved) != len(indexed):
        raise ValueError("Scrapy did not return every requested paper.")
    return [saved[paper["source_url"]] for paper in indexed]
