# PaperRadar

PaperRadar is the project and app name. The Python package (`research_tagger`), launch commands (`research-tagger-web` and `research-tagger`), and database filename retain their existing names for compatibility with installed copies.

Collect conference papers, optionally extract introductions from arXiv TeX, and apply research tags with a TypeSafe model. The browser app saves papers in SQLite and shows collection coverage, tag statistics, and estimated tagging costs.

Papers can have multiple tags or no tags. The browser app supports ICML, ECCV, ICLR, CVPR, ICCV, and NeurIPS with fixed official URL patterns. The original command-line workflow remains available for ECCV title/abstract collections.

Conference discovery and abstract collection use **Scrapy 2.19**, shared by the web app and CLI. HTTPX handles tagging API calls, and arXiv metadata/source downloads; BeautifulSoup is retained only for arXiv metadata parsing.

## Open the web app

Clone the project first:

```sh
git clone https://github.com/eyad-al-shami/PaperRadar.git
cd PaperRadar
```

To start with the collected papers and saved classifications, follow the [included dataset instructions](dataset/README.md). Otherwise, the commands below start with your existing local database or create an empty one.

The app code and project documentation are available under the [MIT license](LICENSE). The bundled SQLite dataset is excluded from that software license; paper text retains its original rights. See the [dataset contents and rights notice](dataset/README.md#source-attribution-and-rights).

```sh
uv sync
uv run research-tagger-web
```

Open **http://127.0.0.1:8000**. The server runs locally while this command is active; stop it with Ctrl+C. Conference settings, papers, jobs, and tagging results survive restarts in `data/research-tagger.sqlite3`. Run one server process per database.

1. Click **Add conference** in the sidebar, then select a conference and year. ECCV accepts even years; ICCV accepts odd years. The official source URL is filled in automatically. CVPR uses `https://openaccess.thecvf.com/CVPR{year}?day=all` (for example, `CVPR2026` or `CVPR2025`). Existing CVPR settings migrate on app restart; click **Scrape papers** to load the CVF list and collect missing content. Old virtual-site records remain stored but become inactive after discovery; their abstracts and tags are not transferred to the new records.
   For **NeurIPS**, select a year (2000 onward). Discovery reads [the official archive](https://proceedings.neurips.cc/) and follows that year's main proceedings link, including all tracks in the main volume. Separate Creative AI volumes and the separate 2021 Datasets and Benchmarks proceedings are excluded. The latest listed main edition at implementation is 2025; an unpublished year fails explicitly. **Scrape papers** collects each paper's abstract and explicit **Paper** PDF URL without downloading PDFs.

2. Open **Scrape conference papers** and click **Scrape papers**. One job reads the full index, then collects missing content. Progress shows **Step 1 of 2 · Reading the conference paper index** and **Step 2 of 2 · Collecting papers**. Run it again to discover new papers and retry incomplete records; completed papers are skipped. An index failure leaves the saved papers intact.
3. Beside **Scrape papers**, open **Text used for tagging** and check the desired sections. **Title** and **Abstract** are selected by default; **Introduction** is optional. Changes save automatically, and at least one section must remain selected. **Scrape papers** collects missing abstracts and PDF URLs for indexed titles, plus introductions when selected. Existing text, links, and tagging results are preserved. Scraping processes all incomplete records. Papers with complete text but no PDF URL are still included in the run.
4. Browse saved papers using title search, text status, and the tag dropdown. Check one or more tags, search the choices, or use **Clear all** to reset. Papers match **all** selected tags. The same filters apply to export across all pages; scraping remains conference-wide.
   **Papers without PDF links** counts missing URLs in the conference. The separate collection button has been removed; **Scrape papers** now handles PDF links. ECCV's **Paper PDF** button links to a PDF, and an ICML/ICLR **OpenReview** button supplies the paper ID for `https://openreview.net/pdf?id=...`. CVPR and ICCV discovery collect PDF URLs directly from their CVF indexes; abstracts come from the linked CVF paper pages. No PDF files are downloaded, and no OpenReview API or login is used. Click **PDF ↗** in a paper row to open its saved URL.
   **Export to Zotero → Download matching papers (.RIS)** exports the matching records and uses the saved PDF link as the Zotero item URL (RIS `UR`), so **Find Full Text** can try that URL. No attachment (`L1`) field or PDF file is exported. Conference and arXiv page links are preserved in notes, alongside arXiv archive metadata when available. Without a saved PDF link, the item URL remains the arXiv page when available, otherwise the conference page. Re-export after restarting the server to use this format; existing Zotero items are not updated automatically.
5. Review complete/pending/failed counts and the compact tagging estimate for the conference's active tags. Incomplete papers are excluded.
6. Add your TypeSafe key in Settings. In **Tag conference papers**, open **Tags to apply** beside **Tag papers** and check the tags to apply. The searchable dropdown shows only tag names and scrolls after five rows. The papers, input tokens, and price recalculate before you click **Tag papers** and confirm the paid call.

The collection and tagging panels start collapsed and remember your choice in this browser. Their headers show current coverage and estimated tagging cost. **Scrape papers** always refreshes the index before collecting missing content and PDF links. It remains available when existing papers are complete so you can check for additions. Tag statistics are available under **Papers by selected tag**.

Paper rows use **Evaluated** to mean a classification request was saved, even when no tags matched. Open a paper to see all current tag probabilities, the threshold, token estimate, abstract and links. Introduction content appears when selected or already saved; arXiv editing is under **Advanced: introduction source**. Completed runs show a compact result, with **Finished with N errors** and **Retry** when applicable.

In Settings, edit tag names/definitions, threshold and prompt directly. Model, parallel requests and pricing are under **Advanced**. New tag IDs are generated automatically and existing IDs are read-only. **Conference details** displays locked editions; use **Add conference** in the sidebar for a new one. Settings stays accessible on small screens.

**Zotero Connector:** open **Export to Zotero → Connector setup and help** (also available in Settings) to download and install the custom translator. The browser extension can then save the open paper dialog or choose from all papers matching your list filters, with Zotero downloading PDFs directly from saved URLs. See [setup and usage](docs/ZOTERO.md).

The conference workspace has two independent collapsible boxes. **Scrape conference papers** contains text-readiness and missing-PDF statistics, discovery, combined collection progress, the text-section dropdown. **Tag conference papers** contains tagged and ready counts, estimated input tokens, estimated price, tag selection, live per-paper tagging progress, and results by selected tag. Opening or closing either box does not affect the other, and the Papers box remains outside both.

Use **×** to dismiss a finished collection or tagging run’s progress and messages. **Show last run** restores them, including **Retry failed**. Dismissal is remembered in this browser; it never resets saved progress, errors, or results. New runs appear automatically, and active runs keep their **Stop** control.

**Settings → Tagging and pricing → Parallel requests** controls simultaneous conference-tagging calls (1–16, default **4**). One request contains one paper and all selected tags. This changes throughput, not prompts or estimated token costs; existing results remain valid. Prompt tests and the CLI remain sequential.

Tagging continues through isolated paper/API failures and saves each error on its paper. Failures are counted in completion order; a success resets the counter. The fifth consecutive failure stops new submissions. **Stop** also prevents new submissions. In either case, in-flight calls finish and their results/errors are saved, so totals can increase before the run finishes. **Retry failed** sends only papers with unresolved tagging errors for the current selection. A normal tagging run continues with every untagged paper, while both paths skip saved current results.

Choosing conference tags does not rewrite the shared prompt. It restricts the model request to the selected tag questions, names, and definitions. Fewer selected tags usually mean fewer instruction tokens and a lower estimated price. The chosen set becomes that conference's active tags when the job starts; changing the set can make papers eligible for a new request signature while previous results remain stored.

OpenReview API integration, account settings, source-label collection, and primary-area/keyword controls have been removed. Scraping reads conference paper text and PDF links, plus arXiv introductions when selected. `OPENREVIEW_USERNAME` and `OPENREVIEW_PASSWORD` are no longer read. On startup, obsolete OpenReview metadata values and `conference_tags` jobs are removed from SQLite; titles, abstracts, introductions, and Jev results are preserved.

The key entered in the browser stays in server memory until restart. Setting `TYPESAFE_API_KEY` before startup also works. No key is saved to SQLite or returned to the browser.

Each field has a **?** help button explaining its purpose and effect. Hover or focus it with the keyboard to read the explanation; click or tap to keep it open. Click again, click elsewhere, move keyboard focus away, or press Escape to dismiss it. Help covers Settings, conference setup, collection choices, paper filters, and the arXiv ID.

In **Settings → Tagging and pricing → Tagging prompt**, edit the shared question/instructions, then click **Save tagging settings**. `{fields}` inserts the selected paper sections, such as “title and abstract” or “title and introduction”. Domain names and definitions are editable directly below. Saved prompts apply to all conferences, persist in SQLite, and are included in token estimates. Changing the resulting request makes previous tags outdated and eligible for tagging again; it does not start paid calls automatically.

Use **Test this prompt** in the same Settings panel to try the current form values before saving. Choose a conference and 1–10 papers with the number control. The app estimates the exact sample first, asks you to review the paid call, then shows every domain probability, threshold match, and actual token cost for each paper. It uses the first complete papers in stable library order so repeated tests are comparable. Test results are temporary: they do not change saved settings, paper tags, jobs, or usage totals.

All five conferences use their official HTML paper lists by default. The virtual sites include a server-rendered list, so discovery works with robots checking enabled and does not depend on their restricted JSON feeds. See **[Web app documentation](docs/WEB.md)** for all settings, data types, endpoints, TeX support, estimate assumptions, error behavior, and verification results. The sections below describe the original CLI, which retains its strict all-or-nothing JSON-file workflow.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
uv sync
uv run research-tagger --help
```

For classification, obtain access and an API key through the [TypeSafe console](https://console.typesafe.ai/). Configure it in the same terminal used to run the tool. In zsh, this reads it without displaying it or putting the value in shell history:

```sh
read -s 'TYPESAFE_API_KEY?TypeSafe API key: '
export TYPESAFE_API_KEY
```

The key is read from the environment, sent only to TypeSafe, and never saved in result files. `.env` files are ignored by version control but are **not automatically loaded**.

## Use

First edit `domains.json`. Each domain requires a unique `id`, a readable `label`, and a precise `description` defining what belongs and what does not. The supplied domains are interpretability/explainability and world models. Jev evaluates these definitions; it does not invent a taxonomy.

Scrape all indexed papers from one edition:

```sh
uv run research-tagger scrape --year 2026 --output data/eccv-2026.json
```

For a small live check, use an explicitly limited sample instead:

```sh
uv run research-tagger scrape --year 2026 --limit 3 --output data/eccv-2026-sample.json
```

Classify a saved corpus and print the groups:

```sh
uv run research-tagger classify \
  --papers data/eccv-2026.json \
  --domains domains.json \
  --threshold 0.7 \
  --output data/eccv-2026-tagged.json
```

Substitute the sample filename for a small first classification run. Classification sends one paid API request per paper. Each request includes all configured domains and uses only the title and abstract as paper content. The default model is `jev-latest`; use `--model` with an available pinned Jev version if you need reproducibility. The actual model returned by TypeSafe is stored per paper.

Read saved results again without additional API calls:

```sh
uv run research-tagger show data/eccv-2026-tagged.json
uv run research-tagger show data/eccv-2026-tagged.json --domain world_models
```

The equivalent module entry point is `uv run python -m research_tagger`.

## Classification behavior

- One independent [Noul question](https://docs.typesafe.ai/primitives/noul) per domain asks whether the paper makes a substantive contribution to that domain. All questions are sent in one [Jev request](https://docs.typesafe.ai/api).
- A probability greater than or equal to the threshold assigns that tag. The initial `0.7` threshold is a starting choice, not a threshold validated on research papers; check labeled examples before relying on it.
- Within each domain, papers are sorted by that domain's probability. A paper can appear in several groups. Every entry shows its title, abstract, source link, and all domain probabilities.
- Papers with no probability meeting the threshold are displayed separately. This means none of the configured tags met the cutoff, not that the paper has no research domain.
- Noul values estimate the probability of domain membership; they are not measures of paper quality, and this application has not yet evaluated their accuracy.

## Scraping and failure behavior

The CLI uses the same ECCV virtual-site adapter as the web app. It reads the selected edition's `papers.html` and validates every poster entry in its server-rendered `noscript` list. It does not request static JSON feeds or their paginated API. Navigation and duplicate presentation links outside the paper list are excluded. Scrapy reads `h1.event-title` and `#abstractText .abstract-text-inner` from poster pages, verifies the indexed title, and excludes lay summaries. Downloads use one concurrent request with a 0.5-second delay, observe robots.txt, and validate public HTTPS destinations including redirects. It does not download PDFs. Source URLs and conference/year are retained as provenance and are not sent to Jev. See [conference settings and parser requirements](docs/WEB.md#conference-settings) before running a scrape.

The crawler uses Scrapy's asyncio mode without a Twisted reactor so multiple jobs can run sequentially in the same local web worker. Scrapy currently marks this integration and its aiohttp download handler as experimental; dependencies are restricted to the tested 2.19 release family. See [crawler configuration and API](docs/WEB.md#scrapy-crawler) before extending or deploying it.

Missing years, HTTP errors, blank or numeric-only paper content, invalid input schemas, duplicate domain IDs/labels, and mismatched Jev answers stop the run with an error. No substitute sources, generated abstracts, default probabilities, automatic retries, or silent skipping are used. If the conference has a malformed paper page, a full scrape stops at that page and reports its URL; fixing the source or explicitly supplying a valid corpus is necessary to proceed.

Results are written after the entire command succeeds. There is no checkpoint/resume support yet: a failure can lose the current run's in-memory progress, and retrying classification can incur repeated API charges. Existing output files are never overwritten; use a new filename for each run. `--limit` explicitly creates a sample, and omitting it processes all discovered links.

## Data types and module API

All persisted records reject unknown fields and incorrectly typed values. Strings must be nonempty after whitespace normalization; probabilities must be finite numbers from 0 to 1. `source_url` is a validated HTTP(S) URL. Saved files use UTF-8 JSON.

| Type | Fields and purpose |
| --- | --- |
| `Paper` | `title: str`, `abstract: str`, `source_url: HttpUrl`; scraped content and provenance |
| `Domain` | `id: str` matching `[a-z][a-z0-9_]*`, `label: str`, `description: str`; classification rubric |
| `TaggingPrompt` | Nonempty `question: str`; shared template supporting only `{fields}`, with the original question as the default |
| `Corpus` | `conference: "ECCV"`, `year: int >= 2000`, nonempty `papers: list[Paper]`; unique source URLs |
| `TaggedPaper` | All `Paper` fields plus `probabilities: dict[str, float]` and `model: str` |
| `TaggedCorpus` | Conference/year, `domains: list[Domain]`, `threshold: float`, and nonempty `papers: list[TaggedPaper]`; exact domain/probability key matching |
| `NoulAnswer` | `type: "noul"`, `noul: float`; validated API answer |
| `JevResponse` | `model: str`, `answers: dict[str, NoulAnswer]`; other API metadata, including token usage, is ignored |

| Function | Behavior |
| --- | --- |
| `scraper.parse_paper(html, source_url) -> Paper` | Extract and validate a virtual poster page’s title/abstract text |
| `scraper.scrape_eccv(year: int, limit: int\|None=None) -> list[Paper]` | Use Scrapy to return every requested paper in index order; optional positive sample limit |
| `classifier.classify_paper(client, paper, domains, api_key, model="jev-latest") -> TaggedPaper` | Perform one authenticated Jev request and validate its answers |
| `models.validate_domains(domains) -> list[Domain]` | Reject an empty taxonomy or duplicate IDs/labels |
| `cli.save(path, record) -> None` | Serialize a validated record into a new file |
| `cli.show_results(run, domain_id=None) -> None` | Print all domain groups or one selected group |
| `cli.main(argv=None) -> int` | CLI entry point; returns 0 on success, 1 on runtime failure, argparse exits with 2 for invalid command syntax |

The scraper now owns its network lifecycle and no longer accepts an HTTPX client or returns an iterator. Its synchronous entry point must run outside an active asyncio event loop. Callers still own and close any `httpx.Client` passed to the classifier. Scraping failures and invalid content raise `ValueError`/Pydantic validation errors; classifier network/API failures raise `httpx.HTTPError` subclasses. The CLI reports failures to stderr and exits unsuccessfully.

Python usage with an already scraped corpus:

```python
import os
from pathlib import Path

import httpx
from pydantic import TypeAdapter

from research_tagger.classifier import classify_paper
from research_tagger.models import Corpus, Domain, validate_domains

corpus = Corpus.model_validate_json(Path("data/eccv-2026.json").read_text())
domains = validate_domains(TypeAdapter(list[Domain]).validate_json(Path("domains.json").read_text()))
with httpx.Client(timeout=60) as client:
    result = classify_paper(client, corpus.papers[0], domains, os.environ["TYPESAFE_API_KEY"])
print(result.probabilities)
```

## Verification

```sh
uv run python -m unittest discover -s tests -v
node tests/test_selection_ui.js
```

Tests use synthetic paper text and mocked Jev responses. Scraping tests run the actual Scrapy engine with an offline download handler. They exercise sequential crawls, fixed conference URLs, year parity, HTML paper lists, lists larger than 200 papers, blocked static feeds, robots rules, redirect validation, cancellation, per-paper failures, database resume, edition filtering, API payloads, independent tags, strict probabilities, response mismatches, authentication failure, file round trips, and the complete CLI workflow. They do not establish Jev's scientific tagging accuracy. A real classification check requires a configured TypeSafe key.

Verified: all 84 Python tests, JavaScript syntax checks, and the Node paper UI tests pass. Tests verify that OpenReview environment variables trigger no requests, its routes and label jobs are unavailable, conference scraping converts OpenReview links to PDF URLs without requesting them, saved data survives, prompt tests use reviewed unsaved drafts without persisting results, and the remaining paper filters and paid confirmation work. No live Jev inference was run. See [verification details](docs/WEB.md#verification-and-current-limits).
