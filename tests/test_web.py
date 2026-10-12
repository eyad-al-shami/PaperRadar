"""Database, API, job, cost, and TeX extraction tests with synthetic fixtures."""

import gzip
import io
import json
import os
import tarfile
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from research_tagger.arxiv import arxiv_download, extract_introduction, find_arxiv, source_files
from research_tagger.estimates import estimate_payload, payload_for, ready, signature, summarize
from research_tagger.models import TaggingPrompt
from research_tagger.storage import Database, now
from research_tagger.web import create_app
from research_tagger.web_models import ConferenceSelection, TaggingConfig
from scrapy_fixtures import fixture_pages

ROOT = Path(__file__).resolve().parent.parent
CONFERENCE = {"name":"ECCV","year":2024}
PROMPT = {"question": "Using only {fields}, does the paper study this domain?"}
TEX = r"""\documentclass{article}
\begin{document}
\section{Introduction}
This synthetic introduction has enough words to verify the extraction of text from a source document.
\subsection{Motivation}
The subsection remains part of the introduction.
\section{Related Work}
THIS SHOULD NOT APPEAR.
\end{document}"""


def archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return output.getvalue()


def paper(identifier: int = 1) -> dict:
    return {"id": identifier, "title": "Synthetic test paper", "abstract": "Synthetic abstract text.",
            "introduction": None, "source_url": f"https://eccv.ecva.net/virtual/2024/poster/{identifier}",
            "abstract_error": None, "introduction_error": None, "tag_error": None, "arxiv_id": None, "arxiv_error": None}


