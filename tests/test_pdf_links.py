"""Official PDF-link extraction uses HTML only and preserves paper identity."""

import unittest

from research_tagger.crawling import crawl_conference, discover_conference
from research_tagger.web_models import ConferenceSelection
from scrapy_fixtures import fixture_pages


def poster(title: str, links: str) -> str:
    """Create a title/abstract page with explicit resource buttons."""
    return (f'<h1 class="event-title">{title}</h1>'
            '<div id="abstractText"><div class="abstract-text-inner">Saved abstract.</div></div>' + links)


class PdfLinksTests(unittest.TestCase):
    def test_virtual_buttons_collect_urls_without_fetching_files_or_openreview(self):
        for name, link, expected in (
            ("ECCV", '<a href="https://media.eventhosts.cc/paper.pdf">Paper PDF</a>',
             "https://media.eventhosts.cc/paper.pdf"),
            ("ICML", '<a href="https://openreview.net/forum?id=abc_123&amp;noteId=review">OpenReview</a>',
             "https://openreview.net/pdf?id=abc_123"),
            ("ICLR", '<a href="https://openreview.net/forum?id=abc_123">OpenReview</a>',
             "https://openreview.net/pdf?id=abc_123"),
        ):
            with self.subTest(conference=name):
                config = ConferenceSelection(name=name, year=2026).to_config()
                url = f"https://{config.index_url.host}/virtual/2026/poster/1"
                paper = {"title": "Test paper", "source_url": url}
                for links_only in (False, True):
                    results = []
                    with fixture_pages({url: poster(paper["title"], link)}) as requested:
                        crawl_conference(config, [paper], results.append, links_only=links_only)
                    self.assertEqual(results[0]["pdf_url"], expected)
                    self.assertEqual("abstract" in results[0], not links_only)
                    self.assertEqual(set(requested), {url, f"https://{config.index_url.host}/robots.txt"})

    def test_cvpr_reads_cvf_abstract_and_pdf_without_fetching_file(self):
        config = ConferenceSelection(name="CVPR", year=2026).to_config()
        url = "https://openaccess.thecvf.com/content/CVPR2026/html/Test.html"
        pdf = "https://openaccess.thecvf.com/content/CVPR2026/papers/Test.pdf"
        record = {"source_url": url, "title": "Test paper"}
        pages = {url: '<div id="papertitle">Test paper</div><div id="abstract">Saved abstract.</div>'
                      f'<a href="{pdf}">pdf</a><a href="supp.pdf">supp</a>'}
        results = []
        with fixture_pages(pages) as requested:
            crawl_conference(config, [record], results.append)
        self.assertEqual(results[0]["pdf_url"], pdf)
        self.assertEqual(results[0]["abstract"], "Saved abstract.")
        self.assertEqual(set(requested), {url, "https://openaccess.thecvf.com/robots.txt"})
        pages[url] = pages[url].replace("Test paper", "Wrong paper")
        results = []
        with fixture_pages(pages):
            crawl_conference(config, [record], results.append)
        self.assertNotIn("pdf_url", results[0])
        self.assertIn("does not match", results[0]["pdf_error"])
        self.assertIn("does not match", results[0]["error"])

    def test_cvf_index_scopes_pdf_links_to_each_title_and_edition(self):
        for conference, year in (("ICCV", 2025), ("CVPR", 2025), ("CVPR", 2026)):
            with self.subTest(conference=conference, year=year):
                config = ConferenceSelection(name=conference, year=year).to_config()
                html = '<dl>'
                for name in ("First", "Missing", "Last"):
                    html += f'<dt class="ptitle"><a href="/content/{conference}{year}/html/{name}.html">{name} paper</a></dt><dd>Authors</dd>'
                    if name != "Missing":
                        html += f'<dd>[<a href="/content/{conference}{year}/papers/{name}.pdf">pdf</a>] [<a href="supp.pdf">supp</a>]</dd>'
                html += '<dt class="ptitle"><a href="/content/CVPR2024/html/Old.html">Wrong edition</a></dt></dl>'
                with fixture_pages({str(config.index_url): html}) as requested:
                    papers = discover_conference(config)
                self.assertEqual(len(papers), 3)
                self.assertTrue(papers[0]["pdf_url"].endswith("/First.pdf"))
                self.assertNotIn("pdf_url", papers[1])
                self.assertTrue(papers[2]["pdf_url"].endswith("/Last.pdf"))
                self.assertFalse(any(url.endswith(".pdf") for url in requested))
                wrong_pdf = html.replace(f'/content/{conference}{year}/papers/First.pdf', '/content/CVPR2024/papers/First.pdf')
                with fixture_pages({str(config.index_url): wrong_pdf}):
                    papers = discover_conference(config)
                self.assertNotIn("pdf_url", papers[0])
                self.assertIn("does not belong", papers[0]["pdf_error"])

    def test_bad_links_are_errors_without_losing_the_abstract(self):
        config = ConferenceSelection(name="ECCV", year=2026).to_config()
        url = "https://eccv.ecva.net/virtual/2026/poster/1"
        record = {"title": "Test paper", "source_url": url}
        for links in (
            '', '<a href="javascript:alert(1)">Paper PDF</a>',
            '<a href="https://example.test/a.pdf">pdf</a><a href="https://example.test/b.pdf">pdf</a>',
            '<a href="https://openreview.net/forum">OpenReview</a>',
            '<a href="https://example.test/project">Paper PDF</a>',
        ):
            with self.subTest(links=links):
                results = []
                with fixture_pages({url: poster(record["title"], links)}):
                    crawl_conference(config, [record], results.append)
                self.assertEqual(results[0]["abstract"], "Saved abstract.")
                self.assertTrue(results[0]["pdf_error"])
                self.assertNotIn("pdf_url", results[0])
