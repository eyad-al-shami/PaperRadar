"""Official HTML discovery works while static feeds remain disallowed by robots."""

import unittest

from scrapy.http import HtmlResponse

from research_tagger.crawling import crawl_conference, discover_conference
from research_tagger.web_models import ConferenceSelection
from scrapy_fixtures import fixture_pages


def index_html(entries: str) -> str:
    """Wrap synthetic list entries in the official server-rendered paper-list markup."""
    return f'<main><noscript class="noscript"><ul>{entries}</ul></noscript></main>'


def entry(identifier=1, year=2026):
    """Create one synthetic poster link; repeated IDs model duplicated links."""
    return f'<li><a href="/virtual/{year}/poster/{identifier}">Synthetic paper {identifier}</a></li>'


class VirtualConferenceTests(unittest.TestCase):
    def setUp(self):
        self.config = ConferenceSelection(name="ECCV", year=2026).to_config()
        self.index = str(self.config.index_url)

    def test_three_providers_work_with_static_feeds_blocked(self):
        for name, host in (("ICML", "icml.cc"), ("ECCV", "eccv.ecva.net"),
                           ("ICLR", "iclr.cc")):
            with self.subTest(name=name):
                config = ConferenceSelection(name=name, year=2026).to_config()
                paper_url = f"https://{host}/virtual/2026/poster/1"
                # Real pages advertise JSON too; discovery must not request it.
                html = index_html(entry()) + f'<script>start("/static/virtual/data/{name.lower()}-2026-orals-posters.json")</script>'
                pages = {str(config.index_url): html,
                         f"https://{host}/robots.txt": "User-agent: *\nDisallow: /static\n",
                         paper_url: '<h1 class="event-title">Synthetic paper 1</h1>'
                                    '<div id="laySummaryText"><div class="abstract-text-inner">Lay summary.</div></div>'
                                    '<div id="abstractText"><div class="abstract-text-inner">Actual abstract.</div></div>'}
                results = []
                with fixture_pages(pages) as requested:
                    papers = discover_conference(config)
                    crawl_conference(config, papers, results.append)
                self.assertEqual(papers, [{"title": "Synthetic paper 1", "source_url": paper_url}])
                self.assertEqual(results[0]["abstract"], "Actual abstract.")
                self.assertTrue(all("/static/" not in url and "/api/" not in url for url in requested))

    def test_entire_list_over_200_papers_is_read_once(self):
        html = index_html("".join(entry(identifier) for identifier in range(1, 251)))
        with fixture_pages({self.index: html}) as requested:
            papers = discover_conference(self.config)
        self.assertEqual(len(papers), 250)
        self.assertEqual(papers[-1]["title"], "Synthetic paper 250")
        self.assertEqual(requested.count(self.index), 1)

    def test_navigation_and_presentations_outside_list_are_excluded(self):
        html = '<nav><a href="/virtual/2026/poster/999">Navigation</a><a href="/virtual/2026/oral/1">Oral</a></nav>'
        html += index_html(entry() + entry() + entry(2))
        with fixture_pages({self.index: html}):
            papers = discover_conference(self.config)
        self.assertEqual([paper["title"] for paper in papers], ["Synthetic paper 1", "Synthetic paper 2"])

    def test_malformed_lists_emit_no_partial_index(self):
        cases = [
            ("<main>Coming soon</main>", "nonempty paper list"),
            (index_html(""), "nonempty paper list"),
            (index_html(entry()) * 2, "nonempty paper list"),
            (index_html(entry() + entry(year=2024)), "different edition"),
            (index_html(entry().replace('/virtual/2026/poster/1', 'https://other.example/virtual/2026/poster/1')), "invalid paper URL"),
            (index_html(entry().replace('/poster/', '/oral/')), "invalid paper URL"),
            (index_html(entry() + '<li>No link</li>'), "exactly one paper link"),
            (index_html(entry() + '<li><a href="/virtual/2026/poster/2">123</a></li>'), "Invalid indexed title"),
            (index_html(entry() + entry().replace('Synthetic paper 1', 'Conflicting title')), "Conflicting titles"),
        ]
        for html, error in cases:
            results = []
            with self.subTest(error=error), fixture_pages({self.index: html}), self.assertRaisesRegex(ValueError, error):
                crawl_conference(self.config, None, results.append)
            self.assertEqual(results, [])

    def test_http_error_emits_no_index(self):
        results = []
        with fixture_pages({self.index: HtmlResponse(self.index, status=403)}), self.assertRaisesRegex(ValueError, "HTTP 403"):
            crawl_conference(self.config, None, results.append)
        self.assertEqual(results, [])

    def test_redirect_to_another_edition_fails(self):
        destination = self.index.replace("2026", "2024")
        pages = {self.index: HtmlResponse(self.index, status=302, headers={"Location": destination}), destination: index_html(entry())}
        with fixture_pages(pages), self.assertRaisesRegex(ValueError, "different edition"):
            discover_conference(self.config)
