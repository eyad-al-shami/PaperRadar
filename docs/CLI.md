# Command-line usage and API

The command-line workflow collects ECCV papers into JSON files. For the browser app and bundled dataset, use the [quick start](../README.md). Run the commands below from the project root.

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

The CLI uses the same ECCV virtual-site adapter as the web app. It reads the selected edition's `papers.html` and validates every poster entry in its server-rendered `noscript` list. It does not request static JSON feeds or their paginated API. Navigation and duplicate presentation links outside the paper list are excluded. Scrapy reads `h1.event-title` and `#abstractText .abstract-text-inner` from poster pages, verifies the indexed title, and excludes lay summaries. Downloads use one concurrent request with a 0.5-second delay, observe robots.txt, and validate public HTTPS destinations including redirects. It does not download PDFs. Source URLs and conference/year are retained as provenance and are not sent to Jev. See [conference settings and parser requirements](WEB.md#conference-settings) before running a scrape.

The crawler uses Scrapy's asyncio mode without a Twisted reactor so multiple jobs can run sequentially in the same local web worker. Scrapy currently marks this integration and its aiohttp download handler as experimental; dependencies are restricted to the tested 2.19 release family. See [crawler configuration and API](WEB.md#scrapy-crawler) before extending or deploying it.

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

