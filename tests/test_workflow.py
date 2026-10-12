"""Offline tests use synthetic paper text and explicitly mocked Jev responses."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from research_tagger.classifier import API_URL, classify_paper
from research_tagger.cli import main, save, show_results
from research_tagger.models import Corpus, Domain, TaggedCorpus, validate_domains
from research_tagger.scraper import parse_paper, scrape_eccv
from research_tagger.conferences import parse_virtual_index
from research_tagger.web_models import ConferenceSelection
from scrapy.http import HtmlResponse
from scrapy_fixtures import fixture_pages

INDEX_URL = "https://eccv.ecva.net/virtual/2024/papers.html"
PAPER_URL = "https://eccv.ecva.net/virtual/2024/poster/4"
PAPER_HTML = """<h1 class="event-title">Synthetic <i>test</i> paper</h1>
<div id="abstractText"><div class="abstract-text-inner">Synthetic abstract for testing only.
 No real research claims.</div></div>"""
INDEX_HTML = f'<main><noscript class="noscript"><ul><li><a href="{PAPER_URL}">Synthetic test paper</a></li></ul></noscript></main>'
DOMAINS = [
    Domain(id="first", label="First test domain", description="Synthetic first rubric."),
    Domain(id="second", label="Second test domain", description="Synthetic second rubric."),
]


def answer(first: float = 0.9, second: float = 0.8) -> dict:
    """Return a synthetic API response, never an inference on an actual paper."""
    return {
        "model": "jev-test-version",
        "answers": {
            "first": {"type": "noul", "noul": first},
            "second": {"type": "noul", "noul": second},
        },
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }


class ScraperTests(unittest.TestCase):
    def test_exact_edition_poster_list_is_read(self):
        config = ConferenceSelection(name="ECCV", year=2024).to_config()
        response = HtmlResponse(INDEX_URL, body=INDEX_HTML, encoding="utf-8")
        self.assertEqual(parse_virtual_index(response, config), [{"source_url": PAPER_URL, "title": "Synthetic test paper"}])

    def test_absent_edition_fails(self):
        config = ConferenceSelection(name="ECCV", year=2026).to_config()
        response = HtmlResponse(str(config.index_url), body=INDEX_HTML, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different edition"):
            parse_virtual_index(response, config)

    def test_visible_text_only(self):
        paper = parse_paper(PAPER_HTML, PAPER_URL)
        self.assertEqual(paper.title, "Synthetic test paper")
        self.assertEqual(paper.abstract, "Synthetic abstract for testing only. No real research claims.")

    def test_missing_blank_or_placeholder_abstract_fails(self):
        for content in ("", '<div id="abstractText"><div class="abstract-text-inner"></div></div>', '<div id="abstractText"><div class="abstract-text-inner">473</div></div>'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_paper('<h1 class="event-title">Test</h1>' + content, PAPER_URL)

    def test_only_fetches_index_and_abstract_pages(self):
        with fixture_pages({INDEX_URL: INDEX_HTML, PAPER_URL: PAPER_HTML}) as seen:
            papers = scrape_eccv(2024)
        self.assertEqual([url for url in seen if not url.endswith("/robots.txt")], [INDEX_URL, PAPER_URL])
        self.assertEqual(len(papers), 1)

    def test_http_error_stops_scrape(self):
        with fixture_pages({INDEX_URL: HtmlResponse(INDEX_URL, status=503)}) as seen:
            with self.assertRaises(ValueError):
                scrape_eccv(2024)
        self.assertEqual(seen.count(INDEX_URL), 1)

    def test_invalid_limit_fails_before_network(self):
        with patch("research_tagger.scraper.discover_conference", side_effect=AssertionError("Unexpected request")):
            with self.assertRaises(ValueError):
                scrape_eccv(2024, limit=0)


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.paper = parse_paper(PAPER_HTML, PAPER_URL)

    def classify(self, payload):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
            return classify_paper(client, self.paper, DOMAINS, "test-key")

    def test_request_contains_only_title_abstract_and_all_domain_questions(self):
        def handler(request):
            self.assertEqual(str(request.url), API_URL)
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            payload = json.loads(request.content)
            self.assertEqual(payload["state"], {"title": self.paper.title, "abstract": self.paper.abstract})
            self.assertEqual(set(payload["questions"]), {"first", "second"})
            self.assertEqual(payload["questions"]["first"]["type"], "noul")
            self.assertEqual(payload["questions"]["first"]["instructions"]["domain"], DOMAINS[0].label)
            self.assertEqual(payload["questions"]["first"]["instructions"]["definition"], DOMAINS[0].description)
            for question in payload["questions"].values():
                self.assertNotIn("criteria", question)
            return httpx.Response(200, json=answer())

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = classify_paper(client, self.paper, DOMAINS, "test-key")
        self.assertEqual(result.probabilities, {"first": 0.9, "second": 0.8})
        self.assertEqual(result.model, "jev-test-version")

    def test_missing_or_extra_answers_fail(self):
        for key in ("first", "unexpected"):
            payload = answer()
            if key == "first":
                del payload["answers"][key]
            else:
                payload["answers"][key] = {"type": "noul", "noul": 0.3}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "mismatched domain"):
                self.classify(payload)

    def test_invalid_probabilities_and_answer_types_fail(self):
        for value in (-0.1, 1.1, "0.9", True, None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.classify(answer(first=value))
        payload = answer()
        payload["answers"]["first"]["type"] = "choice"
        with self.assertRaises(ValidationError):
            self.classify(payload)

    def test_nonfinite_probability_fails(self):
        from research_tagger.classifier import JevResponse

        with self.assertRaises(ValidationError):
            JevResponse.model_validate(answer(first=float("nan")))

    def test_empty_key_and_bad_taxonomies_fail_before_network(self):
        for domains, key in ((DOMAINS, ""), ([], "test-key"), ([DOMAINS[0], DOMAINS[0]], "test-key")):
            with httpx.Client(transport=httpx.MockTransport(lambda _: self.fail("Unexpected request"))) as client:
                with self.subTest(domains=domains, key=key), self.assertRaises(ValueError):
                    classify_paper(client, self.paper, domains, key)

    def test_authentication_error_does_not_create_tags(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401))) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                classify_paper(client, self.paper, DOMAINS, "test-key")

    def test_grouping_includes_multiple_tags_and_untagged_papers(self):
        tagged = self.classify(answer(first=0.7, second=0.8))
        untagged = self.classify(answer(first=0.4, second=0.2)).model_copy(update={
            "source_url": "https://eccv.ecva.net/virtual/2024/poster/5", "title": "Synthetic unmatched paper",
        })
        run = TaggedCorpus(year=2024, papers=[tagged, untagged], domains=DOMAINS, threshold=0.7)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            show_results(run)
        self.assertIn("First test domain (1)", output.getvalue())
        self.assertIn("Second test domain (1)", output.getvalue())
        self.assertIn("No domain above threshold (1)", output.getvalue())
        self.assertEqual(output.getvalue().count(tagged.title), 2)
        with self.assertRaisesRegex(ValueError, "Unknown domain"):
            show_results(run, "unknown")


class WorkflowTests(unittest.TestCase):
    def test_scrape_classify_save_and_show(self):
        def handler(request):
            if str(request.url) == INDEX_URL:
                return httpx.Response(200, text=INDEX_HTML)
            if str(request.url) == PAPER_URL:
                return httpx.Response(200, text=PAPER_HTML)
            if str(request.url) == API_URL:
                return httpx.Response(200, json=answer())
            self.fail(f"Unexpected request: {request.url}")

        real_client = httpx.Client

        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers = root / "papers.json"
            results = root / "results.json"
            domains = root / "domains.json"
            domains.write_text(json.dumps([domain.model_dump() for domain in DOMAINS]))
            with patch("research_tagger.cli.httpx.Client", side_effect=client_factory), \
                 fixture_pages({INDEX_URL: INDEX_HTML, PAPER_URL: PAPER_HTML}), \
                 patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["scrape", "--year", "2024", "--output", str(papers)]), 0)
                self.assertEqual(main(["classify", "--papers", str(papers), "--domains", str(domains), "--output", str(results)]), 0)
                self.assertEqual(main(["show", str(results), "--domain", "first"]), 0)
            run = TaggedCorpus.model_validate_json(results.read_text())
            self.assertEqual(len(run.papers), 1)
            self.assertEqual(run.papers[0].probabilities, {"first": 0.9, "second": 0.8})
            self.assertNotIn("test-key", results.read_text())

    def test_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "papers.json"
            output.write_text("existing content")
            corpus = Corpus(year=2024, papers=[parse_paper(PAPER_HTML, PAPER_URL)])
            with self.assertRaises(FileExistsError):
                save(output, corpus)
            self.assertEqual(output.read_text(), "existing content")

    def test_duplicate_papers_and_labels_are_rejected(self):
        paper = parse_paper(PAPER_HTML, PAPER_URL)
        with self.assertRaises(ValidationError):
            Corpus(year=2024, papers=[paper, paper])
        with self.assertRaises(ValueError):
            validate_domains([DOMAINS[0], DOMAINS[0].model_copy(update={"id": "different"})])

    def test_missing_key_has_clear_error_and_no_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            error = io.StringIO()
            output = Path(directory) / "results.json"
            with contextlib.redirect_stderr(error):
                status = main(["classify", "--papers", "missing.json", "--domains", "missing.json", "--output", str(output)])
            self.assertEqual(status, 1)
            self.assertIn("TYPESAFE_API_KEY", error.getvalue())
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
