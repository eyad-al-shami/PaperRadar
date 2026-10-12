"""Small SQLite store; connections are short lived and each paper commits separately."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter

from .models import Domain
from .web_models import CONFERENCE_URLS, ConferenceConfig, TaggingConfig


def now() -> str:
    """UTC timestamp stored with every successful discovery, scrape, and tag."""
    return datetime.now(timezone.utc).isoformat()


class Database:
    """File-backed SQLite access with foreign keys, WAL, and parameterized queries."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        """Open a transactional connection and always close it after use."""
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self, domains_path: Path) -> None:
        """Migrate storage/CVPR sources, retire OpenReview data and interrupt unfinished jobs."""
        with self.connect() as conn:
            conn.executescript(Path(__file__).with_name("schema.sql").read_text())
            for row in conn.execute("SELECT id, config FROM conferences").fetchall():
                config = json.loads(row["config"])
                if (config.get("name") == "CVPR" and config.get("source") == "virtual"
                        and config.get("index_url") == f"https://cvpr.thecvf.com/virtual/{config['year']}/papers.html"):
                    config.update(source="cvf", index_url=CONFERENCE_URLS["CVPR"].format(year=config["year"]))
                    migrated = ConferenceConfig.model_validate(config)
                    conn.execute("UPDATE conferences SET config=? WHERE id=?", (migrated.model_dump_json(), row["id"]))
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(papers)")}
            for column in ("conference_tags", "conference_tags_error", "conference_tags_source", "openreview_url", "openreview_primary_area", "openreview_keywords", "arxiv_error", "pdf_url", "pdf_error"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE papers ADD COLUMN {column} TEXT")
            conn.execute(
                "UPDATE papers SET conference_tags=NULL, conference_tags_error=NULL, conference_tags_source=NULL, "
                "openreview_url=NULL, openreview_primary_area=NULL, openreview_keywords=NULL "
                "WHERE conference_tags IS NOT NULL OR conference_tags_error IS NOT NULL OR conference_tags_source IS NOT NULL "
                "OR openreview_url IS NOT NULL OR openreview_primary_area IS NOT NULL OR openreview_keywords IS NOT NULL"
            )
            job_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
            for column in ("conference_tag", "stage"):
                if column not in job_columns:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT")
            conn.execute("DELETE FROM jobs WHERE kind='conference_tags'")
            saved = conn.execute("SELECT value FROM settings WHERE key='tagging'").fetchone()
            if not saved:
                domains = TypeAdapter(list[Domain]).validate_json(domains_path.read_text())
                config = TaggingConfig(domains=domains)
                conn.execute("INSERT INTO settings VALUES ('tagging', ?)", (config.model_dump_json(),))
            else:
                config = json.loads(saved["value"])
                prompt = config.get("prompt")
                if isinstance(prompt, dict) and {"true_criterion", "false_criterion"}.intersection(prompt):
                    # Migrate only the removed fields; preserve the saved question and other settings.
                    prompt.pop("true_criterion", None)
                    prompt.pop("false_criterion", None)
                    migrated = TaggingConfig.model_validate(config)
                    conn.execute("UPDATE settings SET value=? WHERE key='tagging'", (migrated.model_dump_json(),))
            conn.execute(
                "UPDATE jobs SET status='interrupted', error='Server stopped. Completed papers are saved; start again to resume.', finished_at=? WHERE status IN ('queued','running')",
                (now(),),
            )

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        """Read dictionaries with SQL and bound parameters supplied by application code."""
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, args)]

    def execute(self, sql: str, args: tuple = ()) -> None:
        """Execute and commit one application-owned parameterized statement."""
        with self.connect() as conn:
            conn.execute(sql, args)

    def settings(self) -> TaggingConfig:
        """Load validated current tagging settings."""
        return TaggingConfig.model_validate_json(self.rows("SELECT value FROM settings WHERE key='tagging'")[0]["value"])

    def conference(self, conference_id: int) -> tuple[dict, ConferenceConfig]:
        """Return the conference row and typed configuration, or fail if absent."""
        rows = self.rows("SELECT * FROM conferences WHERE id=?", (conference_id,))
        if not rows:
            raise ValueError("Conference not found.")
        return rows[0], ConferenceConfig.model_validate_json(rows[0]["config"])

    def papers(self, conference_id: int) -> list[dict]:
        """Return active papers in stable insertion order for collection and tagging."""
        return self.rows("SELECT * FROM papers WHERE conference_id=? AND active=1 ORDER BY id", (conference_id,))

    def discover(self, conference_id: int, papers: list[dict]) -> None:
        """Atomically synchronize an index, preserving previously scraped content."""
        with self.connect() as conn:
            conn.execute("UPDATE papers SET active=0 WHERE conference_id=?", (conference_id,))
            for paper in papers:
                existing = conn.execute("SELECT title FROM papers WHERE conference_id=? AND source_url=?", (conference_id, paper["source_url"])).fetchone()
                if existing and existing["title"] != paper["title"]:
                    raise ValueError(f"Indexed title changed for {paper['source_url']}; existing content was preserved.")
                conn.execute(
                    "INSERT INTO papers(conference_id, source_url, title) VALUES (?, ?, ?) ON CONFLICT(conference_id, source_url) DO UPDATE SET active=1",
                    (conference_id, paper["source_url"], paper["title"]),
                )
                if paper.get("pdf_url"):
                    conn.execute("UPDATE papers SET pdf_url=?, pdf_error=NULL WHERE conference_id=? AND source_url=?",
                                 (paper["pdf_url"], conference_id, paper["source_url"]))
                elif paper.get("pdf_error"):
                    conn.execute("UPDATE papers SET pdf_error=? WHERE conference_id=? AND source_url=? AND pdf_url IS NULL",
                                 (paper["pdf_error"], conference_id, paper["source_url"]))
            conn.execute("UPDATE conferences SET discovered_at=? WHERE id=?", (now(), conference_id))
