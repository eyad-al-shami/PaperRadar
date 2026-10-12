"""NeurIPS archive resolution and Scrapy collection without PDF downloads."""

import unittest

from research_tagger.crawling import crawl_conference, discover_conference
from research_tagger.web_models import ConferenceSelection
from scrapy_fixtures import fixture_pages

ARCHIVE = "https://proceedings.neurips.cc/"
HASH = "a" * 32


def fixtures(year=2025, suffix="-Conference"):
    """Model the current split main volume and older single-volume archive formats."""
    index = f"{ARCHIVE}paper_files/paper/{year}" + ("/vol38-main-conference" if year == 2025 else "")
    paper = f"{ARCHIVE}paper_files/paper/{year}/hash/{HASH}-Abstract{suffix}.html"
    pdf = paper.replace('/hash/', '/file/').replace('-Abstract', '-Paper').replace('.html', '.pdf')
    archive = f'<ul class="proceedings-list"><li><a href="{index}">Main proceedings</a></li>'
    if year == 2025:
        archive += f'<li><a href="{ARCHIVE}paper_files/paper/2025">Creative AI</a></li>'
    pages = {
        ARCHIVE: archive + '</ul>',
        index: f'<ul class="paper-list"><li><a title="paper title" href="{paper}">Test paper</a></li></ul>',
        paper: '<h1 class="paper-title">Test paper</h1>'
               '<section class="paper-section"><h2>Abstract</h2><p class="paper-abstract"><p>Actual abstract.</p></p></section>'
               f'<div class="paper-actions"><a href="{pdf}">Paper</a><a href="supplement.pdf">Supplemental</a></div>',
    }
    return index, paper, pdf, pages


class NeuripsTests(unittest.TestCase):
    def test_discovery_and_scrape_across_years_and_tracks(self):
        for year, suffix in ((2025, '-Conference'), (2025, '-Datasets_and_Benchmarks_Track'),
                             (2024, '-Conference'), (2000, '')):
            with self.subTest(year=year, suffix=suffix):
                config = ConferenceSelection(name="NeurIPS", year=year).to_config()
                index, paper, pdf, pages = fixtures(year, suffix)
                results = []
                with fixture_pages(pages) as requests:
                    papers = discover_conference(config)
                    crawl_conference(config, papers, results.append)
                self.assertEqual(papers, [{"title": "Test paper", "source_url": paper}])
                self.assertEqual(results[0]['abstract'], 'Actual abstract.')
                self.assertEqual(results[0]['pdf_url'], pdf)
                self.assertEqual(set(requests), {ARCHIVE, ARCHIVE + 'robots.txt', index, paper})

    def test_missing_or_ambiguous_edition_fails_without_switching_years(self):
        _, _, _, pages = fixtures()
        config = ConferenceSelection(name="NeurIPS", year=2024).to_config()
        with fixture_pages(pages), self.assertRaisesRegex(ValueError, 'Expected one main NeurIPS 2024'):
            discover_conference(config)
        config = ConferenceSelection(name="NeurIPS", year=2025).to_config()
        pages[ARCHIVE] = pages[ARCHIVE].replace('Creative AI', 'Another main volume')
        with fixture_pages(pages), self.assertRaisesRegex(ValueError, 'Expected one main NeurIPS 2025'):
            discover_conference(config)

    def test_bad_index_emits_no_partial_results(self):
        config = ConferenceSelection(name="NeurIPS", year=2025).to_config()
        index, paper, _, pages = fixtures()
        original = pages[index]
        for bad in ('<ul class="paper-list"></ul>', original.replace('/2025/hash/', '/2024/hash/'),
                    original.replace(paper, paper.replace('proceedings.neurips.cc', 'other.example')),
                    original.replace('Test paper', '123'), original.replace('title="paper title"', '')):
            results = []
            with self.subTest(html=bad), fixture_pages({**pages, index: bad}), self.assertRaises(ValueError):
                crawl_conference(config, None, results.append)
            self.assertEqual(results, [])

    def test_bad_pdf_preserves_abstract_and_title_mismatch_fails(self):
        config = ConferenceSelection(name="NeurIPS", year=2025).to_config()
        _, paper, pdf, pages = fixtures()
        record = {"title": "Test paper", "source_url": paper}
        for target in (pdf.replace('/2025/', '/2024/'), pdf.replace(HASH, 'b' * 32), 'https://other.example/paper.pdf'):
            results = []
            with fixture_pages({paper: pages[paper].replace(pdf, target)}):
                crawl_conference(config, [record], results.append)
            self.assertEqual(results[0]['abstract'], 'Actual abstract.')
            self.assertIn('does not match', results[0]['pdf_error'])
            self.assertNotIn('pdf_url', results[0])
        results = []
        with fixture_pages({paper: pages[paper].replace('Test paper', 'Different title')}):
            crawl_conference(config, [record], results.append)
        self.assertIn('does not match', results[0]['error'])
        self.assertIn('does not match', results[0]['pdf_error'])
