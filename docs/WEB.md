# PaperRadar web app

## Run and storage

```sh
uv sync
uv run research-tagger-web
# Optional alternate database and port:
uv run research-tagger-web --database data/my-library.sqlite3 --port 8001
```

The default URL is `http://127.0.0.1:8000`. This application binds only to the local machine; it is not a hosted service or a multi-user application. Keep the server running while using it. The original CLI is independent and continues to write JSON files.

SQLite stores data in `data/research-tagger.sqlite3`, using WAL mode and foreign keys. For backups, stop the app and copy the database. While it is running, the `-wal` and `-shm` companion files may contain active data; do not copy only the main file as a live backup. Run one app process per database. There is one job coordinator, with a bounded request pool for conference tagging and progress committed after every paper. Browser reloads do not interrupt it.

On first launch with the default database path, `create_app(database_path: Path | None = None, domains_path: Path | None = None)` copies `dataset/research-tagger.sqlite3` into the missing working database before initialization. The bundled snapshot stays unchanged, and existing local databases are never replaced or merged. If the snapshot is missing, first startup fails explicitly. Passing `--database` opts out of seeding; a missing explicit database starts empty. The dataset README documents the snapshot's contents, provenance and rights.

## Field help

Every editable field has a **?** beside its label. Hover or keyboard-focus the button to read what the field does; click or tap to keep the explanation open. Click it again, click outside, move keyboard focus away, or press Escape to close it. Only one explanation is open at a time. Escape dismisses help before closing an enclosing dialog. Help buttons do not submit forms or change settings.

This covers connection/model/pricing/prompt settings, each domain's name/ID/definition, conference/year selection and the generated URL, selected paper sections, search/status/domain filters, and the arXiv ID. Newly added domains and paper-detail forms receive the same help. Descriptions are linked to their controls for screen readers, and popup placement accounts for the viewport and enclosing dialog.

## Conference settings

Use **Settings → Add conference**, choose one conference and one year, then save. The paper URL is generated automatically and is read-only. Arbitrary names, source URLs, and custom selectors are no longer supported.

| Conference | Official URL pattern | Years |
| --- | --- | --- |
| ICML | `https://icml.cc/virtual/{year}/papers.html` | Every year |
| ECCV | `https://eccv.ecva.net/virtual/{year}/papers.html` | Even years |
| ICLR | `https://iclr.cc/virtual/{year}/papers.html` | Every year |
| CVPR | `https://openaccess.thecvf.com/CVPR{year}?day=all` | Every year |
| ICCV | `https://openaccess.thecvf.com/ICCV{year}?day=all` | Odd years |
| NeurIPS | `https://proceedings.neurips.cc/` (resolve selected year from archive) | Every year, 2000 onward |

Years range from 2000 through the current UTC year, subject to parity. The API enforces the same rules as the dropdown and rejects future years. Offered years identify possible editions; they are not a claim that a compatible, complete index is available. Saving does not make network requests. The discovery stage of **Scrape papers** verifies availability and requires a complete index before saving titles and URLs. Missing editions, redirects to another edition, unsupported layouts, robots exclusions, and HTTP errors fail visibly. There is no alternate-source fallback.

Saved CVPR configurations using the former virtual-site URL migrate to the CVF URL and `source: "cvf"` at startup, preserving the year, selected fields, tags and paper records. Run **Scrape papers** to synchronize the new source and collect its abstracts. Discovery marks old virtual-page records inactive and adds CVF records by URL; old abstracts and tagging results remain stored but are not transferred automatically. Newly supplied configurations must match the CVF source and selected year.

**Default for all six conferences: read the official HTML index with Scrapy.** For ICML/ECCV/ICLR, the supplied `papers.html` contains a server-rendered paper list at `main noscript.noscript > ul`. Discovery requires exactly one nonempty list and validates every entry's title, official host, HTTPS URL, and selected year. Identical repeated links are deduplicated; conflicting titles or malformed entries fail the whole discovery. Navigation links and oral/spotlight links outside the paper list are excluded. Abstracts come from `#abstractText .abstract-text-inner` on poster pages after verifying `h1.event-title`, so lay summaries are excluded. CVPR and ICCV use their all-papers HTML indexes and `#papertitle` / `#abstract` detail pages.

NeurIPS uses `source: "neurips"` and the archive root as `index_url`. Discovery resolves exactly one main proceedings link for the selected `year`; it does not infer the year from the first entry or guess volume URLs. It includes all tracks within that volume and excludes separate Creative AI volumes and the separate 2021 Datasets and Benchmarks archive. Missing or ambiguous editions fail explicitly. The latest main edition listed during verification was 2025. The selector retains the app's 2000-to-current-year bounds, which do not guarantee publication.

The NeurIPS adapter reads `ul.paper-list` and validates each title link's HTTPS host, year and abstract-page format. Paper pages must match the indexed `h1.paper-title`; the Abstract section includes nested paragraphs, and the explicit **Paper** button must point to a PDF with the same year and paper hash/suffix. No PDF is downloaded. Missing or mismatched data produces the existing per-paper errors; successful fields remain saved. Use **Settings → Add conference → NeurIPS → year**, then **Scrape papers**. Existing tagging, filtering and export workflows apply unchanged.

The HTML list is the primary source, not a fallback. The crawler does not request the virtual sites' `/static` JSON feeds or paginated API. Those feeds have robots exclusions (and CVPR's pagination requires authentication), but the public HTML paper lists and poster pages are accessible with robots checking enabled. No browser rendering, login, or robots override is needed. Missing or changed list markup fails explicitly instead of switching sources. The indexed total is the number of unique papers in the site's HTML list; the crawler cannot independently guarantee that a publisher has listed every accepted paper.

Rediscovery preserves saved content. Papers no longer in the index remain stored but are excluded from current coverage. Conflicting title changes for an existing URL fail atomically. Once papers exist, add a separate conference to collect a different name/year; selected content fields remain editable. Empty editions can be changed, subject to duplicate-edition validation.

At the user's request, the previous conference collection, paper content, job history, and old JSON sample were deleted during this update. Tagging settings and domain definitions were retained. The obsolete ECVA/custom adapters and source selectors were removed; the CLI now uses the same ECCV virtual source. This was a one-time cleanup, not a startup deletion routine. Old-format conference configurations are no longer accepted.

## Scrapy crawler

