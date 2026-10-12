# PaperRadar collected papers and tagging results

`research-tagger.sqlite3` is a standalone SQLite snapshot prepared on 2026-10-12. It includes the collected paper text and saved classifications for CVPR 2026, ECCV 2026, ICML 2026, and NeurIPS 2025.

| Saved content | Count |
| --- | ---: |
| Paper records, including inactive records | 19,327 |
| Nonempty abstracts | 19,323 |
| PDF links | 19,309 |
| Tagging result records | 25,933 |
| Introductions | 0 |

No PDFs or TeX archives are bundled. Missing text or links remain missing. Multiple tagging result records can belong to one paper because results are retained for different request signatures; the count is not the number of uniquely tagged papers. Tags and probabilities are model-generated annotations, not labels endorsed by the paper authors or conferences.

## Use the snapshot

From the project root, after cloning the repository:

```sh
uv sync
uv run research-tagger-web
```

Open http://127.0.0.1:8000. Stop the server with Ctrl+C. On first launch, the app automatically copies this snapshot to `data/research-tagger.sqlite3`. No manual import is needed. Subsequent scraping, settings changes and tagging are saved only to that working copy. Existing databases are never replaced or merged with a newer snapshot when restarting or updating the project. An explicit `--database` path bypasses automatic seeding and creates an empty database if that path does not exist.

Browsing saved papers and classifications does not require a TypeSafe key. New tagging requests require your own key and incur API charges. The app shows results matching the current tagging configuration; changing the prompt, selected tags or paper text can make previously saved results outdated.

## Contents and types

The schema is in [schema.sql](../research_tagger/schema.sql).

- `conferences`: integer conference IDs, JSON configuration text, and nullable discovery timestamps.
- `papers`: integer IDs and conference references, title and source URL text, nullable abstracts, introductions, identifiers and PDF URLs, integer activity flags, and nullable scrape timestamps. Every source paper row is retained.
- `tag_results`: integer IDs and paper references, request signature text, JSON probability mappings, model names, integer input/output token counts, numeric USD costs and creation timestamps. All saved result versions are retained.
- `settings`: key/value text containing the tagging configuration as JSON, including tag IDs, names, definitions, prompt, threshold, model and pricing settings.
- `jobs`: the application table is present but empty in this snapshot.

Operational job history and per-paper error messages were removed from this publication copy. The original local database was not modified. The copy was compacted after removal, checked for SQLite integrity and foreign-key consistency, and compared against the source for every retained paper field, tagging result, conference and setting. API credentials are not stored by the app in SQLite and are not bundled.

## Source attribution and rights

Each paper retains its source URL and conference/year. PDF links point to external hosts; their continued availability is controlled by those hosts. This snapshot reflects the saved collection, not a guarantee that every conference paper is present or successfully scraped.

Paper text remains subject to its original rights and licenses. Inclusion in this dataset does not grant additional rights or place third-party text under a software license. Per-paper redistribution permissions have not been audited, and this snapshot is not a license clearance.

- [CVF copyright notice](https://openaccess.thecvf.com/)
- [ICML copyright FAQ](https://icml.cc/FAQ)
- [NeurIPS copyright FAQ](https://nips.cc/FAQ/Copyright)
- [ECCV conference website](https://eccv.ecva.net/)

Check the applicable paper/source license for your intended reuse and preserve any required author attribution and license notices. TypeSafe-generated outputs are also subject to the applicable service agreement.