class SourceTests(unittest.TestCase):
    def test_arxiv_download_retries_temporary_406(self):
        request = httpx.Request("GET", "https://export.arxiv.org/api/query")
        rejected = httpx.HTTPStatusError(
            "406 Not Acceptable", request=request, response=httpx.Response(406, request=request),
        )
        with patch("research_tagger.arxiv.download", side_effect=[rejected, b"atom"]) as fetch, \
             patch("research_tagger.arxiv.time.sleep"):
            self.assertEqual(arxiv_download(None, str(request.url)), b"atom")
        self.assertEqual(fetch.call_count, 2)

    def test_explicit_arxiv_id_verifies_title_and_pins_version(self):
        html = b'<meta name="citation_title" content="Synthetic test"><meta property="og:url" content="https://arxiv.org/abs/2401.12345v2">'
        with patch("research_tagger.arxiv.arxiv_download",return_value=html):
            self.assertEqual(find_arxiv(None,"Synthetic test","2401.12345"),"2401.12345v2")
            with self.assertRaisesRegex(ValueError,"does not match"):
                find_arxiv(None,"Different paper","2401.12345")
            with self.assertRaisesRegex(ValueError,"different paper version"):
                find_arxiv(None,"Synthetic test","2401.12345v1")

    def test_plain_and_gzipped_tex_stop_at_next_section(self):
        for content in (TEX.encode(), gzip.compress(TEX.encode())):
            with self.subTest(compressed=content.startswith(b"\x1f\x8b")):
                text = extract_introduction(content)
                self.assertIn("synthetic introduction", text)
                self.assertIn("subsection remains", text)
                self.assertNotIn("SHOULD NOT", text)

    def test_multifile_tex_with_starred_heading_and_comments(self):
        content = archive({
            "main.tex": br"\documentclass{article}\begin{document}\input{sections/intro}\section{Methods}Method\end{document}",
            "sections/intro.tex": b"% \\section{Introduction} fake heading\n" + br"\section*{Introduction} This synthetic introduction includes enough words for a meaningful extraction test with \textbf{formatting}.",
        })
        text = extract_introduction(content)
        self.assertIn("formatting", text)
        self.assertNotIn("Methods", text)

    def test_traversal_and_pdf_are_rejected(self):
        for content in (archive({"../main.tex": TEX.encode()}), b"%PDF-1.4 unsupported"):
            with self.subTest(content=content[:15]), self.assertRaises(ValueError):
                extract_introduction(content)

    def test_missing_ambiguous_and_cyclic_sources_fail(self):
        bad_sources = [
            TEX.replace("Introduction", "Background").encode(),
            archive({"a.tex": TEX.encode(), "b.tex": TEX.encode()}),
            archive({"main.tex": TEX.replace(r"\section{Introduction}", r"\input{missing}").encode()}),
            archive({"main.tex": TEX.replace(r"\section{Introduction}", r"\input{loop}").encode(), "loop.tex": br"\input{loop}"}),
        ]
        for content in bad_sources:
            with self.subTest(content=content[:20]), self.assertRaises(ValueError):
                extract_introduction(content)

    def test_archive_symlink_fails_without_writing_files(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as tar:
            entry = tarfile.TarInfo("main.tex")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/etc/passwd"
            tar.addfile(entry)
        with self.assertRaises(ValueError):
            source_files(output.getvalue())


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.config = ConferenceSelection.model_validate(CONFERENCE).to_config()
        self.tagging = TaggingConfig.model_validate({"domains": json.loads((ROOT / "domains.json").read_text())})

    def test_instructions_are_charged_for_every_paper(self):
        records = [paper(1), paper(2)]
        stats, _ = summarize(records, [], self.config, self.tagging)
        single = estimate_payload(payload_for(records[0], self.config, self.tagging))
        self.assertEqual(stats["input_tokens"], 2 * single["input_tokens"])
        self.assertGreater(stats["instruction_tokens"], 0)
        self.assertAlmostEqual(stats["estimated_cost"], stats["input_tokens"] * 0.042 / 1_000_000)

    def test_missing_selected_introduction_is_not_estimated(self):
        config = self.config.model_copy(update={"fields": ["title", "abstract", "introduction"]})
        record = paper()
        record["introduction_error"] = "No source"
        stats, _ = summarize([record], [], config, self.tagging)
        self.assertEqual((stats["scraped"], stats["not_scraped"], stats["failed"], stats["input_tokens"]), (0,1,1,0))
        with self.assertRaises(ValueError):
            payload_for(record, config, self.tagging)

    def test_tags_become_stale_when_content_or_domains_change(self):
        record = paper()
        request_signature = signature(payload_for(record, self.config, self.tagging))
        result = {"paper_id":1,"signature":request_signature,"probabilities":json.dumps({d.id:0.9 for d in self.tagging.domains}),"input_tokens":100,"cost_usd":0.0000042}
        stats, _ = summarize([record], [result], self.config, self.tagging)
        self.assertEqual((stats["tagged"], stats["ready_to_tag"]), (1,0))
        record["abstract"] = "Changed abstract"
        changed, _ = summarize([record], [result], self.config, self.tagging)
        self.assertEqual((changed["tagged"],changed["ready_to_tag"]), (0,1))
        self.assertEqual(changed["actual_input_tokens"],100)

    def test_coverage_counts_are_disjoint(self):
        saved, pending, failed = paper(1), paper(2), paper(3)
        pending["abstract"] = None
        failed["abstract"] = None
        failed["abstract_error"] = "HTTP error"
        stats, _ = summarize([saved,pending,failed], [], self.config, self.tagging)
        self.assertEqual((stats["total"],stats["scraped"],stats["pending"],stats["failed"],stats["not_scraped"]), (3,1,1,1,2))

    def test_prompt_edits_update_estimates_and_invalidate_old_tags(self):
        record = paper()
        previous_payload = payload_for(record, self.config, self.tagging)
        previous_stats, _ = summarize([record], [], self.config, self.tagging)
        stored = {"paper_id": 1, "signature": signature(previous_payload),
                  "probabilities": json.dumps({domain.id: .9 for domain in self.tagging.domains}),
                  "input_tokens": 100, "cost_usd": .0000042}
        changed = self.tagging.model_copy(deep=True)
        changed.prompt.question += " Require explicit evidence." * 10
        stats, _ = summarize([record], [stored], self.config, changed)
        self.assertEqual((stats["tagged"], stats["ready_to_tag"]), (0, 1))
        self.assertGreater(stats["input_tokens"], previous_stats["input_tokens"])
        self.assertNotEqual(stats["estimate_signature"], previous_stats["estimate_signature"])
        self.assertEqual(stats["actual_input_tokens"], 100)

    def test_prompt_uses_only_selected_fields_and_keeps_domain_definitions(self):
        config = self.config.model_copy(update={"fields": ["title", "introduction"]})
        record = {**paper(), "introduction": "Synthetic introduction."}
        self.tagging.prompt = TaggingPrompt(question=PROMPT["question"] + " Literal {{example}}.")
        payload = payload_for(record, config, self.tagging)
        self.assertEqual(set(payload["state"]), {"title", "introduction"})
        for domain in self.tagging.domains:
            question = payload["questions"][domain.id]
            self.assertEqual(question["instructions"], {"question": "Using only title and introduction, does the paper study this domain? Literal {example}.",
                                                        "domain": domain.label, "definition": domain.description})
            self.assertNotIn("criteria", question)


class BundledDatasetTests(unittest.TestCase):
    """Exercise first-launch seeding without touching the user's working library."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        package = self.root / "research_tagger"
        package.mkdir()
        (package / "static").symlink_to(ROOT / "research_tagger" / "static", target_is_directory=True)
        (self.root / "domains.json").write_bytes((ROOT / "domains.json").read_bytes())
        self.snapshot = Database(self.root / "dataset" / "research-tagger.sqlite3")
        self.snapshot.initialize(self.root / "domains.json")
        config = ConferenceSelection(**CONFERENCE).to_config()
        self.snapshot.execute("INSERT INTO conferences(id,config) VALUES (1,?)", (config.model_dump_json(),))
        self.snapshot.execute("INSERT INTO papers(id,conference_id,title,abstract,source_url) VALUES (1,1,?,?,?)",
                              (paper()["title"], paper()["abstract"], paper()["source_url"]))
        tagging = self.snapshot.settings()
        self.probabilities = {domain.id: 0.9 for domain in tagging.domains}
        request_signature = signature(payload_for(self.snapshot.papers(1)[0], config, tagging))
        self.snapshot.execute(
            "INSERT INTO tag_results(paper_id,signature,probabilities,model,input_tokens,output_tokens,cost_usd,created_at) "
            "VALUES (1,?,?,?,100,1,0.01,?)",
            (request_signature, json.dumps(self.probabilities), tagging.model, now()),
        )
        patched_file = patch("research_tagger.web.__file__", str(package / "web.py"))
        patched_file.start()
        self.addCleanup(patched_file.stop)

    def test_first_launch_shows_saved_papers_and_tags_and_restart_preserves_edits(self):
        original = self.snapshot.path.read_bytes()
        app = create_app()
        with TestClient(app) as client:
            response = client.get("/api/papers/1")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["title"], paper()["title"])
            self.assertEqual(response.json()["probabilities"], self.probabilities)
            app.state.db.execute("UPDATE papers SET title='Local edit' WHERE id=1")
        with TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/papers/1").json()["title"], "Local edit")
        self.assertEqual(self.snapshot.path.read_bytes(), original)

    def test_explicit_database_starts_empty(self):
        app = create_app(self.root / "custom.sqlite3")
        with TestClient(app):
            self.assertEqual(app.state.db.rows("SELECT * FROM papers"), [])

    def test_missing_snapshot_fails_without_creating_an_empty_default_database(self):
        self.snapshot.path.unlink()
        app = create_app()
        with self.assertRaises(FileNotFoundError), TestClient(app):
            pass
        self.assertFalse(app.state.db.path.exists())


class WebTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('OPENREVIEW_USERNAME', 'OPENREVIEW_PASSWORD'):
            os.environ.pop(name, None)
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.app = create_app(self.path, ROOT / "domains.json")
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.db = self.app.state.db

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.temp.cleanup()

    def conference(self):
        response = self.client.post("/api/conferences",json=CONFERENCE)
        self.assertEqual(response.status_code,201,response.text)
        return response.json()["id"]

    def scrape_pages(self, conference_id, pages):
        """Supply an explicit virtual index for tests of already-saved paper collection."""
        _, config = self.db.conference(conference_id)
        records = self.db.papers(conference_id)
        entries = ''.join(f'<li><a href="{paper["source_url"]}">{paper["title"]}</a></li>' for paper in records)
        index = f'<main><noscript class="noscript"><ul>{entries}</ul></noscript></main>'
        return fixture_pages({str(config.index_url): index, **pages})

    def wait_job(self, job_id):
        for _ in range(200):
            row = self.db.rows("SELECT * FROM jobs WHERE id=?",(job_id,))[0]
            if row["status"] not in {"queued","running"}:
                return row
            threading.Event().wait(.01)
        self.fail("Background test job did not finish")

    def test_conference_settings_and_database_persist(self):
        conference_id = self.conference()
        response = self.client.put(f"/api/conferences/{conference_id}",json={**CONFERENCE,"fields":["title","introduction"]})
        self.assertEqual(response.status_code,200,response.text)
        reopened = Database(self.path)
        self.assertEqual(reopened.conference(conference_id)[1].fields,["title","introduction"])
        self.assertEqual(self.client.post("/api/conferences",json=CONFERENCE).status_code,400)

    def test_only_supported_editions_generate_official_urls(self):
        editions = [("ICML", 2026, "https://icml.cc/virtual/2026/papers.html"),
                    ("ECCV", 2026, "https://eccv.ecva.net/virtual/2026/papers.html"),
                    ("ICLR", 2026, "https://iclr.cc/virtual/2026/papers.html"),
                    ("CVPR", 2026, "https://openaccess.thecvf.com/CVPR2026?day=all"),
                    ("CVPR", 2025, "https://openaccess.thecvf.com/CVPR2025?day=all"),
                    ("ICCV", 2025, "https://openaccess.thecvf.com/ICCV2025?day=all"),
                    ("NeurIPS", 2025, "https://proceedings.neurips.cc/")]
        for name, year, url in editions:
            response = self.client.post("/api/conferences", json={"name": name, "year": year})
            self.assertEqual(response.status_code, 201, response.text)
            config = self.db.conference(response.json()["id"])[1]
            self.assertEqual(str(config.index_url), url)
            self.assertEqual(config.source, "neurips" if name == "NeurIPS" else "cvf" if name in {"CVPR", "ICCV"} else "virtual")
        future = datetime.now(timezone.utc).year + 1
        for invalid in ({"name": "ECCV", "year": 2025}, {"name": "ICCV", "year": 2026},
                        {"name": "Unknown", "year": 2026}, {"name": "ICML", "year": future},
                        {"name": "ICML", "year": "2026"}, {"name": "ICML"},
                        {**CONFERENCE, "source": "ecva"}, {**CONFERENCE, "index_url": "https://example.org/"}):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.client.post("/api/conferences", json=invalid).status_code, 422)

    def test_catalog_years_and_edit_validation_match(self):
        options = self.client.get("/api/settings").json()["conference_options"]
        self.assertEqual([item["name"] for item in options], ["ICML", "ECCV", "ICLR", "CVPR", "ICCV", "NeurIPS"])
        for item in options:
            self.assertEqual(item["years"], sorted(item["years"], reverse=True))
            for year in item["years"]:
                config = ConferenceSelection(name=item["name"], year=year).to_config()
                self.assertEqual(str(config.index_url), item["url_template"].format(year=year))
        identifier = self.conference()
        changed = {"name": "ICML", "year": 2026}
        response = self.client.put(f"/api/conferences/{identifier}", json=changed)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(str(self.db.conference(identifier)[1].index_url), "https://icml.cc/virtual/2026/papers.html")
        self.assertEqual(self.client.put(f"/api/conferences/{identifier}", json={"name":"ICCV", "year":2026}).status_code, 422)
        self.conference()
        self.assertEqual(self.client.put(f"/api/conferences/{identifier}", json=CONFERENCE).status_code, 400)

    def test_ris_export_uses_all_matching_filters_and_current_jev_tags(self):
        """The Zotero download includes every matching record and omits filtered papers and low scores."""
        identifier = self.conference()
        self.db.discover(identifier, [
            {"title":"Matching Alpha", "source_url":"https://eccv.ecva.net/virtual/2024/poster/1"},
            {"title":"Excluded Beta", "source_url":"https://eccv.ecva.net/virtual/2024/poster/2"},
        ])
        self.db.execute("UPDATE papers SET abstract='An abstract with\nmultiple lines.'")
        self.db.execute("UPDATE papers SET arxiv_id='2401.12345v1' WHERE title='Matching Alpha'")
        self.db.execute("UPDATE papers SET pdf_url='https://example.test/alpha.pdf' WHERE title='Matching Alpha'")
        tagging = self.db.settings()
        _, config = self.db.conference(identifier)
        saved = self.db.papers(identifier)[0]
        request_signature = signature(payload_for(saved, config, tagging))
        probabilities = {domain.id:(0.9 if index == 0 else 0.1) for index, domain in enumerate(tagging.domains)}
        self.db.execute(
            "INSERT INTO tag_results(paper_id,signature,probabilities,model,input_tokens,output_tokens,cost_usd,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (saved["id"], request_signature, json.dumps(probabilities), tagging.model, 100, 1, 0.0, now()),
        )
        domain = tagging.domains[0]
        response = self.client.get(
            f"/api/conferences/{identifier}/papers.ris", params={"search":"Alpha", "status":"tagged", "domain":domain.id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/x-research-info-systems; charset=utf-8")
        self.assertIn('filename="ECCV-2024-papers.ris"', response.headers["content-disposition"])
        for line in ("TY  - CONF", "TI  - Matching Alpha", "T2  - ECCV", "PY  - 2024",
                     "AB  - An abstract with multiple lines.", "UR  - https://example.test/alpha.pdf",
                     "DB  - arXiv", "AN  - 2401.12345v1",
                     "N1  - arXiv page: https://arxiv.org/abs/2401.12345v1",
                     "N1  - Conference page: https://eccv.ecva.net/virtual/2024/poster/1", f"KW  - {domain.label}", "ER  -"):
            self.assertIn(line, response.text)
        self.assertNotIn("Excluded Beta", response.text)
        self.assertNotIn("L1  -", response.text)
        self.assertEqual(response.text.count("UR  -"), 1)
        for excluded in tagging.domains[1:]:
            self.assertNotIn(f"KW  - {excluded.label}", response.text)
        self.assertEqual(self.client.get(f"/api/conferences/{identifier}/papers.ris", params={"search":"none"}).status_code, 400)
        self.assertEqual(self.client.get(f"/api/conferences/{identifier}/papers.ris", params={"domain":"unknown"}).status_code, 400)

    def test_ris_item_url_for_papers_with_and_without_pdf_links(self):
        """Export one item URL per paper, keeping page metadata without attachment fields."""
        identifier = self.conference()
        source_url = "https://eccv.ecva.net/virtual/2024/poster/1"
        self.db.discover(identifier, [{"title":"Example paper", "source_url":source_url}])
        for pdf_url, arxiv_id, expected_url in (
            ("https://openreview.net/pdf?id=example", None, "https://openreview.net/pdf?id=example"),
            (None, "2401.12345v1", "https://arxiv.org/abs/2401.12345v1"),
            (None, None, source_url),
        ):
            with self.subTest(pdf_url=pdf_url, arxiv_id=arxiv_id):
                self.db.execute("UPDATE papers SET pdf_url=?, arxiv_id=?", (pdf_url, arxiv_id))
                response = self.client.get(f"/api/conferences/{identifier}/papers.ris")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([line for line in response.text.splitlines() if line.startswith("UR  -")],
                                 [f"UR  - {expected_url}"])
                self.assertNotIn("L1  -", response.text)
                if pdf_url or arxiv_id:
                    self.assertIn(f"N1  - Conference page: {source_url}", response.text)

    def test_paper_details_show_only_current_saved_probabilities(self):
        identifier = self.conference()
        self.db.discover(identifier, [{"title":"Scored paper", "source_url":"https://example.test/paper"}])
        self.db.execute("UPDATE papers SET abstract='Saved abstract'")
        saved = self.db.papers(identifier)[0]
        _, config = self.db.conference(identifier)
        tagging = self.db.settings()
        probabilities = {domain.id: (0.9 if index == 0 else 0.1) for index, domain in enumerate(tagging.domains)}
        self.db.execute(
            "INSERT INTO tag_results(paper_id,signature,probabilities,model,input_tokens,output_tokens,cost_usd,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (saved["id"], signature(payload_for(saved, config, tagging)), json.dumps(probabilities), tagging.model, 100, 1, 0.0, now()),
        )
        endpoint = f"/api/papers/{saved['id']}"
        response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["probabilities"], probabilities)
        self.assertEqual(response.json()["status"], "tagged")
        self.assertGreater(response.json()["estimated_tokens"], 0)
        self.db.execute("UPDATE papers SET abstract='Changed abstract'")
        self.assertIsNone(self.client.get(endpoint).json()["probabilities"])
        self.assertEqual(len(self.db.rows("SELECT * FROM tag_results")), 1)
        self.assertEqual(self.client.get('/api/papers/99999').status_code, 404)

    def test_zotero_connector_metadata_and_single_paper_scope(self):
        """The Connector reads saved metadata only, including missing-PDF records."""
        identifier = self.conference()
        self.db.discover(identifier, [
            {"title":"Paper with PDF", "source_url":"https://example.test/paper/1"},
            {"title":"Paper without PDF", "source_url":"https://example.test/paper/2"},
        ])
        self.db.execute("UPDATE papers SET abstract='Saved abstract', introduction='Private unused section'")
        self.db.execute("UPDATE papers SET pdf_url='https://openreview.net/pdf?id=example', arxiv_id='2401.12345v1' WHERE title='Paper with PDF'")
        endpoint = f"/api/conferences/{identifier}/zotero"
        response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['version'], 1)
        self.assertEqual(len(data['papers']), 2)
        paper = next(p for p in data['papers'] if p['pdf_url'])
        self.assertEqual(paper['pdf_url'], 'https://openreview.net/pdf?id=example')
        self.assertEqual(paper['abstract'], 'Saved abstract')
        self.assertEqual((paper['conference'], paper['year']), ('ECCV', 2024))
        self.assertEqual(paper['tags'], [])
        self.assertEqual(set(paper), {'id','title','abstract','conference','year','source_url','pdf_url','arxiv_id','tags'})
        self.assertEqual(self.client.get(endpoint, params={'paper_id':paper['id']}).json()['papers'], [paper])
        self.assertEqual(self.client.get(endpoint, params={'paper_id':paper['id'], 'search':'missing'}).status_code, 404)
        other = self.client.post('/api/conferences', json={'name':'ICML', 'year':2026}).json()['id']
        self.assertEqual(self.client.get(f'/api/conferences/{other}/zotero', params={'paper_id':paper['id']}).status_code, 404)
        self.assertEqual(self.client.get(endpoint, params={'search':'missing'}).json()['papers'], [])
        self.assertEqual(self.client.get(endpoint, params={'status':'invalid'}).status_code, 400)
        self.assertEqual(self.db.rows('SELECT * FROM jobs'), [])
        translator = self.client.get('/static/zotero/Research%20Tagger.js')
        self.assertEqual(translator.status_code, 200)
        self.assertIn('"label": "PaperRadar"', translator.text)

    def test_pdf_link_collection_preserves_text_filters_and_resumes(self):
        identifier = self.conference()
        records = [{"title": f"{name} paper", "source_url": f"https://eccv.ecva.net/virtual/2024/poster/{i}"}
                   for i, name in enumerate(("Matching Alpha", "Matching Beta", "Excluded"), 1)]
        self.db.discover(identifier, records)
        self.db.execute("UPDATE papers SET abstract='Existing abstract', introduction='Existing introduction'")
        pages = {item["source_url"]: f'<h1 class="event-title">{item["title"]}</h1>' for item in records}
        pages[records[0]["source_url"]] += '<a href="https://example.test/alpha.pdf">Paper PDF</a>'
        with fixture_pages(pages) as requested:
            response = self.client.post(f"/api/conferences/{identifier}/pdf-links", json={"search":"Matching", "domain":""})
            self.assertEqual(response.status_code, 202, response.text)
            job = self.wait_job(response.json()["id"])
        self.assertEqual((job["status"], job["total"], job["completed"], job["failed"]), ("completed", 2, 1, 1))
        self.assertNotIn(records[2]["source_url"], requested)
        self.assertFalse(any(url.endswith(".pdf") for url in requested))
        saved = self.db.papers(identifier)
        self.assertEqual(saved[0]["pdf_url"], "https://example.test/alpha.pdf")
        self.assertIn("No paper PDF", saved[1]["pdf_error"])
        self.assertTrue(all(paper["abstract"] == "Existing abstract" and paper["introduction"] == "Existing introduction" for paper in saved))
        listed = self.client.get(f"/api/conferences/{identifier}/papers", params={"search":"Matching"}).json()
        self.assertEqual(listed["pdf_missing"], 1)
        pages[records[1]["source_url"]] += '<a href="https://example.test/beta.pdf">Paper PDF</a>'
        with fixture_pages(pages) as requested:
            response = self.client.post(f"/api/conferences/{identifier}/pdf-links", json={"search":"Matching"})
            job = self.wait_job(response.json()["id"])
        self.assertEqual((job["total"], job["completed"], job["failed"]), (1, 1, 0))
        self.assertNotIn(records[0]["source_url"], requested)
        self.assertIsNone(self.db.papers(identifier)[1]["pdf_error"])

    def test_normal_scrape_saves_pdf_links_with_abstracts(self):
        identifier = self.conference()
        url = 'https://eccv.ecva.net/virtual/2024/poster/1'
        self.db.discover(identifier, [{"title":"Test paper", "source_url":url}])
        html = ('<h1 class="event-title">Test paper</h1>'
                '<div id="abstractText"><div class="abstract-text-inner">Collected abstract.</div></div>'
                '<a href="https://example.test/paper.pdf">Paper PDF</a>')
        with self.scrape_pages(identifier, {url:html}) as requested:
            response = self.client.post(f'/api/conferences/{identifier}/jobs', json={"kind":"scrape"})
            job = self.wait_job(response.json()['id'])
        self.assertEqual((job['status'], job['completed']), ('completed', 1))
        saved = self.db.papers(identifier)[0]
        self.assertEqual(saved['pdf_url'], 'https://example.test/paper.pdf')
        self.assertEqual(saved['abstract'], 'Collected abstract.')
        self.assertNotIn(saved['pdf_url'], requested)

    def test_combined_scrape_resumes_missing_metadata_without_changing_saved_tags(self):
        """Ready/tagged text still gets missing links; retries preserve saved fields."""
        identifier = self.conference()
        records = [{"title":name, "source_url":f"https://eccv.ecva.net/virtual/2024/poster/{i}"}
                   for i, name in enumerate(("Complete", "Needs link", "Needs abstract", "Both missing"), 1)]
        self.db.discover(identifier, records)
        papers = self.db.papers(identifier)
        for record in papers[:2]:
            self.db.execute("UPDATE papers SET abstract='Preserved abstract' WHERE id=?", (record["id"],))
        for record in (papers[0], papers[2]):
            self.db.execute("UPDATE papers SET pdf_url='https://example.test/saved.pdf' WHERE id=?", (record["id"],))
        saved = self.db.papers(identifier)[1]
        _, config = self.db.conference(identifier)
        tagging = self.db.settings()
        self.db.execute(
            "INSERT INTO tag_results(paper_id,signature,probabilities,model,input_tokens,output_tokens,cost_usd,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (saved["id"], signature(payload_for(saved, config, tagging)), json.dumps({domain.id:0.9 for domain in tagging.domains}), tagging.model, 100, 1, 0.01, now()),
        )
        before_tags = self.db.rows("SELECT * FROM tag_results")
        stats = self.client.get('/api/conferences').json()['conferences'][0]['stats']
        self.assertEqual((stats['missing_pdf'], stats['collection_pending']), (2, 3))
        pages = {
            records[1]['source_url']: '<h1 class="event-title">Needs link</h1><a href="https://example.test/new.pdf">Paper PDF</a>',
            records[2]['source_url']: '<h1 class="event-title">Needs abstract</h1><div id="abstractText"><div class="abstract-text-inner">New abstract.</div></div>',
            records[3]['source_url']: '<h1 class="event-title">Both missing</h1><div id="abstractText"><div class="abstract-text-inner">Collected text.</div></div>',
        }
        with self.scrape_pages(identifier, pages) as requested:
            response = self.client.post(f'/api/conferences/{identifier}/jobs', json={'kind':'scrape'})
            self.assertEqual(response.status_code, 202, response.text)
            job = self.wait_job(response.json()['id'])
        self.assertEqual((job['total'], job['completed'], job['failed']), (3, 2, 1))
        self.assertNotIn(records[0]['source_url'], requested)
        self.assertFalse(any(url.endswith('.pdf') for url in requested))
        self.assertEqual(self.db.rows('SELECT * FROM tag_results'), before_tags)
        saved = self.db.papers(identifier)
        self.assertEqual(saved[1]['abstract'], 'Preserved abstract')
        self.assertEqual(saved[2]['pdf_url'], 'https://example.test/saved.pdf')
        self.assertTrue(saved[3]['pdf_error'])
        stats = self.client.get('/api/conferences').json()['conferences'][0]['stats']
        self.assertEqual((stats['not_scraped'], stats['missing_pdf'], stats['collection_pending'], stats['ready_to_tag']), (0, 1, 1, 3))
        pages[records[3]['source_url']] = '<h1 class="event-title">Both missing</h1><a href="https://example.test/final.pdf">Paper PDF</a>'
        with self.scrape_pages(identifier, pages) as requested:
            response = self.client.post(f'/api/conferences/{identifier}/jobs', json={'kind':'scrape'})
            job = self.wait_job(response.json()['id'])
        self.assertEqual((job['total'], job['completed'], job['failed']), (1, 1, 0))
        self.assertFalse(any(record['source_url'] in requested for record in records[:3]))
        self.assertEqual(self.db.papers(identifier)[3]['abstract'], 'Collected text.')
        self.assertIsNone(self.db.papers(identifier)[3]['pdf_error'])
        with self.scrape_pages(identifier, {}) as requested:
            response = self.client.post(f'/api/conferences/{identifier}/jobs', json={'kind':'scrape'})
            job = self.wait_job(response.json()['id'])
        self.assertEqual((job['status'], job['stage'], job['total']), ('completed', 'scraping', 0))
        self.assertFalse(any('/poster/' in url for url in requested))

    def test_scrape_always_collects_abstract_and_pdf_even_for_title_only_tagging(self):
        identifier = self.conference()
        self.client.put(f'/api/conferences/{identifier}', json={**CONFERENCE, 'fields':['title']}).raise_for_status()
        url = 'https://eccv.ecva.net/virtual/2024/poster/1'
        self.db.discover(identifier, [{'title':'Title only', 'source_url':url}])
        html = ('<h1 class="event-title">Title only</h1><div id="abstractText"><div class="abstract-text-inner">Abstract collected.</div></div>'
                '<a href="https://example.test/paper.pdf">Paper PDF</a>')
        with self.scrape_pages(identifier, {url:html}):
            response = self.client.post(f'/api/conferences/{identifier}/jobs', json={'kind':'scrape'})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(self.wait_job(response.json()['id'])['completed'], 1)
        saved = self.db.papers(identifier)[0]
        self.assertEqual(saved['abstract'], 'Abstract collected.')
        self.assertEqual(saved['pdf_url'], 'https://example.test/paper.pdf')
        _, config = self.db.conference(identifier)
        self.assertEqual(set(payload_for(saved, config, self.db.settings())['state']), {'title'})

    def test_multi_domain_filter_matches_all_and_export_matches_all_pages(self):
        identifier = self.conference()
        records = [{'title':f'Matching {i}', 'source_url':f'https://eccv.ecva.net/virtual/2024/poster/{i}'} for i in range(130)]
        self.db.discover(identifier, records)
        self.db.execute("UPDATE papers SET abstract='Saved abstract'")
        tagging = self.db.settings()
        first, second = [domain.id for domain in tagging.domains[:2]]
        _, config = self.db.conference(identifier)
        saved = self.db.papers(identifier)
        for i, paper in enumerate(saved[:129]):
            values = {domain.id:0.1 for domain in tagging.domains}
            values[first] = 0.9 if i % 4 in (0, 2) else 0.1
            values[second] = 0.9 if i % 4 in (1, 2) else 0.1
            request_signature = signature(payload_for(paper, config, tagging)) if i < 128 else 'stale-result'
            self.db.execute(
                'INSERT INTO tag_results(paper_id,signature,probabilities,model,input_tokens,output_tokens,cost_usd,created_at) VALUES (?,?,?,?,?,?,?,?)',
                (paper['id'], request_signature, json.dumps(values), tagging.model, 100, 1, 0.01, now()),
            )
        query = [('search','Matching'), ('status','tagged'), ('domain',first), ('domain',second)]
        page = self.client.get(f'/api/conferences/{identifier}/papers', params=query).json()
        following = self.client.get(f'/api/conferences/{identifier}/papers', params=query + [('page',2)]).json()
        expected = {saved[i]['id'] for i in range(128) if i % 4 == 2}
        self.assertEqual(page['count'], 32)
        reversed_page = self.client.get(f'/api/conferences/{identifier}/papers', params=query[:2] + list(reversed(query[2:]))).json()
        self.assertEqual(reversed_page['papers'], page['papers'])
        self.assertEqual(len(page['papers']), 30)
        self.assertEqual({p['id'] for p in page['papers'] + following['papers']}, expected)
        ris = self.client.get(f'/api/conferences/{identifier}/papers.ris', params=query)
        self.assertEqual(ris.status_code, 200, ris.text)
        self.assertEqual({line.removeprefix('TI  - ') for line in ris.text.splitlines() if line.startswith('TI  - ')},
                         {saved[i]['title'] for i in range(128) if i % 4 == 2})
        self.assertEqual(self.client.get(f'/api/conferences/{identifier}/papers').json()['count'], 130)
        self.assertEqual(self.client.get(f'/api/conferences/{identifier}/papers', params={'domain':first}).json()['count'], 64)
        connector = self.client.get(f'/api/conferences/{identifier}/zotero', params=query)
        self.assertEqual(connector.status_code, 200, connector.text)
        self.assertEqual({p['id'] for p in connector.json()['papers']}, expected)
        for paper in connector.json()['papers']:
            self.assertEqual(set(paper['tags']), {domain.label for domain in tagging.domains[:2]})
        for path in ('papers', 'papers.ris', 'zotero'):
            self.assertEqual(self.client.get(f'/api/conferences/{identifier}/{path}', params=query + [('domain','unknown')]).status_code, 400)

    def test_iccv_pdf_collection_reads_only_the_index_and_discovery_saves_links(self):
        identifier = self.client.post('/api/conferences', json={"name":"ICCV", "year":2025}).json()["id"]
        record = {"title":"Test paper", "source_url":"https://openaccess.thecvf.com/content/ICCV2025/html/Test.html"}
        self.db.discover(identifier, [record])
        index = 'https://openaccess.thecvf.com/ICCV2025?day=all'
        html = f'<dt class="ptitle"><a href="{record["source_url"]}">Test paper</a></dt><dd><a href="/content/ICCV2025/papers/Test.pdf">pdf</a></dd>'
        with fixture_pages({index:html}) as requested:
            response = self.client.post(f"/api/conferences/{identifier}/pdf-links", json={})
            job = self.wait_job(response.json()["id"])
        self.assertEqual((job["status"], job["completed"]), ("completed", 1))
        self.assertEqual(set(requested), {index, 'https://openaccess.thecvf.com/robots.txt'})
        saved = self.db.papers(identifier)[0]
        self.assertTrue(saved["pdf_url"].endswith('/Test.pdf'))
        self.assertIsNone(saved["pdf_error"])
        self.db.discover(identifier, [{**record, "pdf_url": saved["pdf_url"]}])
        self.assertEqual(self.db.papers(identifier)[0]["pdf_url"], saved["pdf_url"])

    def test_arxiv_link_job_uses_current_filters_and_downloads_no_sources(self):
        """Metadata lookup stores exact IDs/errors and resumes only unresolved matching papers."""
        identifier = self.conference()
        self.db.discover(identifier, [
            {"title":"Matching Alpha", "source_url":"https://eccv.ecva.net/virtual/2024/poster/1"},
            {"title":"Matching Beta", "source_url":"https://eccv.ecva.net/virtual/2024/poster/2"},
            {"title":"Excluded Gamma", "source_url":"https://eccv.ecva.net/virtual/2024/poster/3"},
        ])
        self.db.execute("UPDATE papers SET abstract='Saved abstract'")
        with patch("research_tagger.jobs.find_arxiv", side_effect=["2401.00001v2", ValueError("No exact match")]) as lookup:
            response = self.client.post(
                f"/api/conferences/{identifier}/arxiv-links",
                json={"search":"Matching", "status":"all", "domain":""},
            )
            self.assertEqual(response.status_code, 202, response.text)
            job = self.wait_job(response.json()["id"])
        self.assertEqual((job["status"], job["total"], job["completed"], job["failed"]), ("completed", 2, 1, 1))
        self.assertEqual(lookup.call_count, 2)
        papers = self.db.papers(identifier)
        self.assertEqual(papers[0]["arxiv_id"], "2401.00001v2")
        self.assertIsNone(papers[0]["introduction"])
        self.assertEqual(papers[1]["arxiv_error"], "No exact match")
        self.assertIsNone(papers[2]["arxiv_id"])
        listed = self.client.get(f"/api/conferences/{identifier}/papers", params={"search":"Matching"}).json()
        self.assertEqual(listed["arxiv_missing"], 1)
        with patch("research_tagger.jobs.find_arxiv", return_value="2401.00002v1") as retry:
            response = self.client.post(f"/api/conferences/{identifier}/arxiv-links", json={"search":"Beta", "status":"all"})
            job = self.wait_job(response.json()["id"])
        self.assertEqual((job["total"], job["completed"], job["failed"]), (1, 1, 0))
        self.assertEqual(retry.call_count, 1)
        self.assertIsNone(self.db.papers(identifier)[1]["arxiv_error"])

    def test_invalid_html_list_does_not_replace_saved_index(self):
        identifier = self.conference()
        self.db.discover(identifier, [{"title": "Existing paper", "source_url": "https://eccv.ecva.net/virtual/2024/poster/1"}])
        self.db.execute("UPDATE papers SET abstract='Existing abstract'")
        prior = self.db.papers(identifier)
        config = self.db.conference(identifier)[1]
        html = '<main><noscript class="noscript"><ul><li><a href="/virtual/2024/poster/2">Different paper</a></li><li>Missing link</li></ul></noscript></main>'
        pages = {str(config.index_url): html}
        with fixture_pages(pages):
            job = self.client.post(f"/api/conferences/{identifier}/jobs", json={"kind":"discover"}).json()["id"]
            finished = self.wait_job(job)
        self.assertEqual(finished["status"], "failed")
        self.assertIn("exactly one paper link", finished["error"])
        self.assertEqual(self.db.papers(identifier), prior)

    def test_prompt_settings_load_existing_defaults_and_persist_edits(self):
        config = self.client.get("/api/settings").json()["tagging"]
        del config["prompt"]
        self.db.execute("UPDATE settings SET value=? WHERE key='tagging'", (json.dumps(config),))
        loaded = self.client.get("/api/settings").json()["tagging"]
        self.assertEqual(loaded["prompt"], TaggingPrompt().model_dump())
        response = self.client.put("/api/settings", json={**loaded, "prompt": PROMPT})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tagging"]["prompt"], PROMPT)
        self.assertEqual(Database(self.path).settings().prompt.model_dump(), PROMPT)
        self.assertEqual(self.client.get("/api/settings").json()["tagging"]["prompt"], PROMPT)

    def test_invalid_prompt_is_rejected_without_overwriting_saved_settings(self):
        config = self.client.get("/api/settings").json()["tagging"]
        for field in PROMPT:
            for value in (" ", "{unknown}", "{fields!r}", "{fields:>20}", "{fields", "{}"):
                with self.subTest(field=field, value=value):
                    changed = {**config, "prompt": {**PROMPT, field: value}}
                    self.assertEqual(self.client.put("/api/settings", json=changed).status_code, 422)
        self.assertEqual(self.client.get("/api/settings").json()["tagging"], config)

    def test_prompt_test_uses_reviewed_draft_and_does_not_save_results(self):
        from unittest.mock import Mock

        conference_id = self.conference()
        self.db.discover(conference_id, [
            {"title": f"Synthetic {index}", "source_url": f"https://eccv.ecva.net/virtual/2024/poster/{index}"}
            for index in (1, 2, 3)
        ])
        self.db.execute("UPDATE papers SET abstract='Synthetic abstract'")
        saved = self.client.get("/api/settings").json()["tagging"]
        draft = {**saved, "prompt": PROMPT, "price_per_million": 1.0}
        request = {"conference_id": conference_id, "limit": 2, "tagging": draft}
        preview_response = self.client.post("/api/prompt-test/estimate", json=request)
        self.assertEqual(preview_response.status_code, 200, preview_response.text)
        preview = preview_response.json()
        self.assertEqual(preview["paper_count"], 2)
        self.assertGreater(preview["input_tokens"], 0)
        self.assertEqual(self.client.post("/api/prompt-test", json={**request, "estimate_signature": preview["estimate_signature"]}).status_code, 400)
        self.client.put("/api/key", json={"api_key": "synthetic-test-key"}).raise_for_status()
        self.assertEqual(self.client.post("/api/prompt-test", json={**request, "estimate_signature": "stale"}).status_code, 400)
        responses = []
        for tokens, probability in ((100, 0.9), (200, 0.2)):
            response = Mock()
            response.json.return_value = {
                "model": "jev-1.13.0",
                "answers": {domain["id"]: {"type": "noul", "noul": probability} for domain in draft["domains"]},
                "usage": {"input_tokens": tokens, "output_tokens": 1},
            }
            responses.append(response)
        with patch("research_tagger.jobs.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.side_effect = responses
            result_response = self.client.post("/api/prompt-test", json={**request, "estimate_signature": preview["estimate_signature"]})
        self.assertEqual(result_response.status_code, 200, result_response.text)
        result = result_response.json()
        self.assertEqual([paper["title"] for paper in result["results"]], ["Synthetic 1", "Synthetic 2"])
        self.assertEqual((result["actual_input_tokens"], result["actual_output_tokens"]), (300, 2))
        self.assertAlmostEqual(result["actual_cost"], 0.0003)
        payload = client.return_value.__enter__.return_value.post.call_args_list[0].kwargs["json"]
        self.assertTrue(all(question["instructions"]["question"] == "Using only title and abstract, does the paper study this domain?" for question in payload["questions"].values()))
        self.assertEqual(self.client.get("/api/settings").json()["tagging"], saved)
        self.assertFalse(self.db.rows("SELECT id FROM tag_results"))
        self.assertFalse(self.db.rows("SELECT id FROM jobs"))

    def test_startup_removes_legacy_criteria_and_preserves_saved_question(self):
        config = self.client.get("/api/settings").json()["tagging"]
        config["prompt"] = {**PROMPT, "true_criterion": "Prior custom inclusion rule.",
                            "false_criterion": "Prior custom exclusion rule."}
        self.db.execute("UPDATE settings SET value=? WHERE key='tagging'", (json.dumps(config),))
        expected = {**config, "prompt": PROMPT}
        for _ in range(2):
            self.db.initialize(ROOT / "domains.json")
            self.assertEqual(self.client.get("/api/settings").json()["tagging"], expected)
            saved = json.loads(self.db.rows("SELECT value FROM settings WHERE key='tagging'")[0]["value"])
            self.assertEqual(saved, expected)
        self.assertEqual(self.client.put("/api/settings", json=config).status_code, 422)

    def test_discovery_scrape_and_resume_preserve_successes(self):
        conference_id = self.conference()
        urls = [f"https://eccv.ecva.net/virtual/2024/poster/{identifier}" for identifier in (1, 2)]
        config = ConferenceSelection(**CONFERENCE).to_config()
        entries = "".join(f'<li><a href="{url}">Synthetic {name}</a></li>' for url, name in zip(urls, ("A", "B")))
        index = f'<main><noscript class="noscript"><ul>{entries}</ul></noscript></main>'
        pages = {str(config.index_url): index,
                 urls[0]: '<h1 class="event-title">Synthetic A</h1><div id="abstractText"><div class="abstract-text-inner">Saved abstract</div></div><a href="https://example.test/a.pdf">Paper PDF</a>',
                 urls[1]: '<h1 class="event-title">Synthetic B</h1><a href="https://example.test/b.pdf">Paper PDF</a>'}
        with fixture_pages(pages):
            job_id = self.client.post(f"/api/conferences/{conference_id}/jobs",json={"kind":"scrape"}).json()["id"]
            job = self.wait_job(job_id)
        self.assertEqual((job["completed"],job["failed"]),(1,1))
        stats = self.client.get("/api/conferences").json()["conferences"][0]["stats"]
        self.assertEqual((stats["scraped"],stats["not_scraped"],stats["failed"]),(1,1,1))
        pages[urls[1]] += '<div id="abstractText"><div class="abstract-text-inner">Recovered abstract</div></div>'
        with fixture_pages(pages) as fetched:
            job_id = self.client.post(f"/api/conferences/{conference_id}/jobs",json={"kind":"scrape"}).json()["id"]
            self.assertEqual(self.wait_job(job_id)["status"], "completed")
        self.assertNotIn(urls[0], fetched)
        self.assertEqual(fetched.count(urls[1]), 1)
        self.assertEqual([p["abstract"] for p in self.db.papers(conference_id)],["Saved abstract","Recovered abstract"])
        new_url = "https://eccv.ecva.net/virtual/2024/poster/3"
        pages[str(config.index_url)] = index.replace('</ul>', f'<li><a href="{new_url}">Synthetic C</a></li></ul>')
        pages[new_url] = '<h1 class="event-title">Synthetic C</h1><div id="abstractText"><div class="abstract-text-inner">New abstract</div></div><a href="https://example.test/c.pdf">Paper PDF</a>'
        with fixture_pages(pages) as fetched:
            job_id = self.client.post(f"/api/conferences/{conference_id}/jobs",json={"kind":"scrape"}).json()["id"]
            job = self.wait_job(job_id)
        self.assertEqual((job['status'], job['stage'], job['completed']), ('completed', 'scraping', 1))
        self.assertTrue(all(url not in fetched for url in urls))
        self.assertEqual(len(self.db.papers(conference_id)), 3)

    def test_scrape_index_failure_keeps_saved_papers(self):
        conference_id = self.conference()
        self.db.discover(conference_id, [{"title":"Saved paper", "source_url":"https://example.test/saved"}])
        before = self.db.papers(conference_id)
        _, config = self.db.conference(conference_id)
        with fixture_pages({str(config.index_url): '<main>Unavailable index</main>'}) as requested:
            response = self.client.post(f'/api/conferences/{conference_id}/jobs', json={'kind':'scrape'})
            job = self.wait_job(response.json()['id'])
        self.assertEqual((job['status'], job['stage'], job['total']), ('failed', 'discovering', 0))
        self.assertEqual(self.db.papers(conference_id), before)
        self.assertNotIn(before[0]['source_url'], requested)

    def test_scrape_cancelled_during_discovery_does_not_replace_index(self):
        conference_id = self.conference()
        self.db.discover(conference_id, [{"title":"Saved paper", "source_url":"https://example.test/saved"}])
        before = self.db.papers(conference_id)
        def stop_after_read(config, should_stop):
            self.db.execute("UPDATE jobs SET cancel_requested=1 WHERE status='running'")
            return [{"title":"New paper", "source_url":"https://example.test/new"}]
        with patch('research_tagger.jobs.discover_conference', side_effect=stop_after_read), \
             patch('research_tagger.jobs.crawl_conference') as crawl:
            response = self.client.post(f'/api/conferences/{conference_id}/jobs', json={'kind':'scrape'})
            job = self.wait_job(response.json()['id'])
        self.assertEqual((job['status'], job['stage']), ('cancelled', 'discovering'))
        self.assertEqual(self.db.papers(conference_id), before)
        crawl.assert_not_called()

    def test_introduction_failure_preserves_scrapy_abstract_for_resume(self):
        conference_id = self.conference()
        self.client.put(f"/api/conferences/{conference_id}", json={**CONFERENCE, "fields": ["title", "abstract", "introduction"]}).raise_for_status()
        url = "https://eccv.ecva.net/virtual/2024/poster/1"
        self.db.discover(conference_id, [{"title": "Synthetic paper", "source_url": url}])
        with self.scrape_pages(conference_id, {url: '<h1 class="event-title">Synthetic paper</h1><div id="abstractText"><div class="abstract-text-inner">Saved abstract</div></div><a href="https://example.test/paper.pdf">Paper PDF</a>'}), \
             patch("research_tagger.jobs.fetch_introduction", side_effect=ValueError("Source unavailable")):
            job_id = self.client.post(f"/api/conferences/{conference_id}/jobs", json={"kind": "scrape"}).json()["id"]
            job = self.wait_job(job_id)
        self.assertEqual((job["completed"], job["failed"]), (0, 1))
        saved = self.db.papers(conference_id)[0]
        self.assertEqual(saved["abstract"], "Saved abstract")
        self.assertEqual(saved["introduction_error"], "Source unavailable")
        with self.scrape_pages(conference_id, {}) as requests, \
             patch("research_tagger.jobs.fetch_introduction", return_value=("2401.12345v1", "Synthetic introduction")):
            job_id = self.client.post(f"/api/conferences/{conference_id}/jobs", json={"kind": "scrape"}).json()["id"]
            job = self.wait_job(job_id)
        self.assertEqual((job["completed"], job["failed"]), (1, 0))
        self.assertNotIn(url, requests)
        self.assertIsNone(self.db.papers(conference_id)[0]["introduction_error"])

    def test_paid_job_requires_matching_estimate_and_saves_actual_usage(self):
        conference_id = self.conference()
        self.db.discover(conference_id,[{"title":"Synthetic","source_url":"https://eccv.ecva.net/virtual/2024/poster/1"}])
        self.db.execute("UPDATE papers SET abstract='Synthetic abstract'")
        self.client.put("/api/key",json={"api_key":"synthetic-test-key"})
        old_stats = self.client.get("/api/conferences").json()["conferences"][0]["stats"]
        config = self.client.get("/api/settings").json()["tagging"]
        all_domain_ids = [domain["id"] for domain in config["domains"]]
        self.client.put("/api/settings", json={**config, "prompt": PROMPT}).raise_for_status()
        stale_prompt = self.client.post(f"/api/conferences/{conference_id}/jobs", json={"kind": "tag", "domain_ids": all_domain_ids, "estimate_signature": old_stats["estimate_signature"]})
        self.assertEqual(stale_prompt.status_code, 400)
        selected_ids = [all_domain_ids[0]]
        stale = self.client.post(f"/api/conferences/{conference_id}/jobs",json={"kind":"tag","domain_ids":selected_ids,"estimate_signature":"stale"})
        self.assertEqual(stale.status_code,400)
        stats = self.client.post(f"/api/conferences/{conference_id}/tag-estimate", json={"domain_ids": selected_ids}).json()
        self.assertEqual(stats["domain_ids"], selected_ids)
        self.assertLess(stats["instruction_tokens"], self.client.get("/api/conferences").json()["conferences"][0]["stats"]["instruction_tokens"])
        from unittest.mock import Mock
        response = Mock()
        response.json.return_value = {"model":"jev-1.13.0","answers":{selected_ids[0]:{"type":"noul","noul":0.9}},"usage":{"input_tokens":1234,"output_tokens":5}}
        with patch("research_tagger.jobs.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value = response
            job_id = self.client.post(f"/api/conferences/{conference_id}/jobs",json={"kind":"tag","domain_ids":selected_ids,"estimate_signature":stats["estimate_signature"]}).json()["id"]
            self.assertEqual(self.wait_job(job_id)["status"],"completed")
            payload = client.return_value.__enter__.return_value.post.call_args.kwargs["json"]
            self.assertEqual(list(payload["questions"]), selected_ids)
            for question in payload["questions"].values():
                self.assertEqual(question["instructions"]["question"], "Using only title and abstract, does the paper study this domain?")
                self.assertNotIn("criteria", question)
        self.assertEqual(self.db.conference(conference_id)[1].domain_ids, selected_ids)
        result = self.db.rows("SELECT * FROM tag_results")[0]
        self.assertEqual(result["input_tokens"],1234)
        self.assertAlmostEqual(result["cost_usd"],1234*0.042/1_000_000)
        self.assertNotIn("synthetic-test-key",self.client.get("/api/settings").text)

    def test_tagging_continues_until_five_consecutive_errors_and_retries_only_failures(self):
        """Isolated failures persist, five consecutive failures stop, and retry excludes untouched papers."""
        from unittest.mock import Mock

        conference_id = self.conference()
        self.db.discover(conference_id, [
            {"title":f"Synthetic {identifier}", "source_url":f"https://eccv.ecva.net/virtual/2024/poster/{identifier}"}
            for identifier in range(1, 10)
        ])
        self.db.execute("UPDATE papers SET abstract='Synthetic abstract'")
        self.client.put("/api/key", json={"api_key":"synthetic-test-key"})
        config = self.client.get("/api/settings").json()["tagging"]
        self.client.put("/api/settings", json={**config, "parallel_requests":1}).raise_for_status()
        selected_ids = [config["domains"][0]["id"]]
        estimate = self.client.post(f"/api/conferences/{conference_id}/tag-estimate", json={"domain_ids":selected_ids}).json()
        success = Mock()
        success.json.return_value = {"model":"jev-1.13.0", "answers":{selected_ids[0]:{"type":"noul","noul":0.9}},
                                     "usage":{"input_tokens":100,"output_tokens":1}}
        first_run = [ValueError("temporary 1"), ValueError("temporary 2"), success,
                     ValueError("temporary 3"), ValueError("temporary 4"), ValueError("temporary 5"),
                     ValueError("temporary 6"), ValueError("temporary 7")]
        with patch("research_tagger.jobs.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.side_effect = first_run
            job_id = self.client.post(f"/api/conferences/{conference_id}/jobs", json={
                "kind":"tag", "domain_ids":selected_ids, "estimate_signature":estimate["estimate_signature"],
            }).json()["id"]
            job = self.wait_job(job_id)
            self.assertEqual(client.return_value.__enter__.return_value.post.call_count, 8)
        self.assertEqual((job["status"], job["completed"], job["failed"]), ("failed", 1, 7))
        self.assertIn("5 consecutive errors", job["error"])
        self.assertEqual(sum(bool(row["tag_error"]) for row in self.db.papers(conference_id)), 7)

        retry_estimate = self.client.post(f"/api/conferences/{conference_id}/tag-estimate", json={"domain_ids":selected_ids}).json()
        with patch("research_tagger.jobs.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value = success
            job_id = self.client.post(f"/api/conferences/{conference_id}/jobs", json={
                "kind":"tag", "domain_ids":selected_ids, "estimate_signature":retry_estimate["estimate_signature"],
                "retry_failed_only":True,
            }).json()["id"]
            retry = self.wait_job(job_id)
            self.assertEqual(client.return_value.__enter__.return_value.post.call_count, 7)
        self.assertEqual((retry["status"], retry["total"], retry["completed"], retry["failed"]), ("completed", 7, 7, 0))
        self.assertEqual(len(self.db.rows("SELECT id FROM tag_results")), 8)
        self.assertEqual(self.client.get("/api/conferences").json()["conferences"][0]["stats"]["ready_to_tag"], 1)

    def test_source_changes_do_not_mix_conferences(self):
        conference_id = self.conference()
        self.db.discover(conference_id,[{"title":"Synthetic","source_url":"https://eccv.ecva.net/virtual/2024/poster/1"}])
        response = self.client.put(f"/api/conferences/{conference_id}",json={**CONFERENCE,"year":2022})
        self.assertEqual(response.status_code,400)

    def test_cross_origin_mutations_are_rejected(self):
        response = self.client.post("/api/conferences",json=CONFERENCE,headers={"Origin":"https://unrelated.example"})
        self.assertEqual(response.status_code,403)

    def test_public_routes_and_assets(self):
        for url in ("/","/static/app.js","/static/style.css","/api/settings","/api/conferences"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code,200)

    def test_arbitrary_source_fields_and_empty_fields_fail(self):
        for config in ({**CONFERENCE,"fields":[]},{**CONFERENCE,"link_selector":"[broken"}):
            self.assertEqual(self.client.post("/api/conferences",json=config).status_code,422)

    def test_restart_marks_running_jobs_interrupted(self):
        conference_id = self.conference()
        self.db.execute("INSERT INTO jobs(id,conference_id,kind,status,created_at) VALUES ('interrupted-test',?,'scrape','running',?)",(conference_id,now()))
        self.db.initialize(ROOT / "domains.json")
        self.assertEqual(self.db.rows("SELECT status FROM jobs")[0]["status"],"interrupted")

    def test_cvpr_source_migration_preserves_records_and_is_repeatable(self):
        config = ConferenceSelection(name="CVPR", year=2025).to_config().model_dump(mode="json")
        config.update(source="virtual", index_url="https://cvpr.thecvf.com/virtual/2025/papers.html",
                      domain_ids=["world_models"])
        self.db.execute("INSERT INTO conferences(id,config) VALUES (77,?)", (json.dumps(config),))
        self.db.discover(77, [{"title": "Saved paper", "source_url": "https://cvpr.thecvf.com/virtual/2025/poster/1"}])
        self.db.execute("UPDATE papers SET abstract='Saved abstract' WHERE conference_id=77")
        before = self.db.papers(77)
        for _ in range(2):
            self.db.initialize(ROOT / "domains.json")
            migrated = self.db.conference(77)[1]
            self.assertEqual(migrated.source, "cvf")
            self.assertEqual(str(migrated.index_url), "https://openaccess.thecvf.com/CVPR2025?day=all")
            self.assertEqual(migrated.fields, ["title", "abstract"])
            self.assertEqual(migrated.domain_ids, ["world_models"])
            self.assertEqual(self.db.papers(77), before)

    def test_old_database_migration_preserves_content_and_is_repeatable(self):
        import sqlite3
        path = Path(self.temp.name) / "legacy.sqlite3"
        schema = (ROOT / "research_tagger/schema.sql").read_text()
        for column in ("conference_tags", "conference_tags_error", "conference_tags_source", "openreview_url", "openreview_primary_area", "openreview_keywords", "arxiv_error", "pdf_url", "pdf_error", "conference_tag", "primary_area", "keyword", "stage"):
            schema = schema.replace(f"    {column} TEXT,\n", "")
        settings = self.db.settings().model_dump_json()
        with sqlite3.connect(path) as connection:
            connection.executescript(schema)
            connection.execute("INSERT INTO settings VALUES ('tagging', ?)", (settings,))
            connection.execute("INSERT INTO conferences(id,config) VALUES (7,?)", (ConferenceSelection(**CONFERENCE).to_config().model_dump_json(),))
            connection.execute("INSERT INTO papers(id,conference_id,title,source_url,abstract) VALUES (9,7,'Old paper','https://eccv.ecva.net/virtual/2024/poster/9','Saved abstract')")
        migrated = Database(path)
        for _ in range(2):
            migrated.initialize(ROOT / "domains.json")
            saved = migrated.papers(7)[0]
            self.assertEqual((saved["id"], saved["abstract"], saved["conference_tags"]), (9, "Saved abstract", None))
            self.assertIsNone(saved["conference_tags_error"])
            self.assertIsNone(saved["openreview_keywords"])
            self.assertIsNone(saved["arxiv_error"])
            self.assertIsNone(saved["pdf_url"])
            self.assertIsNone(saved["pdf_error"])
            self.assertEqual(migrated.settings().model_dump_json(), settings)
        migrated.execute("UPDATE papers SET conference_tags='[]' WHERE id=9")
        migrated.initialize(ROOT / "domains.json")
        self.assertIsNone(migrated.papers(7)[0]["conference_tags"])



    def test_openreview_environment_is_ignored_and_auth_routes_are_removed(self):
        credentials = {'OPENREVIEW_USERNAME':'synthetic@example.test','OPENREVIEW_PASSWORD':'synthetic-password'}
        with patch.dict(os.environ, credentials), patch('httpx.HTTPTransport.handle_request',side_effect=AssertionError('Unexpected external request')):
            app = create_app(Path(self.temp.name) / 'no-openreview.sqlite3', ROOT / 'domains.json')
            with TestClient(app) as client:
                settings = client.get('/api/settings').json()
                self.assertFalse(any(key.startswith('openreview') for key in settings))
                for action in ('connect','send-code','verify'):
                    self.assertEqual(client.post('/api/openreview/' + action,json={}).status_code,404)
                self.assertEqual(client.put('/api/openreview-token',json={'token':'synthetic'}).status_code,404)
                self.assertEqual(client.get('/api/conferences').status_code,200)

    def test_scraping_saves_openreview_pdf_url_without_metadata_requests(self):
        identifier = self.conference()
        records = [{'title':f'Paper {i}','source_url':f'https://eccv.ecva.net/virtual/2024/poster/{i}'} for i in (1,2)]
        self.db.discover(identifier, records)
        pages = {row['source_url']:f'<h1 class="event-title">{row["title"]}</h1><div id="abstractText"><div class="abstract-text-inner">Saved conference abstract</div></div>' for row in records}
        pages[records[0]['source_url']] += '<a href="https://openreview.net/forum?id=Paper1">OpenReview</a>'
        pages[records[1]['source_url']] += '<a href="https://example.test/paper2.pdf">Paper PDF</a>'
        with self.scrape_pages(identifier, pages) as requested, patch('httpx.HTTPTransport.handle_request',side_effect=AssertionError('Unexpected external API request')):
            response = self.client.post(f'/api/conferences/{identifier}/jobs',json={'kind':'scrape'})
            job = self.wait_job(response.json()['id'])
        self.assertEqual((job['status'],job['completed'],job['failed']),('completed',2,0))
        self.assertFalse(any('openreview' in url for url in requested))
        for row in self.db.papers(identifier):
            self.assertEqual(row['abstract'],'Saved conference abstract')
            self.assertIsNone(row['conference_tags_error'])
            self.assertIsNone(row['openreview_url'])
        self.assertEqual(self.client.post(f'/api/conferences/{identifier}/jobs',json={'kind':'conference_tags'}).status_code,422)

    def test_removed_openreview_data_and_jobs_are_cleared(self):
        identifier = self.conference()
        self.db.discover(identifier,[{'title':'Paper 1','source_url':'https://eccv.ecva.net/virtual/2024/poster/1'}])
        self.db.execute("UPDATE papers SET abstract='Saved text',openreview_primary_area='Saved area',openreview_keywords='[\"Saved keyword\"]',conference_tags='[\"Saved area\",\"Saved keyword\"]',conference_tags_source='openreview',conference_tags_error='Old metadata error'")
        self.db.execute("INSERT INTO jobs(id,conference_id,kind,status,total,completed,failed,error,created_at) VALUES ('old-openreview-job',?,'conference_tags','failed',10,4,1,'Old OpenReview error',?)", (identifier, now()))
        self.db.initialize(ROOT / 'domains.json')
        saved = self.db.rows('SELECT * FROM papers')[0]
        self.assertEqual((saved['title'], saved['abstract']), ('Paper 1', 'Saved text'))
        for field in ('conference_tags','conference_tags_error','conference_tags_source','openreview_url','openreview_primary_area','openreview_keywords'):
            self.assertIsNone(saved[field])
        self.assertFalse(self.db.rows("SELECT id FROM jobs WHERE kind='conference_tags'"))
        self.assertFalse(any(job['kind'] == 'conference_tags' for job in self.client.get('/api/conferences').json()['jobs']))
        for field in ('primary_area','keyword','conference_tag'):
            self.assertEqual(self.client.get(f'/api/conferences/{identifier}/papers',params={field:'Old value'}).status_code,400)
            self.assertEqual(self.client.post(f'/api/conferences/{identifier}/jobs',json={'kind':'scrape',field:'Old value'}).status_code,422)
        page = self.client.get(f'/api/conferences/{identifier}/papers',params={'search':'Paper','status':'scraped'}).json()
        self.assertEqual((page['count'],page['stats']['ready_to_tag']),(1,1))
        self.assertFalse(any('conference_tag' in key for key in page['stats']))
        self.assertEqual(self.client.get(f'/api/conferences/{identifier}/papers',params={'search':'Missing'}).json()['count'],0)


if __name__ == "__main__":
    unittest.main()
