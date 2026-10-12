"""Local browser app and JSON API; launch with ``research-tagger-web``."""

import argparse
import asyncio
import json
import shutil
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import SecretStr
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .arxiv import ARXIV_ID
from .estimates import summarize
from .jobs import JobService
from .models import Record
from .storage import Database
from .web_models import ArxivUpdate, ConferenceConfig, ConferenceSelection, JobRequest, PaperFilterRequest, PromptTestRequest, TagEstimateRequest, TaggingConfig, conference_options


class KeyUpdate(Record):
    """API key held only in this server process; never echoed to the browser."""

    api_key: SecretStr


PAPER_STATUSES = {"all", "pending", "failed", "scraped", "tagged"}


def filter_papers(records: list[dict], tagging: TaggingConfig, search: str, status: str,
                  domain: list[str] | str | None) -> list[dict]:
    """Match search/status and ALL selected current domains at the saved threshold.

    An empty selection means all domains. Single-domain callers remain supported.
    """
    if status not in PAPER_STATUSES:
        raise ValueError("Unknown paper status.")
    domains = {value for value in (domain if isinstance(domain, list) else [domain]) if value}
    if not domains <= {item.id for item in tagging.domains}:
        raise ValueError("Unknown research tag.")
    return [paper for paper in records if (not search or search.casefold() in paper["title"].casefold())
            and (status == "all" or paper["status"] == status)
            and (not domains or paper["probabilities"] and all(
                item in paper["probabilities"] and paper["probabilities"][item] >= tagging.threshold for item in domains))]


def ris_text(records: list[dict], config: ConferenceConfig, tagging: TaggingConfig) -> str:
    """Serialize metadata/tags as RIS, using saved PDF URLs as item URLs, not attachments.

    Preserve displaced conference/arXiv page URLs in notes. Without a PDF URL,
    retain the arXiv page or conference page as the item URL. No files are fetched.
    """
    domains = {domain.id: domain.label for domain in tagging.domains}

    def clean(value: object) -> str:
        """Keep each RIS field on one physical line and remove empty whitespace."""
        return " ".join(str(value or "").split())

    entries = []
    for paper in records:
        lines = ["TY  - CONF", f"TI  - {clean(paper['title'])}", f"T2  - {config.name}", f"PY  - {config.year}"]
        if paper["abstract"]:
            lines.append(f"AB  - {clean(paper['abstract'])}")
        pdf_url = paper.get("pdf_url")
        if pdf_url:
            lines.extend((f"UR  - {clean(pdf_url)}", f"N1  - Conference page: {clean(paper['source_url'])}"))
        if paper["arxiv_id"]:
            article_id = clean(paper["arxiv_id"])
            lines.extend(("DB  - arXiv", f"AN  - {article_id}"))
            if pdf_url:
                lines.append(f"N1  - arXiv page: https://arxiv.org/abs/{article_id}")
            else:
                lines.extend((f"UR  - https://arxiv.org/abs/{article_id}",
                              f"N1  - Conference page: {clean(paper['source_url'])}"))
        elif not pdf_url:
            lines.append(f"UR  - {clean(paper['source_url'])}")
        for domain_id, probability in (paper["probabilities"] or {}).items():
            if probability >= tagging.threshold and domain_id in domains:
                lines.append(f"KW  - {clean(domains[domain_id])}")
        lines.append("ER  -")
        entries.append("\r\n".join(lines))
    return "\r\n\r\n".join(entries) + ("\r\n" if entries else "")


