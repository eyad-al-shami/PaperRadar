"""Persistent, resumable scrape/tag jobs processed by one local background worker."""

import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from typing import Annotated

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .arxiv import fetch_introduction, find_arxiv
from .classifier import API_URL, JevResponse
from .crawling import CrawlResult, crawl_conference, discover_conference
from .estimates import canonical, estimate_payload, needs_scrape, payload_for, ready, signature, summarize
from .network import USER_AGENT
from .storage import Database, now
from .web_models import ConferenceConfig, JobRequest, PromptTestRequest, TaggingConfig


class Usage(BaseModel):
    """Actual billed input/output counts supplied by the TypeSafe API."""

    model_config = ConfigDict(strict=True)
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]


class BilledResponse(JevResponse):
    """Classification with required actual usage; never invent token counts."""

    usage: Usage


MAX_CONSECUTIVE_TAG_ERRORS = 4
"""Continue through this many consecutive paper failures; stop on the next one."""


class JobService:
    """Own one worker and a memory-only API key; all durable state lives in SQLite."""

    def __init__(self, database: Database):
        self.db = database
        self.api_key = os.environ.get("TYPESAFE_API_KEY", "")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-tagger")

    def close(self) -> None:
        """Request cancellation and wait for in-flight network calls to finish."""
        self.db.execute("UPDATE jobs SET cancel_requested=1 WHERE status IN ('queued','running')")
        self.executor.shutdown(wait=True)

    def assert_idle(self) -> None:
        """Reject configuration changes during a running job to preserve its inputs."""
        if self.db.rows("SELECT id FROM jobs WHERE status IN ('queued','running')"):
            raise ValueError("A job is running. Stop it before changing settings or starting another job.")

    def summary(self, conference_id: int) -> tuple[dict, list[dict]]:
        """Summarize conference content, Jev classifications, and token/cost usage."""
        _, config = self.db.conference(conference_id)
        return self.summary_for_domains(conference_id, config.domain_ids)

    def tagging_config(self, domain_ids: list[str] | None = None) -> TaggingConfig:
        """Return saved tagging settings restricted to known domains in saved order."""
        tagging = self.db.settings()
        if domain_ids is None:
            return tagging
        selected = set(domain_ids)
        known = {domain.id for domain in tagging.domains}
        if not selected <= known:
            raise ValueError("One or more selected research tags no longer exist in Settings.")
        return tagging.model_copy(update={"domains": [domain for domain in tagging.domains if domain.id in selected]})

    def summary_for_domains(self, conference_id: int, domain_ids: list[str] | None) -> tuple[dict, list[dict]]:
        """Summarize one conference using all domains or an explicit selected subset."""
        _, config = self.db.conference(conference_id)
        papers = self.db.papers(conference_id)
        results = self.db.rows(
            "SELECT t.* FROM tag_results t JOIN papers p ON p.id=t.paper_id WHERE p.conference_id=?", (conference_id,),
        )
        tagging = self.tagging_config(domain_ids)
        stats, enriched = summarize(papers, results, config, tagging)
        stats["domain_ids"] = [domain.id for domain in tagging.domains]
        return stats, enriched

    def tag_estimate(self, conference_id: int, domain_ids: list[str]) -> dict:
        """Estimate conference tagging for the selected domain questions without saving it."""
        self.assert_idle()
        stats, _ = self.summary_for_domains(conference_id, domain_ids)
        return stats

    def prompt_test_preview(self, request: PromptTestRequest) -> dict:
        """Estimate the exact deterministic prompt-test sample without calling Jev."""
        self.assert_idle()
        _, conference = self.db.conference(request.conference_id)
        papers = [paper for paper in self.db.papers(request.conference_id) if ready(paper, conference)][:request.limit]
        if not papers:
            raise ValueError("This conference has no complete papers for its selected content fields.")
        payloads = [payload_for(paper, conference, request.tagging) for paper in papers]
        estimates = [estimate_payload(payload) for payload in payloads]
        preview = {
            "paper_count": len(papers),
            "input_tokens": sum(item["input_tokens"] for item in estimates),
            "paper_tokens": sum(item["paper_tokens"] for item in estimates),
            "instruction_tokens": sum(item["instruction_tokens"] for item in estimates),
            "estimated_cost": sum(item["input_tokens"] for item in estimates) * request.tagging.price_per_million / 1_000_000,
            "low_cost": sum(item["low_tokens"] for item in estimates) * request.tagging.price_per_million / 1_000_000,
            "high_cost": sum(item["high_tokens"] for item in estimates) * request.tagging.price_per_million / 1_000_000,
            "estimate_signature": signature({
                "conference_id": request.conference_id,
                "papers": [(paper["id"], signature(payload)) for paper, payload in zip(papers, payloads)],
                "price": request.tagging.price_per_million,
            }),
        }
        return {"preview": preview, "papers": papers, "payloads": payloads}

    def prompt_test(self, request: PromptTestRequest) -> dict:
        """Call Jev for a reviewed sample and return transient probabilities and usage."""
        if not self.api_key.strip():
            raise ValueError("Add your TypeSafe API key in Settings before testing a prompt.")
        sample = self.prompt_test_preview(request)
        preview = sample["preview"]
        if request.estimate_signature != preview["estimate_signature"]:
            raise ValueError("The prompt-test estimate changed. Review the test again before running it.")
        results = []
        with httpx.Client(timeout=45, headers={"User-Agent": USER_AGENT}) as client:
            for paper, payload in zip(sample["papers"], sample["payloads"]):
                try:
                    response = client.post(API_URL, headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
                    response.raise_for_status()
                    decision = BilledResponse.model_validate(response.json())
                    if set(decision.answers) != set(payload["questions"]):
                        raise ValueError("The model returned mismatched tag IDs.")
                except (httpx.HTTPError, ValueError) as error:
                    raise ValueError(f"Prompt test failed for {paper['title']}: {error}") from error
                input_tokens = decision.usage.input_tokens
                results.append({
                    "paper_id": paper["id"],
                    "title": paper["title"],
                    "model": decision.model,
                    "probabilities": {key: answer.noul for key, answer in decision.answers.items()},
                    "input_tokens": input_tokens,
                    "output_tokens": decision.usage.output_tokens,
                    "cost_usd": input_tokens * request.tagging.price_per_million / 1_000_000,
                })
        return {
            "preview": preview,
            "actual_input_tokens": sum(item["input_tokens"] for item in results),
            "actual_output_tokens": sum(item["output_tokens"] for item in results),
            "actual_cost": sum(item["cost_usd"] for item in results),
            "results": results,
        }

    def start(self, conference_id: int, request: JobRequest) -> str:
        """Persist one job, validating the displayed estimate before any paid call."""
        self.assert_idle()
        conference, config = self.db.conference(conference_id)
        if request.kind == "tag" and not conference["discovered_at"]:
            raise ValueError("Scrape conference papers before tagging.")
        stats, papers = (self.summary_for_domains(conference_id, request.domain_ids)
                         if request.kind == "tag" else self.summary(conference_id))
        if request.kind == "tag":
            if not self.api_key.strip():
                raise ValueError("Add your TypeSafe API key in Settings before tagging.")
            if request.estimate_signature != stats["estimate_signature"]:
                raise ValueError("The tagging estimate changed. Refresh and review it before starting.")
            if not stats["ready_to_tag"]:
                raise ValueError("No complete, untagged papers are ready for the selected fields.")
            if request.retry_failed_only and not any(
                paper["status"] == "scraped" and paper["tag_error"] and not paper["context_error"] for paper in papers
            ):
                raise ValueError("There are no failed tagging requests to retry for this selection.")
        job_id = uuid.uuid4().hex
        try:
            with self.db.connect() as conn:
                if request.kind == "tag":
                    config = config.model_copy(update={"domain_ids": request.domain_ids})
                    conn.execute("UPDATE conferences SET config=? WHERE id=?", (config.model_dump_json(), conference_id))
                conn.execute(
                    "INSERT INTO jobs(id, conference_id, kind, status, stage, created_at) VALUES (?, ?, ?, 'queued', ?, ?)",
                    (job_id, conference_id, request.kind, "discovering" if request.kind == "scrape" else None, now()),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError("Another job is already running.") from error
        self.executor.submit(self.run, job_id, conference_id, request)
        return job_id

    def cancelled(self, job_id: str) -> bool:
        """Read durable cancellation so a user can stop submitting papers and finish in-flight requests."""
        return bool(self.db.rows("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,))[0]["cancel_requested"])

    def start_pdf_links(self, conference_id: int, paper_ids: list[int]) -> str:
        """Queue link-only collection for matching saved papers without a PDF URL."""
        self.assert_idle()
        selected = set(paper_ids)
        papers = [paper for paper in self.db.papers(conference_id) if paper["id"] in selected and not paper["pdf_url"]]
        if not papers:
            raise ValueError("No matching papers need PDF links.")
        if len(papers) != len(selected):
            raise ValueError("The PDF selection contains an unavailable or already resolved paper.")
        job_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO jobs(id,conference_id,kind,status,total,created_at) VALUES (?,?,?,'queued',?,?)",
            (job_id, conference_id, "pdf_links", len(papers), now()),
        )
        self.executor.submit(self.run_pdf_links, job_id, conference_id, papers)
        return job_id

    def save_pdf_link(self, result: CrawlResult) -> None:
        """Persist a PDF URL or its collection error independently of paper text."""
        if result.get("pdf_url"):
            self.db.execute("UPDATE papers SET pdf_url=?, pdf_error=NULL WHERE id=?",
                            (result["pdf_url"], result["paper"]["id"]))
        elif result.get("pdf_error"):
            self.db.execute("UPDATE papers SET pdf_error=? WHERE id=?", (result["pdf_error"], result["paper"]["id"]))

    def run_pdf_links(self, job_id: str, conference_id: int, papers: list[dict]) -> None:
        """Collect URLs from official HTML; skip PDF downloads and OpenReview API calls.

        ICCV reads its index once. Other conferences visit matching paper pages,
        following CVF landing pages when needed. Each result commits immediately.
        """
        self.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        finished = set()

        def receive(result: CrawlResult) -> None:
            self.save_pdf_link(result)
            success = bool(result.get("pdf_url"))
            self.db.execute("UPDATE jobs SET completed=completed+?, failed=failed+? WHERE id=?",
                            (int(success), int(not success), job_id))
            finished.add(result["paper"]["id"])

        try:
            _, config = self.db.conference(conference_id)
            if config.source == "cvf":
                index = {item["source_url"]: item for item in discover_conference(config, lambda: self.cancelled(job_id))}
                for paper in papers:
                    if self.cancelled(job_id):
                        break
                    item = index.get(paper["source_url"])
                    if not item or item["title"] != paper["title"]:
                        receive({"paper": paper, "pdf_error": "Paper missing or title changed in the conference index."})
                    elif item.get("pdf_url"):
                        receive({"paper": paper, "pdf_url": item["pdf_url"]})
                    else:
                        receive({"paper": paper, "pdf_error": item.get("pdf_error") or "No PDF link provided in the conference index."})
            else:
                crawl_conference(config, papers, receive, lambda: self.cancelled(job_id), links_only=True)
            if not self.cancelled(job_id) and finished != {paper["id"] for paper in papers}:
                raise ValueError("Scrapy did not return every requested PDF link result.")
            self.finish(job_id, "cancelled" if self.cancelled(job_id) else "completed")
        except Exception as error:
            self.finish(job_id, "failed", str(error))

    def start_arxiv_links(self, conference_id: int, paper_ids: list[int]) -> str:
        """Queue exact-title arXiv metadata lookups for selected papers missing an ID."""
        self.assert_idle()
        selected = set(paper_ids)
        papers = [paper for paper in self.db.papers(conference_id) if paper["id"] in selected and not paper["arxiv_id"]]
        if not papers:
            raise ValueError("All matching papers already have arXiv links, or no papers match the current filters.")
        if len(papers) != len(selected):
            raise ValueError("The arXiv selection contains an unavailable or already resolved paper.")
        job_id = uuid.uuid4().hex
        try:
            self.db.execute(
                "INSERT INTO jobs(id,conference_id,kind,status,total,created_at) VALUES (?,?,?,'queued',?,?)",
                (job_id, conference_id, "arxiv_links", len(papers), now()),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("Another job is already running.") from error
        self.executor.submit(self.run_arxiv_links, job_id, papers)
        return job_id

    def run_arxiv_links(self, job_id: str, papers: list[dict]) -> None:
        """Save verified arXiv IDs and lookup failures without downloading PDFs or TeX."""
        self.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        try:
            with httpx.Client(timeout=45, headers={"User-Agent": USER_AGENT}) as client:
                for paper in papers:
                    if self.cancelled(job_id):
                        self.finish(job_id, "cancelled")
                        return
                    try:
                        article_id = find_arxiv(client, paper["title"])
                        self.db.execute("UPDATE papers SET arxiv_id=?, arxiv_error=NULL WHERE id=?", (article_id, paper["id"]))
                        self.db.execute("UPDATE jobs SET completed=completed+1 WHERE id=?", (job_id,))
                    except Exception as error:
                        self.db.execute("UPDATE papers SET arxiv_error=? WHERE id=?", (str(error), paper["id"]))
                        self.db.execute("UPDATE jobs SET failed=failed+1 WHERE id=?", (job_id,))
            self.finish(job_id, "completed")
        except Exception as error:
            self.finish(job_id, "failed", str(error))

    def run(self, job_id: str, conference_id: int, request: JobRequest) -> None:
        """Refresh the index before scraping missing content; persist stage and per-paper results."""
        self.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        try:
            _, config = self.db.conference(conference_id)
            tagging = self.tagging_config(config.domain_ids)
            with httpx.Client(timeout=45, headers={"User-Agent": USER_AGENT}) as client:
                if request.kind in {"discover", "scrape"}:
                    indexed = discover_conference(config, lambda: self.cancelled(job_id))
                    if self.cancelled(job_id):
                        self.finish(job_id, "cancelled")
                        return
                    self.db.discover(conference_id, indexed)
                    if request.kind == "discover":
                        self.db.execute("UPDATE jobs SET total=?, completed=? WHERE id=?", (len(indexed), len(indexed), job_id))
                        self.finish(job_id, "completed")
                        return
                    self.db.execute("UPDATE jobs SET stage='scraping' WHERE id=?", (job_id,))
                if request.kind in {"scrape", "tag"}:
                    _, papers = self.summary(conference_id)
                    papers = [paper for paper in papers if (
                        needs_scrape(paper, config) if request.kind == "scrape"
                        else paper["status"] == "scraped" and not paper["context_error"]
                    )]
                    if request.kind == "tag" and request.retry_failed_only:
                        papers = [paper for paper in papers if paper["tag_error"]]
                    papers = papers[:request.limit]
                    self.db.execute("UPDATE jobs SET total=? WHERE id=?", (len(papers), job_id))
                    if request.kind == "scrape":
                        self.scrape(job_id, config, papers, client)
                        self.finish(job_id, "cancelled" if self.cancelled(job_id) else "completed")
                        return
                    self.tag_papers(job_id, config, tagging, papers, client)
                    self.finish(job_id, "cancelled" if self.cancelled(job_id) else "completed")
                    return
        except Exception as error:
            self.finish(job_id, "failed", str(error))

    def tag_papers(self, job_id: str, config: ConferenceConfig, tagging: TaggingConfig,
                   papers: list[dict], client: httpx.Client) -> None:
        """Bound concurrent requests, persisting each result on the coordinator thread.

        Completion callbacks enter a FIFO queue. Five consecutive failures in that
        order stop replenishment permanently for this run. Cancellation also stops
        new submissions; already submitted requests finish and are saved before
        returning. The HTTP client stays open until the request pool is drained.
        """
        def classify(paper: dict) -> tuple[dict, BilledResponse]:
            """Send one paper with all selected tags; perform no database writes."""
            payload = payload_for(paper, config, tagging)
            response = client.post(API_URL, headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
            response.raise_for_status()
            decision = BilledResponse.model_validate(response.json())
            if set(decision.answers) != set(payload["questions"]):
                raise ValueError("The model returned mismatched tag IDs.")
            return payload, decision

        completed = Queue()
        pending = {}
        remaining = iter(papers)
        exhausted = False
        consecutive_errors = 0
        stop_error = None
        with ThreadPoolExecutor(max_workers=tagging.parallel_requests, thread_name_prefix="tag-request") as pool:
            while True:
                # Handle completed requests before launching replacements so an
                # already-reached error threshold cannot queue more paid work.
                while (not exhausted and not stop_error and not self.cancelled(job_id)
                       and len(pending) < tagging.parallel_requests and completed.empty()):
                    paper = next(remaining, None)
                    if paper is None:
                        exhausted = True
                        break
                    future = pool.submit(classify, paper)
                    pending[future] = paper
                    future.add_done_callback(completed.put)
                if not pending:
                    break
                future = completed.get()
                paper = pending.pop(future)
                try:
                    payload, decision = future.result()
                except Exception as error:
                    with self.db.connect() as conn:
                        conn.execute("UPDATE papers SET tag_error=? WHERE id=?", (str(error), paper["id"]))
                        conn.execute("UPDATE jobs SET failed=failed+1 WHERE id=?", (job_id,))
                    consecutive_errors += 1
                    if consecutive_errors > MAX_CONSECUTIVE_TAG_ERRORS and stop_error is None:
                        stop_error = f"Tagging stopped after {consecutive_errors} consecutive errors. Last error: {error}"
                else:
                    with self.db.connect() as conn:
                        conn.execute(
                            "INSERT INTO tag_results(paper_id, signature, probabilities, model, input_tokens, output_tokens, cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (paper["id"], signature(payload), canonical({key: value.noul for key, value in decision.answers.items()}),
                             decision.model, decision.usage.input_tokens, decision.usage.output_tokens,
                             decision.usage.input_tokens * tagging.price_per_million / 1_000_000, now()),
                        )
                        conn.execute("UPDATE papers SET tag_error=NULL WHERE id=?", (paper["id"],))
                        conn.execute("UPDATE jobs SET completed=completed+1 WHERE id=?", (job_id,))
                    consecutive_errors = 0
        if stop_error:
            raise RuntimeError(stop_error)

    def scrape(self, job_id: str, config: ConferenceConfig, papers: list[dict], client: httpx.Client) -> None:
        """Save Scrapy results immediately, then fetch any selected missing introduction.

        Each paper contributes once to progress. Already saved fields are untouched;
        missing abstracts or PDF links go through one HTML crawl. Abstracts and
        links are always collected; selected fields only govern Jev input and
        optional introduction extraction. arXiv keeps its own rate limit.
        """
        finished = set()

        def receive(result: CrawlResult) -> None:
            paper = result["paper"]
            self.save_pdf_link(result)
            failed = "error" in result or "pdf_error" in result
            if "error" in result and not paper["abstract"]:
                self.db.execute("UPDATE papers SET abstract_error=? WHERE id=?", (result["error"], paper["id"]))
            elif "abstract" in result and not paper["abstract"]:
                self.db.execute("UPDATE papers SET abstract=?, abstract_error=NULL, scraped_at=? WHERE id=?",
                                (result["abstract"], now(), paper["id"]))
            complete(paper, failed)

        def complete(paper: dict, failed: bool) -> None:
            if "introduction" in config.fields and not paper["introduction"]:
                try:
                    article_id, text = fetch_introduction(client, paper["title"], paper["arxiv_id"])
                    self.db.execute("UPDATE papers SET introduction=?, arxiv_id=?, arxiv_error=NULL, introduction_error=NULL, scraped_at=? WHERE id=?",
                                    (text, article_id, now(), paper["id"]))
                except Exception as error:
                    self.db.execute("UPDATE papers SET introduction_error=? WHERE id=?", (str(error), paper["id"]))
                    failed = True
            finished.add(paper["id"])
            self.db.execute("UPDATE jobs SET completed=completed+?, failed=failed+? WHERE id=?",
                            (int(not failed), int(failed), job_id))

        metadata = []
        for paper in papers:
            if self.cancelled(job_id):
                return
            if not paper["abstract"] or not paper["pdf_url"]:
                metadata.append(paper)
            else:
                complete(paper, False)
        if metadata and not self.cancelled(job_id):
            crawl_conference(config, metadata, receive, lambda: self.cancelled(job_id))
        if not self.cancelled(job_id) and finished != {paper["id"] for paper in papers}:
            raise ValueError("Scrapy did not return every requested paper.")

    def finish(self, job_id: str, status: str, error: str | None = None) -> None:
        """Persist terminal status; counts retain per-paper successes and failures."""
        self.db.execute("UPDATE jobs SET status=?, error=?, finished_at=? WHERE id=?", (status, error, now(), job_id))