Both the web worker and CLI use `ConferenceSpider` in `research_tagger/crawling.py`. Scrapy manages the request scheduler, downloads, redirects, robots.txt, and HTML selectors. The adapters in `conferences.py` parse Scrapy HTML responses using fixed provider-specific rules. A robots exclusion appears as a crawl error. Abstract pages require static HTML and exact normalized title matching.

`SCRAPY_SETTINGS` uses one concurrent download, a fixed 0.5-second download delay, a 45-second timeout, a 16 MB response limit, and at most five redirects. Automatic retries, cookies, and the telnet console are disabled. `PublicSourceMiddleware` validates each public HTTPS destination and checks cancellation before downloading. These are code settings, not additional browser settings. A failed abstract produces an error for that paper; successful abstracts are saved immediately. Missing introductions continue through the existing arXiv path, with its separate rate limit. Source access failures never trigger a switch to another provider.

Each synchronous crawl creates its own asyncio loop with `AsyncCrawlerRunner` and `TWISTED_REACTOR_ENABLED=False`. This lets the existing background thread run successive jobs without restarting Twisted's global reactor. Call these functions from a synchronous worker, not FastAPI's active event loop. **Scrapy currently labels this mode and its aiohttp download handler experimental.** The dependency is limited to `>=2.19,<2.20` and `uv.lock` records the tested versions. See [Scrapy's asyncio documentation](https://docs.scrapy.org/en/latest/topics/asyncio.html) and [running Scrapy from a script](https://docs.scrapy.org/en/latest/topics/practices.html#run-scrapy-from-a-script).

`CrawlResult` is a typed dictionary with `kind: "index"|"abstract"`. Index results contain `papers: list[dict]`; abstract results contain `paper: dict` and `abstract: str`. A failed request/content parse supplies `error: str`. Indexed records contain `title: str` and `source_url: str`. Callbacks persist each abstract immediately. Callback and unexpected spider errors abort the crawl; cancellation leaves remaining papers unprocessed.

## Paper list and filters

The Papers box supports title search, collection status, and active tag filters. Open **Filter by tags** and check one or more names to match **all** selected tags at the saved threshold. The dropdown stays open while selecting; its search narrows the choices without removing selections. Long lists scroll. The button shows the selected name or a count, and **Clear all** restores all tags. Tab moves through controls, Space toggles a focused checkbox, and Escape closes the dropdown and returns focus to its button. Clicking or moving focus outside also closes it. Search, status, and tags also filter RIS export across all pages. **Scrape papers** processes all eligible papers in the combined abstract/PDF/introduction collection run; paper-list filters do not narrow that run. Conference tagging processes every eligible paper for the tags selected in its paid review. That review retains the exact conference, tag IDs, and estimate signature.

The interface uses “tags” throughout. Existing `domain`/`domains` API fields, saved IDs, and model identifiers remain unchanged for compatibility; no saved prompts or classification results are rewritten. The paper-filter checkbox dropdown uses the existing vanilla JavaScript frontend. `state.filterDomainIds: string[]` stores the selection and supplies repeated `domain` query parameters to paper listing and export. `renderDomainFilter(): void` updates labels and checkbox state without replacing focused rows during polling; it rebuilds choices only when domain IDs or labels change. `filterDomainOptions(): void` applies the local choice search. `closeDomainFilter(restoreFocus: boolean = false): void` closes the panel, updates its accessibility state, and optionally focuses the trigger. Switching conferences clears the filter.

OpenReview API requests, authentication, verification codes, source-label collection, and primary-area/keyword controls have been removed. The app ignores OpenReview credential environment variables and does not follow OpenReview links when scraping conference papers. Missing or blocked OpenReview pages cannot interrupt abstract collection. Existing historical metadata remains in SQLite without being collected, displayed, or used as filters. No replacement metadata source is selected automatically.

Old authentication routes return 404; `kind="conference_tags"` and source-label fields in job bodies return 422. Removed `primary_area`, `keyword`, and `conference_tag` list query parameters return an explicit 400 rather than silently widening a request.

## Selected content and statistics

Title, abstract, and introduction are independently selectable; at least one must be selected. Titles are always retained from discovery to identify papers in the interface, but are only sent to Jev when selected. Scraping always collects missing abstracts and PDF URLs, even if only titles are selected for Jev. Introduction extraction remains optional. Already saved fields are preserved. PDF URLs are never included in the Jev payload and do not affect tagging eligibility.

| Statistic | Meaning |
| --- | --- |
| Indexed | Unique active paper URLs from the latest completed discovery; unknown before discovery |
| Text ready (`scraped`) | All currently selected Jev text fields are present |
| Text incomplete (`not_scraped`, API only) | Indexed minus text-ready papers; not a separate dashboard card |
| Pending | At least one selected field is missing, without a recorded error for a missing selected field |
| Needs attention | At least one missing selected field has an error; a subset of text-incomplete papers |
| Introductions | Papers with an extracted introduction; the card is shown only when introduction is selected |
| Papers without PDF links (`missing_pdf`) | Active conference papers without a saved PDF URL, regardless of text or tag status |
| `collection_pending` (API) | Papers missing title, abstract, PDF URL, or a selected introduction; determines which saved papers need content; scraping stays enabled to refresh the index |
| Tagged | Papers whose stored probabilities match the exact current content, model, and domain instructions |
| Ready to tag | Complete papers without matching tags, excluding requests over the conservative estimated context cutoff |

Changing fields recalculates completeness; it does not discard saved text. Changing content, domains, or model changes request identities, so old probabilities are not presented as current. Changing only the threshold reuses probabilities and recomputes tags. A paper can count in multiple domains. A paper below all thresholds has no matching tag, not a fabricated “other” classification.

Use paper search, status filters, and domain filters to inspect the collection. Opening a paper shows saved content, provenance, and specific errors. Setting a new arXiv ID clears its saved introduction and introduction error so the next scrape verifies and fetches the new source.

## arXiv introduction extraction

The app uses two explicit workflows:

1. **No arXiv ID supplied:** search arXiv's Atom metadata API by title and require exactly one normalized title match. Title normalization removes typography differences; it does not perform fuzzy matching.
2. **An arXiv ID supplied in the paper details:** read that arXiv abstract page directly, require a matching `citation_title`, and obtain and validate its versioned ID from `og:url`. An explicitly pinned version must match exactly.

After verification, download `https://arxiv.org/src/<versioned-id>`, unpack it in memory, expand local `\input{...}`/`\include{...}` references, and extract the `Introduction` section through the next section/chapter. Subsections remain part of the introduction. “Introduction and …” and starred/optional-short-title section forms are supported. Comments are removed and `pylatexenc` converts the section to readable text. The versioned ID and resulting text are stored; the archive is not retained. This is arXiv source content and may differ from the final conference proceedings version.

Supported source formats are plain TeX, gzipped TeX, tar, and gzipped tar. The extractor requires one main document and one identifiable Introduction section. PDF-only submissions, missing sources, ambiguous matches, missing/cyclic includes, custom macro-defined section structures, non-UTF-8 sources, and unsupported layouts fail explicitly. It never substitutes an abstract, PDF text, generated summary, or another paper.

Source limits are 32 MB downloaded, 64 MB uncompressed, 8 MB of TeX, 500 TeX files, and 20 nested include levels. Archive traversal, symlinks, and duplicate filenames are rejected; TeX is never executed. arXiv requests are serialized with at least three seconds between starts. References: [arXiv API](https://info.arxiv.org/help/api/user-manual.html), [source archive formats](https://info.arxiv.org/help/unpack.html), [pylatexenc](https://pylatexenc.readthedocs.io/en/latest/latex2text/).

## Editable tagging prompt

Open **Settings → Tagging and pricing → Tagging prompt**. Edit the question/instructions in the text area. Tag names and definitions appear under **Research tags** below it. Click **Save tagging settings** to persist the complete configuration. A save never starts inference.

The shared prompt applies to every conference and every domain selected for that conference's tagging run. Each selected domain receives its own independent `noul` question with its name and definition supplied separately. The question is sent as `instructions.question`; selected paper text is sent in `state`. Neither the web app nor CLI sends the optional `criteria` object. Jev returns a probability, and the configured threshold determines whether the paper receives the tag.

The question supports the optional `{fields}` placeholder. It expands to the selected sections in title/abstract/introduction order, joined with “ and ”. For example, `Using only {fields}, does this paper contribute to the specified domain?` becomes `Using only title and introduction, does this paper contribute to the specified domain?` when those two sections are selected. Plain text without a placeholder is also valid. Use `{{` and `}}` for literal braces. Empty text, unknown placeholders, unmatched braces, conversions, and nonempty format specifications are rejected before saving; invalid edits leave saved settings unchanged.

`TaggingConfig.prompt: TaggingPrompt` stores only `question` as a nonempty string in the existing SQLite settings JSON. Startup removes legacy `true_criterion` and `false_criterion` entries while preserving the saved question and other settings. Existing databases without a prompt use the original default question. New API requests reject the removed fields. The CLI remains independent of browser settings and uses the default question.

Removing criteria changes the request signature, so previous tags produced with criteria become outdated and token estimates exclude that text. Saved paper content and historical usage remain intact.

Token estimates use the rendered saved prompt for every active tag on every eligible paper. The **Tag conference papers** box places **Tag papers** beside a searchable **Select tags** checkbox dropdown and recalculates the exact estimate when the selection changes. Five 40px rows fit before vertical scrolling begins; descriptions remain editable in Settings and are still included in Jev instructions. The shared prompt text stays unchanged; only selected domain question blocks are included, so fewer tags generally reduce instruction tokens. The chosen IDs are saved in that conference's configuration when the job starts. Changed selections or requests invalidate the current match while preserving historical results, usage, and paper text. Settings remain locked during an active job, so the running job cannot mix prompt versions.

### Prompt testing

The **Test this prompt** box uses the current Settings form, including unsaved model, prompt, domain, threshold, and price edits. Choose a saved conference and 1–10 papers with the number input. The sample takes the first complete papers in stable database order and uses that conference's saved content-field selection. If fewer complete papers are available, the review shows the smaller count; if none are complete, the estimate fails without calling Jev.

Selecting **Test prompt** calculates tokens and cost without an API key or model call, then opens a paid-call review. The review signature binds the sampled paper content, complete Jev payloads, conference, and price. Editing inputs or changing saved paper content invalidates it. After confirmation, the response shows every domain probability, matches at the draft threshold, actual model, actual tokens, and cost.

Prompt-test results stay in the browser and are not written to settings, `tag_results`, jobs, or dashboard usage totals. Tests run synchronously and cannot be cancelled after confirmation. A TypeSafe key is required for the model calls. A request failure stops the test and is shown without fabricated or partial classifications.

## Token and cost estimation

The initial model is pinned to `jev-1.13.0`. Its published input price is **USD 0.042 per million tokens**, and outputs are free, verified September 22, 2026 in [TypeSafe's model documentation](https://docs.typesafe.ai/models). Settings let you update the model, price, and checked date; pricing does not update automatically.

No public Jev tokenizer has been integrated. The interface explicitly estimates tokens from the serialized request at **4 characters/token**, with a **3–5 characters/token sensitivity range**. This range is not a guaranteed upper/lower bound. It includes selected paper fields and the domain questions/definitions/JSON wrapper repeated for each paper, with the paper state included once per request.

```text
estimated input tokens = sum(ceil(serialized request characters / 4))
estimated USD = estimated input tokens × USD per million input tokens / 1,000,000
```

The Conference overview keeps pricing compact: papers ready, estimated input tokens, and estimated price for the conference's active tags. The selection dialog recalculates those values for its checked tags. Both estimates cover **all complete papers without a matching current request**, excluding incomplete, already matching, and context-blocked papers. They do not extrapolate unknown text for uncollected papers.

The estimate signature binds the selected candidate requests and price. The server rejects a paid job when the displayed signature is stale. Context checks use the 3-character estimate to exclude payloads that may exceed 64k tokens overall or 32k for state plus the longest question; these checks remain approximate. Jev may still reject a request if its actual tokenization exceeds its limit.

After each successful tagging call, save the provider's **actual input/output token counts**, actual model, probabilities, and input cost at the configured rate. Failed requests or a process interruption after a provider response but before saving may still incur charges not represented in stored totals. No API key is needed for scraping or estimates. Jev inference needs a user-supplied key and is never simulated in the application.

## Jobs and errors

**Settings → Tagging and pricing → Parallel requests** accepts integers from 1 through 16, defaulting to 4. `TaggingConfig.parallel_requests: int` is persisted in the existing settings JSON; older settings receive the default when loaded, with no database migration. It affects conference tagging only; prompt tests and CLI tagging stay sequential. It is excluded from model payloads and estimate/result signatures, so changing concurrency does not invalidate tags or change estimated tokens/pricing.

`JobService.tag_papers(job_id: str, config: ConferenceConfig, tagging: TaggingConfig, papers: list[dict], client: httpx.Client) -> None` runs at most `parallel_requests` requests at once. Each request carries one paper and every selected tag. The shared HTTP client remains open until the pool drains. Worker threads only request and validate responses; a FIFO completion queue returns them to the job coordinator, which commits each result/error and progress counter together in a SQLite transaction. Completed requests are handled before replenishing the pool. HTTP/validation failures save per-paper errors; five consecutive completed failures stop replenishment and raise after in-flight outcomes are saved. Cancellation also drains the pool. Settings remain locked during the run.

Only one job runs at a time. Settings cannot change during a job. Each discovered index synchronizes atomically; each scraped field and tagged paper is saved independently. A missing abstract, PDF URL, or selected introduction records a field-specific failure while other papers continue. Each paper counts once in job progress. Running **Scrape papers** again refreshes the index, then retries only incomplete records, including papers already tagged but missing a PDF URL. Saved text, links, and Jev results are not overwritten during these retries. A link failure does not discard a collected abstract or block text-ready papers from tagging.

Discovery progress reports that the index is being read, the number of indexed papers after success, or that discovery did not complete. It does not show zero paper failures as if discovery had successfully processed an empty conference. Scrape/tag jobs retain per-paper success/failure counts; source errors remain visible below the job status.

**Stop** prevents new tagging submissions and lets in-flight requests finish and save their outcomes. Scraping stops after its current network operation. On restart, queued/running jobs become interrupted, and completed work remains saved. Restart a scrape/tag job to resume work not already saved with matching inputs. Tagging stops launching new requests after five consecutive failures in completion order, then drains in-flight requests. A success resets the error streak but never reverses an already-triggered stop. There are no automatic request retries. **Retry failed** selects only saved per-paper errors, excluding successful and untouched papers.

An API key entered in Settings is held only in server memory; it is not returned to the browser or written to SQLite. `TYPESAFE_API_KEY` is read once at startup. The server binds to loopback, validates Host, rejects cross-origin mutations, requires JSON for writes, and checks public HTTPS destinations before following source redirects. This is a local application, not an authenticated server for exposure to the Internet.

## Data contracts

API inputs reject unknown fields, empty required text, and inappropriate type coercion. Pydantic documents the exact schema at **http://127.0.0.1:8000/docs** while the app is running.

| Type | Fields |
| --- | --- |
| `ConferenceSelection` | Public input: `name: "ICML"\|"ECCV"\|"ICLR"\|"CVPR"\|"ICCV"\|"NeurIPS"`, integer `year` from 2000 through the current year with ECCV/ICCV parity, nonempty unique `fields: list["title"\|"abstract"\|"introduction"]` (default title + abstract) |
| `ConferenceConfig` | Stored `ConferenceSelection` plus derived `source: "virtual"\|"cvf"\|"neurips"` / URL and optional `domain_ids: list[str]`; missing IDs mean all configured domains, while a saved selection identifies the conference's active tags |
| `TaggingConfig` | Nonempty unique `domains: list[Domain]`, `prompt: TaggingPrompt`, `model: str`, `threshold: float [0,1]`, finite nonnegative `price_per_million: float`, `pricing_date: YYYY-MM-DD` |
| `TaggingPrompt` | Nonempty `question: str`; `validate_template()` restricts interpolation to `{fields}` and permits escaped literal braces |
| `JobRequest` | `kind: "discover"\|"scrape"\|"tag"`, optional positive scrape `limit`; paid tagging requires unique `domain_ids` and its matching `estimate_signature` |
| `ArxivUpdate` | `arxiv_id: str`, validated as a modern or legacy arXiv ID with optional version |
| `KeyUpdate` | `api_key: SecretStr`, nonempty; never persisted |
| `Usage` | Strict nonnegative integer `input_tokens`, `output_tokens` |
| `BilledResponse` | Jev response `model`, one typed Noul answer per domain, required actual `usage` |

SQLite tables:

- `conferences`: integer ID, JSON configuration, discovery timestamp.
- `papers`: conference ID, source URL, title, nullable abstract/introduction/arXiv ID, nullable `pdf_url`/`pdf_error` text, field/tag errors, active flag and scrape timestamp. Existing databases gain the PDF columns automatically. Nullable legacy OpenReview columns remain for database compatibility, but startup clears their values and application code does not read them.
- `tag_results`: paper ID, exact request signature, domain probabilities, model, input/output tokens, recorded USD cost, timestamp; unique paper/signature pair.
- `jobs`: ID, conference ID, kind/status, total/completed/failed counts, cancellation flag, error, start/end timestamps. Startup deletes obsolete `conference_tags` jobs. Historical scope columns, when present, remain unused. A partial unique index enforces a single active job.
- `settings`: validated tagging configuration as JSON. No secrets.

Startup preserves existing paper fields, metadata, results, and settings. Legacy nullable columns remain for database compatibility; no source metadata is inferred or refreshed.

## HTTP API

All writes take JSON. Runtime validation errors return HTTP 400, schema errors 422, conflicts 409, and cross-origin mutations 403. Paper details return 404 for an unknown ID. Errors appear in the UI.

| Method and endpoint | Request / result |
| --- | --- |
| `GET /api/settings` | Tagging settings, `api_key_configured: bool`, and `conference_options: [{name, url_template, years}]`; no credentials are returned |
| `PUT /api/settings` | Save `TaggingConfig` while idle |
| `PUT /api/key` | Set `KeyUpdate` for this process only |
| `POST /api/prompt-test/estimate` | Validate a `PromptTestRequest` containing the unsaved `TaggingConfig`; return exact sample counts, estimated tokens/cost, and a review signature without calling Jev |
| `POST /api/prompt-test` | Run the reviewed `PromptTestRequest`; return transient per-paper probabilities and actual usage without saving tags or settings |
| `POST /api/conferences/{id}/tag-estimate` | Validate selected `domain_ids` and return conference-wide ready-paper, token, price, and signature statistics without saving the selection or calling Jev |
| `GET /api/conferences` | Configurations, statistics, and latest 20 jobs |
| `POST /api/conferences` | Add `ConferenceSelection`; server derives URL/source; returns integer ID (201) |
| `PUT /api/conferences/{id}` | Update `ConferenceSelection`; name/year changes disallowed once papers exist |
| `GET /api/conferences/{id}/papers` | Parameters `search`, `status`, repeated `domain` (e.g. `domain=world_models&domain=interpretability_explainability`), `page`; 30 records per page with saved content availability and probabilities. Stats cover the conference; `count` covers display filters. Removed source-label parameters return 400. |
| `GET /api/papers/{id}` | Full saved paper text, provenance, and errors |
| `PUT /api/papers/{id}/arxiv` | Save explicit `ArxivUpdate`, clearing the previous introduction |
| `POST /api/conferences/{id}/jobs` | Start `JobRequest`; returns job ID (202) |
| `POST /api/jobs/{id}/cancel` | Request cancellation with an empty JSON object |

## Python modules and extension points

All public functions have inline docstrings. The original `Paper`, `Domain`, `Corpus`, and CLI contracts are documented in the project README.

| Module / API | Responsibility and types |
| --- | --- |
| `web.create_app(database_path: Path\|None, domains_path: Path\|None) -> FastAPI` | Construct the local API/static server and lifecycle; no startup crawl |
| `web.main() -> None` | Parse port/database CLI options; start loopback Uvicorn |
| `storage.Database` | Short-lived transactional connections; `initialize`, `rows`, `execute`, `settings`, `conference`, `papers`, and atomic `discover` |
| `jobs.JobService` | `summary` uses a conference's active domains; `summary_for_domains` and `tag_estimate` calculate an explicit selection; `start` atomically saves reviewed tag IDs with the job; `run` processes it; prompt-test methods remain transient; cancellation and lifecycle methods manage the worker |
| `crawling.crawl_conference(config: ConferenceConfig, papers: list[dict]\|None, on_result: Callable[[CrawlResult],None], cancelled: Callable[[],bool]) -> None` | Run a crawl to completion. `papers=None` discovers the official index; a list downloads abstracts. Cancellation defaults to false. No source-label or OpenReview requests. |
| `crawling.discover_conference(config: ConferenceConfig, cancelled: Callable[[],bool]) -> list[dict]` | Return a complete matching index, or an empty list on explicit cancellation; cancellation defaults to false |
| `crawling.ConferenceSpider` | `start` schedules requests; `index`, `abstract`, and `failed` emit validated results or visible failures |
| `crawling.PublicSourceMiddleware` | `from_crawler` constructs middleware; async `process_request(Request)` checks cancellation and validates destinations |
| `conferences.parse_index(response: HtmlResponse, config: ConferenceConfig) -> list[dict]` | Parse the CVPR/ICCV HTML index as `{title: str, source_url: str}` records with optional `pdf_url: str` or `pdf_error: str`, matching the conference name and year without networking |
| `web_models.conference_options() -> list[dict]` | Return the five URL templates and descending valid years for the UI |
| `ConferenceSelection.to_config() -> ConferenceConfig` | Derive and validate the selected edition's official adapter and URL |
| `conferences.proceedings_url(response: HtmlResponse, config: ConferenceConfig) -> str` | Resolve exactly one selected-year NeurIPS main volume from the archive; fail on missing or ambiguous editions |
| `conferences.parse_neurips_index(response: HtmlResponse, config: ConferenceConfig) -> list[dict]` | Return unique `{title: str, source_url: str}` NeurIPS records after validating the entire list |
| `conferences.neurips_abstract(response: HtmlResponse) -> str` | Require one nonempty Abstract section, handling nested paragraph markup |
| `conferences.neurips_pdf(response: HtmlResponse, config: ConferenceConfig) -> str` | Require one explicit Paper button with a matching HTTPS PDF URL; no download |
| `conferences.parse_virtual_index(response: HtmlResponse, config: ConferenceConfig) -> list[dict]` | Read the one nonempty HTML noscript list; return unique `{title: str, source_url: str}` records, validating every entry's title, host and edition without networking |
| `conferences.parse_abstract(response: HtmlResponse, config: ConferenceConfig, paper: dict) -> str` | Verify the indexed title before extracting abstract text |
| `conferences.validate_paper_title(response: HtmlResponse, config: ConferenceConfig, paper: dict) -> None` | Require an exact normalized title match before collecting any paper content or labels |
| `conferences.text_content(element: Selector) -> str` | Normalize text excluding scripts/styles |
| `conferences.required_text(response: HtmlResponse, selector: str, field: str) -> str` | Require exactly one element with valid text |
| `arxiv.find_arxiv(client, title, identifier=None) -> str` | Resolve a unique verified arXiv ID, pinned to a version |
| `arxiv.fetch_introduction(client, title, identifier=None) -> tuple[str,str]` | Return versioned ID and extracted introduction |
| `arxiv.source_files(bytes) -> dict[str,str]` | Bounded in-memory TeX archive reader |
| `arxiv.extract_introduction(bytes) -> str` | Expand includes and convert only the Introduction to text |
| `arxiv.normalized_title(str) -> str` | Typography normalization for strict matching |
| `arxiv.without_comments(str) -> str` | Remove unescaped TeX comments |
| `arxiv.arxiv_download(client, url) -> bytes` | Serialized, rate-spaced public download |
| `network.public_url(str) -> str` | Validate public HTTPS destination and resolved addresses |
| `network.download(client, url, limit=16000000) -> bytes` | Bounded streaming download with redirect validation |
| `classifier.domain_questions(domains: list[Domain], fields: list[str], prompt: TaggingPrompt\|None=None) -> dict` | Expand shared templates for selected fields and pair them with each domain's name/definition; omitted prompt uses original CLI defaults |
| `estimates.payload_for(paper, config, tagging) -> dict` | Exact selected-field Jev payload; missing content fails |
| `estimates.estimate_payload(payload) -> dict` | Paper/instruction/total tokens and sensitivity range |
| `estimates.summarize(papers, results, config, tagging) -> tuple[dict,list[dict]]` | Content coverage, current Jev tags, costs, and estimate signature for the supplied conference papers |
| `estimates.canonical(object) -> str`, `signature(object) -> str` | Stable JSON and SHA-256 request identity |
| `estimates.ready(paper, config) -> bool` | Check all selected content is present |

To support a changed provider layout, update its explicit parser in `conferences.py` and relevant Scrapy callbacks in `crawling.py`, with fixtures verifying complete discovery and strict abstract matching. The supported conference catalog is deliberately limited to the five entries above.

The frontend is plain HTML/CSS/JavaScript, served by FastAPI without a Node runtime or build step. It uses the JSON API as the source of truth, native dialogs for details/forms, and a four-second refresh while the library is visible. Where supported, two feature-detected WebMCP tools expose the same API: `get_conference_statistics` (read-only) and `add_conference` (adds an edition without scraping or billing).

The workspace uses two independent native details elements: `<details id="scraping-overview" open>` and `<details id="tagging-overview" open>`. **Scrape conference papers** owns collection statistics, discovery, progress, and field selection. **Tag conference papers** owns tagged/ready statistics, the compact estimate, domain selection action, and results by active tag. Each summary is keyboard accessible and retains its own open/closed state during periodic data refreshes. The Papers panel remains outside both; a full browser reload opens both again.

The conference dialog uses the API catalog. `openConference(id: number|null = null) -> void` populates the selectors and locks a discovered edition; `updateConferenceYears(year?: number) -> void` lists permitted years, retaining the selected year when valid; `updateConferenceUrl() -> void` updates the read-only URL and edition note. Both form saves and WebMCP's `add_conference` send only the selected name/year (and content fields for form saves). The server derives the source independently.

**Scrape papers** and **Text used for tagging** share a wrapping row, in that order. There is no separate discovery control. The text dropdown has Title, Abstract, and Introduction checkboxes. It shares the **Filter by tags** styling: one selection shows its name, while multiple selections show **Sections** and a selected-count badge. New conferences default to Title and Abstract; saved choices are retained. Changes save automatically through `PUT /api/conferences/{id}` and update coverage, paper readiness, and tagging estimates. At least one section is required. Introduction still requires arXiv TeX collection.

`renderFieldSelection() -> void` displays saved fields or the pending edit for its conference. `closeFieldSelection(restoreFocus: boolean = false) -> void` hides the dropdown and optionally focuses its trigger; Escape or moving outside also closes it. `saveFieldSelection() -> Promise<void>` saves a nonempty selection, reports save errors, and restores saved choices on failure. `state.fieldSave: {conferenceId: number, fields: string[]}|null` tracks the pending write; checkboxes and run buttons are disabled until it finishes. Checkboxes are also disabled while the conference has an active job. `state.conferenceRevision: number` prevents an older polling response from overwriting an autosave, and pending tag estimates are invalidated before recalculation.

`renderTagPicker() -> void` updates the tagging dropdown label/count from `state.tagSelectionIds: string[]` without replacing focused rows. `filterTagOptions() -> void` searches those choices without dropping checked items. `closeTagSelection(restoreFocus: boolean = false) -> void` hides the dropdown and optionally focuses its trigger. Escape or moving outside closes the dropdown; **Clear all** disables tagging until a tag is selected. Tagging choices stay independent of **Filter by tags**, which requires all selected tags. The dropdown reuses the paper filter’s styles and sits beside the action and the tagged/ready/token/price statistics in a wrapping flex row with 20px gaps.

`ensureTagSelection() -> void` initializes the tagging dropdown checkboxes for the selected conference. `renderRunControls() -> void` renders the checked-tag estimate, and `updateTagSelectionEstimate() -> Promise<void>` rejects stale responses while calculating the selected questions. `openTagDialog() -> Promise<void>` shows a final confirmation for the reviewed selection. `loadPapers() -> Promise<void>` applies only title/status/domain/page query parameters and ignores obsolete responses. `jobRequest(kind: string) -> object` returns `{kind, limit: null, estimate_signature: null}` so free collection actions process all eligible conference papers. `startJob(kind: string, reviewed: object|null=null) -> Promise<void>` requires the stored `{conferenceId, request}` for paid calls; later control changes do not alter that reviewed request.

`setProgressDismissed(jobId: string, dismissed: boolean) -> void` dismisses or restores a finished job, saves its ID under a conference/job-group key in `state.dismissedProgress: Record<string, string>` and browser local storage (`research-tagger-dismissed-progress`), rerenders, and moves focus to the replacement control. Active jobs cannot be dismissed. Each conference has independent collection and tagging dismissal entries, retained across refreshes in the same browser origin.

`renderJobProgress(element: Element, job: object|null, tagging: boolean) -> void` keeps collection history in the scraping box and renders tagging status in the tagging box. Tagging, combined scraping, and legacy PDF-link jobs display a progress bar and percentage using `(completed + failed) / total`, alongside separate success/failure counts. The existing Stop control appears while a job is queued or running. Finished, failed, interrupted, and cancelled runs have an **×** button that hides the whole progress message. A compact **Show last collection/tagging run** control restores it and any retry action. New runs are always shown. Dismissing is presentation-only and never resets counters, errors, job history, or saved papers. A cancelled or failed job retains its partial progress; completion with per-paper failures reaches 100% processed. Expand **Scrape conference papers** to see collection progress, which updates during normal browser polling.

`GET /api/conferences/{conference_id}/papers.ris` applies the same title/status/domain filters as the paper table without pagination. It returns a Zotero-compatible RIS attachment containing saved title, abstract, conference/year, source URL, optional arXiv ID, and current above-threshold Jev domain labels as keywords. `filter_papers(...)` is shared with the JSON paper list so displayed counts and exports cannot diverge.

`POST /api/conferences/{conference_id}/arxiv-links` accepts the current paper filters, resolves only matching papers without saved IDs, and queues a metadata-only job. `JobService.run_arxiv_links(...)` uses exact normalized-title matching and arXiv's shared three-second request spacing, stores versioned IDs or per-paper lookup errors, and never requests PDF or TeX content. Re-running the same filtered action skips saved matches. RIS records with an ID include arXiv archive metadata. When a PDF URL is saved, it becomes `UR` and both the arXiv page and conference page are preserved in notes; otherwise the arXiv abstract URL remains `UR` with the conference page in a note.

### Zotero Connector

The existing paper dialog and filtered list expose a versioned metadata context for the downloadable PaperRadar web translator. `GET /api/conferences/{conference_id}/zotero` supplies all matching records or a single paper, using the same filters as RIS. Zotero owns PDF downloads; the app returns links only. See [installation, data types, behavior, and verification](ZOTERO.md).

### Collect official PDF URLs

The Papers toolbar has no link-collection buttons. **Scrape papers** in **Scrape conference papers** handles text and PDF URLs together. Saved arXiv IDs, introduction extraction, and the existing link-only API endpoints remain available for API clients.

**Scrape papers** visits conference papers that lack an abstract or PDF URL, and retrieves missing introductions when selected. Saved abstracts are not re-extracted during a link-only retry; saved PDF URLs are not re-collected during an abstract-only retry. The existing collection progress bar and Stop control apply to the combined run. PDF-link errors are saved separately and do not change selected-text status or tagging readiness.

- ECCV: read the **Paper PDF** link from the title-verified poster page.
- ICML/ICLR: an explicit **OpenReview** forum link supplies the ID used in `https://openreview.net/pdf?id=<id>`. This is a URL conversion, with no OpenReview API request, authentication, or PDF availability check.
- NeurIPS: read the explicit **Paper** link on the title-verified abstract page and require a matching year and paper identifier.
- CVPR and ICCV: read each title's sibling `[pdf]` link from the official index, stopping at the next title so links cannot be assigned to another paper. Discovery saves these links too.

Only HTML is fetched. Missing, ambiguous, unsafe, wrong-title, or unsupported links are reported instead of guessed. PDF URLs are displayed in the table and paper details. `ris_text(records: list[dict], config: ConferenceConfig, tagging: TaggingConfig) -> str` exports the saved PDF URL in RIS `UR` (the item URL) and emits no `L1` attachment field. The conference page and, when present, arXiv page remain in `N1` notes. Without a PDF link, `UR` remains the arXiv page if available, otherwise the conference page. This lets Zotero’s **Find Full Text** try the PDF through the item URL; successful downloading still depends on the host. Restart the server and export a new RIS file to use this change; previously imported Zotero items are not modified. The app does not test whether a PDF URL is currently accessible.

Saved errors from the separate arXiv lookup remain in paper details under **arXiv lookup error**. They are omitted from the paper-list error summary so historical arXiv failures cannot be mistaken for PDF-link collection failures. Actual PDF-link and text-collection errors remain visible in the list.

| Interface | Types and behavior |
| --- | --- |
| `POST /api/conferences/{id}/pdf-links` | `PaperFilterRequest`: `search: str = ""`, `status: all/pending/failed/scraped/tagged = all`, optional `domain: list[DomainId] \| DomainId` (empty/null means all; lists match any domain). Returns HTTP 202 with `{id: str}` for the queued job. |
| `GET /api/conferences/{id}/papers` | Adds `pdf_missing: int` for the full filtered set; paper records include nullable `pdf_url: str` and `pdf_error: str`. |
| `conferences.pdf_link(anchors, base_url) -> str or None` | Reads explicit labeled links, validates HTTPS, converts OpenReview IDs, and rejects ambiguity. |
| `conferences.parse_pdf_link(response, config, paper, proceedings=False) -> str` | Verifies the title and supported destination; raises `ValueError` for missing or invalid links. |
| `conferences.is_cvf_paper_page(url, config) -> bool` | Checks the CVF host and exact selected conference edition before following an HTML target. |
| `crawling.crawl_conference(..., links_only: bool = False)` | Link-only mode omits abstract extraction. `CrawlResult` carries `pdf_url: str` or `pdf_error: str`; normal mode also carries the abstract or its independent error. |
| `estimates.needs_scrape(paper: dict, config: ConferenceConfig) -> bool` | Checks mandatory title/abstract/PDF URL and optional introduction; shared by statistics and the worker selection. |
| `JobService.scrape(job_id: str, config: ConferenceConfig, papers: list[dict], client: httpx.Client) -> None` | Collects missing abstracts/links via Scrapy, optional introductions via arXiv, and counts each processed paper once. |
| `filter_papers(records: list[dict], tagging: TaggingConfig, search: str, status: str, domain: list[str] \| str \| None) -> list[dict]` | Validates every domain ID, applies OR across current probabilities at the threshold, and combines with search/status. Used by JSON lists and RIS export. |
| `JobService.start_pdf_links(conference_id: int, paper_ids: list[int]) -> str` | Validates unresolved saved records and queues a resumable `pdf_links` job. |
| `JobService.run_pdf_links(job_id, conference_id, papers) -> None` | Reads the CVPR/ICCV index once or crawls virtual pages; commits each URL/error and updates counts. |
| `JobService.save_pdf_link(result: CrawlResult) -> None` | Saves a URL and clears its error, or stores a link error independently of paper content. |

Tag jobs persist an error on each failed paper and continue until a fifth consecutive failure; any success resets that counter. `JobRequest.retry_failed_only` is valid only for tag jobs and restricts the candidate list to unresolved `tag_error` rows. The tagging progress panel offers that retry after a run with failures, while the normal action still processes all papers lacking a current result.


Field-help text lives in `static/app.js` as `FIELD_HELP: Record<string, string>`, mapping CSS selectors to plain-text explanations. `addFieldHelp(root: Element | Document = document) -> void` adds labelled buttons, tooltip descriptions, and input associations once per matching control while retaining existing input elements and listeners. It runs on initial load and after rendering domain and paper-detail forms. To add help for a new field, add its selector/text to the map and call `addFieldHelp` on its container after dynamic rendering. `showFieldHelp(button: HTMLButtonElement, tooltip: HTMLSpanElement, group: HTMLElement) -> void` opens and positions one explanation; `closeFieldHelp() -> void` hides it and resets accessibility state. `static/style.css` defines the `.help-field`, `.field-heading`, `.field-help-button`, and `.field-help-text` layout and mobile rules.

## Verification and current limits

```sh
uv run python -m unittest discover -s tests -v
node --check research_tagger/static/app.js
node tests/test_selection_ui.js
```

Tests cover strict inputs, parser failures, safe archive handling, multi-file TeX extraction, explicit arXiv title/version validation, token/instruction accounting, stale tag invalidation, disjoint coverage, database persistence, resumable jobs, actual mocked API usage, cross-origin protection, and the original CLI. Scrapy tests use the real engine with an offline download handler, covering repeated crawls in one worker, provider adapters, robots exclusions, redirect validation, cancellation, callback failures, HTTP errors without retries, and preserving abstracts when arXiv extraction fails.

All 84 Python tests and the Node paper UI harness pass. Parallel-tagging checks cover default/legacy settings, range validation, unchanged estimate signatures, bounded concurrency, out-of-order results, cancellation draining, error-limit draining, and failed-only retry. Combined-scrape checks cover missing-link retries on tagged papers, preservation of saved content/results, partial collection failures, and mandatory abstract/PDF collection for title-only tagging. Multi-domain checks cover AND (intersection) semantics, invalid IDs, stale-result exclusion, pagination, and matching RIS exports. Retained coverage includes official source validation, Scrapy robots rules and failures, TeX extraction, prompts, token costs, stale tag detection, paid-request validation, database persistence, prompt-test isolation, job resume/cancellation, and cross-origin protections. Removal checks prove that configured OpenReview environment variables cause no network requests, removed routes/jobs/filters fail explicitly, and abstracts with or without OpenReview links scrape successfully. UI checks cover remaining filters, conference-wide estimates, exact reviewed paid requests, and stale-response rejection.

Live checks on September 22, 2026:

- Before the CVPR source change, full live Scrapy index discovery and one abstract per conference succeeded: ICML 2026 (6,628 papers), ECCV 2026 (2,834), ICLR 2026 (5,468), CVPR 2026 (5,023), and ICCV 2025 (2,701). Robots checking remained enabled. This does not constitute a full abstract scrape.
- NeurIPS live Scrapy verification discovered 5,823 papers in the 2025 main volume and successfully collected one abstract and PDF URL with robots checking enabled; no PDFs were downloaded. Downloaded 2024 and 2000 HTML samples also parsed successfully.
- CVPR now uses the CVF all-papers index. Fixture tests cover 2025/2026 discovery, per-paper PDF links, abstracts, edition validation and repeatable migration. A live check of the new 2026 URL returned HTTP 403; live discovery from that source has not been verified.
- Existing saved conferences and tagging settings are preserved by this fix; completed discovery can be retried through the same API.
- Octopus, arXiv `2310.08588v2`: its source archive yielded a 651-word introduction.
- Automatic arXiv API search returned HTTP 406 in this environment. Explicit-ID metadata/source access worked. Automatic search remains implemented but not live-verified successfully; it fails visibly rather than switching sources.
- No TypeSafe key was supplied, so live Jev classification and tagging accuracy are unverified. Tests use clearly synthetic inputs and mocked Jev responses.
- HTTP routes and JavaScript syntax were checked. Browser interaction, appearance, and WebMCP tool execution were not verified in this update.

TeX conversion is best effort within the documented structural constraints. Complex macros can lose formatting or require an additional parser rule. No claim is made that every arXiv source or conference page is supported.

## Compact interface and paper evaluation details

The scraping and tagging disclosures default to closed. `research-tagger-panel-scraping-overview` and `research-tagger-panel-tagging-overview` store `"open"` or `"closed"` in browser localStorage, shared across conference views. Polling does not reset disclosure state. Header summaries stay visible and show coverage, remaining evaluations, estimated cost and active work. The tag-distribution disclosure defaults to closed. Collection cards show total papers, selected text ready, text errors and missing PDF links; introductions appear only when selected.

**Scrape papers** runs index discovery and missing-content collection as one job. Tag selection is labeled **Tags to apply**; the separate list filter is **Filter papers by tags**, still requiring all checked tags. Paper rows omit source-host and per-paper token repetition. The Collection column lists missing selected content/PDF links or Ready. Status labels display Evaluated and Awaiting evaluation; API filter/status values remain `tagged` and `scraped`. Finished jobs have compact counts without a progress bar; mixed outcomes read Finished with N errors. Stop, Retry, dismissal and restoration keep their existing behavior and saved history.

`GET /api/papers/{paper_id}` now returns the saved paper fields plus `probabilities: dict[str, float] | null`, `estimated_tokens: int | null`, `status: str`, and `context_error: str | null`. It computes these for that paper with the saved conference tag selection and exact request signature. All probabilities are shown, including those below threshold. If the paper text, prompt, model or selected tags no longer match, probabilities are null; old results remain in storage. This read does not call the model or download content.

`paperDetailHTML(paper) -> string` escapes saved values and renders current scores, threshold, token estimate, abstract and resource links. The introduction section appears when selected or saved. Its source/repair form remains available in a closed Advanced disclosure. Zotero help opens a dedicated dialog from Export, Settings, or paper details. RIS downloads still include all matching papers across pages; the Connector remains a browser-toolbar action.

`nextTagId() -> string` chooses an unused `tag_N` identifier against saved tags and current editors. IDs remain read-only, including for unsaved tags. Labels and definitions remain editable; renaming preserves identity. `validateTaggingForm() -> boolean` opens ancestor disclosures of invalid fields before native validation, so collapsed model/pricing controls cannot conceal a validation failure. Existing edition selectors are locked after discovery and their dialog uses Conference details without a misleading Save action. Settings is available below 800px as well as on desktop.

## Single-action conference scraping

**Scrape papers** posts `JobRequest(kind="scrape")` for new or existing conferences. Each job first refreshes the complete index using the selected conference adapter. After successful discovery and a cancellation check, `Database.discover` synchronizes that index atomically. The worker then recalculates missing content and collects only those papers. Existing abstracts, introductions and tag results remain stored; CVF discovery may refresh PDF URLs directly from its index. A supplied `limit` caps content collection after full discovery, not the index size. Paper-list filters do not affect this operation.

The nullable `jobs.stage: str | null` column is migrated at startup. Scrape jobs start with `"discovering"`, then move to `"scraping"`; historical and non-scrape jobs may have null. The API returns this field with each job. The UI shows an indeterminate first-stage progress bar, then the second-stage per-paper counts. Counts represent content collection, not indexed titles. A completed zero-paper run reports that the index was refreshed and all requested content is already available. Index failures or cancellation before synchronization leave saved papers intact; content-stage cancellation preserves completed work. The button is disabled only during an active job or pending field save.

The old `kind="discover"` request remains accepted for existing API clients and historical jobs still render, but it has no separate browser control and is not needed for normal scraping. Paid tagging remains a separate reviewed action.
