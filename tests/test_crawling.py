"""Exercise real Scrapy scheduling, middleware, failures, and sequential worker runs."""

import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from scrapy.http import HtmlResponse

from research_tagger.crawling import SCRAPY_SETTINGS, crawl_conference, discover_conference
from research_tagger.web_models import ConferenceSelection
from scrapy_fixtures import fixture_pages

INDEX = "https://openaccess.thecvf.com/ICCV2025?day=all"
PAPER = "https://openaccess.thecvf.com/content/ICCV2025/html/one.html"
CONFIG = ConferenceSelection(name="ICCV", year=2025).to_config()
RECORD = {"title": "Synthetic paper", "source_url": PAPER}
HTML = "<div id='papertitle'>Synthetic paper</div><div id='abstract'>Synthetic abstract.</div>"


class CrawlingTests(unittest.TestCase):
    def test_iccv_index_and_repeated_worker_crawls(self):
        pages = {INDEX: f"<dt class='ptitle'><a href='{PAPER}'>Synthetic paper</a></dt>", PAPER: HTML}
        with fixture_pages(pages), ThreadPoolExecutor(max_workers=1) as worker:
            indexed = worker.submit(discover_conference, CONFIG).result()
            self.assertEqual(indexed, [RECORD])
            results = []
            for _ in range(2):
                worker.submit(crawl_conference, CONFIG, indexed, results.append).result()
        self.assertEqual([item["abstract"] for item in results], ["Synthetic abstract."] * 2)

    def test_http_and_title_errors_are_per_paper_without_retries(self):
        unavailable = {"title": "Unavailable", "source_url": PAPER + "-unavailable"}
        mismatch = {"title": "Different title", "source_url": PAPER + "-mismatch"}
        results = []
        with fixture_pages({PAPER: HTML, unavailable["source_url"]: HtmlResponse(unavailable["source_url"], status=503),
                            mismatch["source_url"]: HTML}) as requests:
            crawl_conference(CONFIG, [RECORD, unavailable, mismatch], results.append)
        by_url = {item["paper"]["source_url"]: item for item in results}
        self.assertEqual(by_url[PAPER]["abstract"], "Synthetic abstract.")
        self.assertIn("HTTP 503", by_url[unavailable["source_url"]]["error"])
        self.assertIn("does not match", by_url[mismatch["source_url"]]["error"])
        self.assertEqual(requests.count(unavailable["source_url"]), 1)

    def test_robots_denial_is_visible_and_page_not_downloaded(self):
        results = []
        with fixture_pages({PAPER: HTML, "https://openaccess.thecvf.com/robots.txt": "User-agent: *\nDisallow: /content/\n"}) as requests:
            crawl_conference(CONFIG, [RECORD], results.append)
        self.assertNotIn(PAPER, requests)
        self.assertIn("robots.txt", results[0]["error"])

    def test_redirect_destination_is_validated(self):
        destination = "https://private.example/paper"
        def validate(url):
            if url == destination:
                raise ValueError("Private destination rejected")
            return url
        results = []
        with fixture_pages({PAPER: HtmlResponse(PAPER, status=302, headers={"Location": destination})}) as requests, \
             patch("research_tagger.crawling.public_url", side_effect=validate) as validator:
            crawl_conference(CONFIG, [RECORD], results.append)
        validator.assert_any_call(destination)
        self.assertNotIn(destination, requests)
        self.assertIn("Private destination rejected", results[0]["error"])

    def test_cancelled_crawl_makes_no_requests(self):
        with fixture_pages({PAPER: HTML}) as requests:
            crawl_conference(CONFIG, [RECORD], lambda item: self.fail("Unexpected item"), lambda: True)
        self.assertEqual(requests, [])

    def test_result_consumer_error_is_propagated(self):
        def receive(item):
            raise ValueError("Database save failed")
        with fixture_pages({PAPER: HTML}), self.assertRaisesRegex(ValueError, "Database save failed"):
            crawl_conference(CONFIG, [RECORD], receive)

    def test_stop_after_first_result_leaves_remaining_papers_unfailed(self):
        records = [{**RECORD, "source_url": PAPER + str(index)} for index in range(10)]
        results = []
        with fixture_pages({record["source_url"]: HTML for record in records}) as requests, \
             patch.dict(SCRAPY_SETTINGS, {"DOWNLOAD_DELAY": .02}):
            crawl_conference(CONFIG, records, results.append, lambda: bool(results))
        self.assertTrue(results)
        self.assertTrue(all("abstract" in result and "error" not in result for result in results))
        self.assertLess(len([url for url in requests if not url.endswith("/robots.txt")]), len(records))

    def test_conflicting_index_titles_fail_discovery(self):
        html = f"<dt class='ptitle'><a href='{PAPER}'>First</a><a href='{PAPER}'>Second</a></dt>"
        with fixture_pages({INDEX: html}), self.assertRaisesRegex(ValueError, "Conflicting titles"):
            discover_conference(CONFIG)
