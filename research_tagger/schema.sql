PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conferences (
    id INTEGER PRIMARY KEY,
    config TEXT NOT NULL,
    discovered_at TEXT,
    UNIQUE(config)
);
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY,
    conference_id INTEGER NOT NULL REFERENCES conferences(id),
    source_url TEXT NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT,
    conference_tags TEXT,
    conference_tags_error TEXT,
    conference_tags_source TEXT,
    openreview_url TEXT,
    openreview_primary_area TEXT,
    openreview_keywords TEXT,
    introduction TEXT,
    arxiv_id TEXT,
    arxiv_error TEXT,
    pdf_url TEXT,
    pdf_error TEXT,
    abstract_error TEXT,
    introduction_error TEXT,
    tag_error TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    scraped_at TEXT,
    UNIQUE(conference_id, source_url)
);
CREATE TABLE IF NOT EXISTS tag_results (
    id INTEGER PRIMARY KEY,
    paper_id INTEGER NOT NULL REFERENCES papers(id),
    signature TEXT NOT NULL,
    probabilities TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(paper_id, signature)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    conference_id INTEGER NOT NULL REFERENCES conferences(id),
    kind TEXT NOT NULL,
    conference_tag TEXT,
    status TEXT NOT NULL,
    stage TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active ON jobs((1))
    WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS idx_jobs_conference ON jobs(conference_id, created_at);
PRAGMA optimize;