def create_app(database_path: Path | None = None, domains_path: Path | None = None) -> FastAPI:
    """Build the app; seed a missing default database from the bundled snapshot.

    Explicit database paths start empty when missing. Existing databases are
    always reused. A missing bundled snapshot fails default first startup.
    """
    root = Path(__file__).resolve().parent.parent
    db = Database(database_path or root / "data" / "research-tagger.sqlite3")
    service = JobService(db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if database_path is None and not db.path.exists():
            with (root / "dataset" / "research-tagger.sqlite3").open("rb") as source:
                with db.path.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
        db.initialize(domains_path or root / "domains.json")
        yield
        service.close()

    app = FastAPI(title="PaperRadar", lifespan=lifespan)
    app.state.db = db
    app.state.service = service
    mutation_lock = asyncio.Lock()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        """Reject cross-origin mutations; keep keys and scraping controls local."""
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return JSONResponse({"detail": "Cross-origin changes are not allowed."}, status_code=403)
            if not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"detail": "Changes require application/json."}, status_code=415)
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            async with mutation_lock:
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def bad_input(request: Request, error: ValueError):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request: Request, error: sqlite3.IntegrityError):
        return JSONResponse({"detail": "This entry already exists or a job is already running."}, status_code=409)

    @app.get("/api/settings")
    def settings():
        return {"tagging": db.settings().model_dump(mode="json"), "api_key_configured": bool(service.api_key.strip()),
                "conference_options": conference_options()}

    @app.put("/api/settings")
    def update_settings(config: TaggingConfig):
        """Save tagging settings and reconcile conference domain selections."""
        service.assert_idle()
        valid_ids = {domain.id for domain in config.domains}
        with db.connect() as conn:
            conn.execute("UPDATE settings SET value=? WHERE key='tagging'", (config.model_dump_json(),))
            for row in conn.execute("SELECT id, config FROM conferences"):
                conference = ConferenceConfig.model_validate_json(row["config"])
                if conference.domain_ids is not None:
                    retained = [domain_id for domain_id in conference.domain_ids if domain_id in valid_ids]
                    conference = conference.model_copy(update={"domain_ids": retained or None})
                    conn.execute("UPDATE conferences SET config=? WHERE id=?", (conference.model_dump_json(), row["id"]))
        return settings()

    @app.put("/api/key")
    def update_key(config: KeyUpdate):
        service.assert_idle()
        key = config.api_key.get_secret_value().strip()
        if not key:
            raise ValueError("Enter a nonempty API key.")
        service.api_key = key
        return {"api_key_configured": True}

    @app.post("/api/prompt-test/estimate")
    def estimate_prompt_test(request: PromptTestRequest):
        """Estimate a small test of the submitted draft prompt without calling Jev."""
        return service.prompt_test_preview(request)["preview"]

    @app.post("/api/prompt-test")
    def run_prompt_test(request: PromptTestRequest):
        """Run the exact reviewed prompt test without saving results as paper tags."""
        return service.prompt_test(request)

    @app.post("/api/conferences/{conference_id}/tag-estimate")
    def estimate_conference_tags(conference_id: int, request: TagEstimateRequest):
        """Estimate the exact selected domain questions before a tagging job."""
        return service.tag_estimate(conference_id, request.domain_ids)

    @app.get("/api/conferences")
    def conferences():
        output = []
        for row in db.rows("SELECT * FROM conferences ORDER BY id"):
            stats, _ = service.summary(row["id"])
            output.append({"id": row["id"], "config": json.loads(row["config"]), "discovered_at": row["discovered_at"], "stats": stats})
        return {"conferences": output, "jobs": db.rows("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20")}

    @app.post("/api/conferences", status_code=201)
    def add_conference(selection: ConferenceSelection):
        service.assert_idle()
        config = selection.to_config()
        for row in db.rows("SELECT config FROM conferences"):
            other = ConferenceConfig.model_validate_json(row["config"])
            if other.name.casefold() == config.name.casefold() and other.year == config.year:
                raise ValueError("This conference edition already exists.")
        with db.connect() as conn:
            cursor = conn.execute("INSERT INTO conferences(config) VALUES (?)", (config.model_dump_json(),))
            return {"id": cursor.lastrowid}

    @app.put("/api/conferences/{conference_id}")
    def update_conference(conference_id: int, selection: ConferenceSelection):
        service.assert_idle()
        _, previous = db.conference(conference_id)
        if (previous.name, previous.year) == (selection.name, selection.year):
            config = selection.to_config().model_copy(update={"domain_ids": previous.domain_ids})
        else:
            if db.rows("SELECT id FROM papers WHERE conference_id=? LIMIT 1", (conference_id,)):
                raise ValueError("This edition already has papers. Add a new conference for a different year or name.")
            config = selection.to_config()
            for row in db.rows("SELECT id, config FROM conferences WHERE id!=?", (conference_id,)):
                other = json.loads(row["config"])
                if (other["name"], other["year"]) == (selection.name, selection.year):
                    raise ValueError("This conference edition already exists.")
        db.execute("UPDATE conferences SET config=? WHERE id=?", (config.model_dump_json(), conference_id))
        return {"id": conference_id}

    @app.get("/api/conferences/{conference_id}/papers")
    def papers(conference_id: int, search: str = "", status: str = "all",
               domain: Annotated[list[str] | None, Query()] = None, page: int = 1,
               primary_area: str | None = None, keyword: str | None = None, conference_tag: str | None = None):
        if primary_area is not None or keyword is not None or conference_tag is not None:
            raise ValueError("Source-label filters have been removed. Use title search, status, or tag filters.")
        stats, records = service.summary(conference_id)
        if page < 1:
            raise ValueError("Page must be positive.")
        tagging = db.settings()
        records = filter_papers(records, tagging, search, status, domain)
        count = len(records)
        arxiv_missing = sum(not paper["arxiv_id"] for paper in records)
        pdf_missing = sum(not paper["pdf_url"] for paper in records)
        records = records[(page - 1) * 30:page * 30]
        for paper in records:
            paper["has_abstract"] = bool(paper.pop("abstract"))
            paper["has_introduction"] = bool(paper.pop("introduction"))
        return {"papers": records, "count": count, "arxiv_missing": arxiv_missing, "pdf_missing": pdf_missing, "page": page, "stats": stats}

    @app.post("/api/conferences/{conference_id}/pdf-links", status_code=202)
    def collect_pdf_links(conference_id: int, selection: PaperFilterRequest):
        """Collect only URLs for unresolved papers matching the current unpaginated filters."""
        _, records = service.summary(conference_id)
        records = filter_papers(records, db.settings(), selection.search, selection.status, selection.domain or "")
        return {"id": service.start_pdf_links(conference_id, [paper["id"] for paper in records if not paper["pdf_url"]])}

    @app.post("/api/conferences/{conference_id}/arxiv-links", status_code=202)
    def collect_arxiv_links(conference_id: int, selection: PaperFilterRequest):
        """Queue metadata-only arXiv lookups for unresolved papers matching the current view."""
        _, records = service.summary(conference_id)
        records = filter_papers(records, db.settings(), selection.search, selection.status, selection.domain or "")
        paper_ids = [paper["id"] for paper in records if not paper["arxiv_id"]]
        return {"id":service.start_arxiv_links(conference_id, paper_ids)}

    @app.get("/api/conferences/{conference_id}/papers.ris")
    def export_papers(conference_id: int, search: str = "", status: str = "all",
                      domain: Annotated[list[str] | None, Query()] = None):
        """Download all papers matching the current browser filters for Zotero import."""
        _, config = db.conference(conference_id)
        _, records = service.summary(conference_id)
        tagging = db.settings()
        records = filter_papers(records, tagging, search, status, domain)
        if not records:
            raise ValueError("No papers match the current filters.")
        filename = f"{config.name}-{config.year}-papers.ris"
        return Response(ris_text(records, config, tagging), media_type="application/x-research-info-systems",
                        headers={"Content-Type":"application/x-research-info-systems; charset=utf-8",
                                 "Content-Disposition":f'attachment; filename="{filename}"'})

    @app.get("/api/conferences/{conference_id}/zotero")
    def zotero_papers(conference_id: int, search: str = "", status: str = "all",
                      domain: Annotated[list[str] | None, Query()] = None, paper_id: int | None = None):
        """Read-only Connector metadata: all filtered papers, or one matching paper.

        Version 1 includes saved text, source/PDF links, arXiv ID, and current
        above-threshold tag names. It excludes introductions, errors, and secrets;
        no PDF requests or database writes occur. Empty lists are valid.
        """
        _, config = db.conference(conference_id)
        _, records = service.summary(conference_id)
        tagging = db.settings()
        records = filter_papers(records, tagging, search, status, domain)
        if paper_id is not None:
            records = [record for record in records if record["id"] == paper_id]
            if not records:
                raise HTTPException(404, "Paper not found in this selection.")
        return {"version": 1, "papers": [
            {"id": record["id"], "title": record["title"], "abstract": record["abstract"],
             "conference": config.name, "year": config.year, "source_url": record["source_url"],
             "pdf_url": record["pdf_url"], "arxiv_id": record["arxiv_id"],
             "tags": [domain.label for domain in tagging.domains
                      if (record["probabilities"] or {}).get(domain.id, -1) >= tagging.threshold]}
            for record in records]}

    @app.get("/api/papers/{paper_id}")
    def paper(paper_id: int):
        """Return saved content plus current-signature probabilities and token estimate."""
        rows = db.rows("SELECT * FROM papers WHERE id=?", (paper_id,))
        if not rows:
            raise HTTPException(404, "Paper not found.")
        _, config = db.conference(rows[0]["conference_id"])
        results = db.rows("SELECT * FROM tag_results WHERE paper_id=?", (paper_id,))
        _, papers = summarize(rows, results, config, service.tagging_config(config.domain_ids))
        return papers[0]

    @app.put("/api/papers/{paper_id}/arxiv")
    def arxiv(paper_id: int, value: ArxivUpdate):
        service.assert_idle()
        paper(paper_id)
        if not ARXIV_ID.fullmatch(value.arxiv_id):
            raise ValueError("Invalid arXiv ID.")
        db.execute("UPDATE papers SET arxiv_id=?, arxiv_error=NULL, introduction=NULL, introduction_error=NULL WHERE id=?", (value.arxiv_id, paper_id))
        return {"saved": True}

    @app.post("/api/conferences/{conference_id}/jobs", status_code=202)
    def start_job(conference_id: int, request: JobRequest):
        return {"id": service.start(conference_id, request)}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=? AND status IN ('queued','running')", (job_id,))
        return {"requested": True}

    static = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app


def main() -> None:
    """Start a single-process, loopback-only web server and print its browser URL."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database", type=Path, help="SQLite path; defaults to project data/research-tagger.sqlite3")
    args = parser.parse_args()
    uvicorn.run(create_app(args.database), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
