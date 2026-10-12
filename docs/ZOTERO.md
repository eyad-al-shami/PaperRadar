# Zotero Connector support

PaperRadar's custom web translator saves the open paper dialog or a selection
from the filtered paper list. There are no separate paper pages. The app supplies
metadata and PDF URLs; Zotero fetches and stores the PDFs. RIS export remains
available independently.

The translator is displayed as **PaperRadar**. Its download filename remains `Research Tagger.js`, and its translator ID is unchanged so it updates existing installations. Install the updated file and reset Connector translators as described below to see the new name.

## Install once: Mac and Chrome

You use three separate places: this web app, the Zotero desktop application, and
Chrome's Zotero Connector extension. The translator is a `.js` file copied into
Zotero's data directory. No API key is needed.

1. **Open Zotero desktop.** In Chrome, click the puzzle-piece Extensions button
   beside the address bar. Find **Zotero Connector** and pin it. If either is
   missing, install it from https://www.zotero.org/download/.
2. **Download the translator.** In PaperRadar's Papers box, expand
   **Export to Zotero → Connector setup and help** and click
   **Download Research Tagger.js**. Locate the file in Downloads (or your browser's
   configured download folder). Leave its name and `.js` extension unchanged.
3. **Open Zotero's installation folder for translators.** Switch to Zotero.
   In the Mac menu bar choose **Zotero → Settings → Advanced**, find
   **Files and Folders**, and click **Show Data Directory**. Finder opens your
   active Zotero data directory. Open its **translators** subfolder.
4. **Copy the downloaded file into that subfolder.** It should sit alongside the
   other `.js` files. Do not use Zotero's File → Import command for this file.
   The project copy is `research_tagger/static/zotero/Research Tagger.js`.
5. **Quit Zotero with ⌘Q, then reopen it.** Keep Zotero running.
6. **Refresh the browser extension's translators.** Return to Chrome, right-click
   the Zotero Connector toolbar button, choose **Options** (or **Preferences**),
   then **Advanced → Reset Translators**. This reloads the extension's translator
   list, not your Zotero library. Do this in the browser extension, not Zotero's
   desktop settings. Refresh the PaperRadar tab with **⌘R**.
7. **Check detection before saving.** Select a conference with papers. Hover over
   the Connector toolbar button: its tooltip should mention **PaperRadar**.
   If it says **Web Page**, check the copied file's location, keep Zotero open,
   repeat the extension reset, and refresh the app tab.

Keep **Zotero Settings → General → Automatically attach associated PDFs and other
files when saving items** enabled. After a first save, expand the new paper in
Zotero using the arrow beside its title. A successfully downloaded PDF appears as
a child attachment; double-click it to open it.

Other browsers and operating systems use the same translator file; Connector
preferences and Zotero's Advanced settings may have different menu labels. Use
**Show Data Directory** to find the active location rather than guessing a path.
The Connector must be allowed to run on the app's `http://127.0.0.1:<port>/` or
`http://localhost:<port>/` origin. The backend must have been restarted after the
Connector API was first added; merely refreshing the browser does not update it.

Sources: [Zotero data directory](https://www.zotero.org/support/zotero_data),
[custom-translator installation](https://forums.zotero.org/discussion/93046/instructions-for-how-to-install-custom-translator),
and [Connector translator refresh](https://www.zotero.org/support/connector/preferences/advanced).
The app does not install extensions or change Zotero settings automatically.

## Save papers

- **One paper:** open its existing dialog and click Zotero Connector in the browser
  toolbar. The detected translator should be **PaperRadar**. It saves that
  paper, regardless of the current list filters.
- **A batch:** close the paper dialog, set the title search, status, and tag filters,
  then click Zotero Connector. Choose papers in Zotero's selection window and a
  destination library/collection. The selection includes every matching page, not
  only the 30 visible rows. All selected tag filters must match.
- Papers without saved PDF URLs are marked **[no PDF link]** in the selection
  window. They save metadata only. Cancelling the selection saves nothing.
- Settings and empty/loading selections expose no saveable PaperRadar context.

Saved items are `conferencePaper` records containing title, available abstract,
conference name, year, current above-threshold tags, source-page note, and optional
arXiv identifier/page note. The item URL uses the PDF link when saved; otherwise it
uses the arXiv page when present or the conference page. Each available PDF is also
supplied as an explicit `application/pdf` attachment for Zotero to download.
Authors and DOIs are not currently collected and are not invented.

This is an explicit save, not synchronization: saving the same records again can
create duplicates, and previous RIS imports are not updated. Download failures
from authentication, unavailable URLs, or rate limits appear in Zotero; Research
Tagger neither proxies PDF bytes nor records Zotero's download status.

## Implementation contract

`GET /api/conferences/{conference_id}/zotero` is read-only. Optional query fields:
`search: str`, `status: str`, repeated `domain: str`, and `paper_id: int`. Search,
status, and tag intersection use the same `filter_papers` function as RIS and the
paper list. There is no pagination. `paper_id` restricts to that paper within the
conference/filter scope; a mismatch returns 404. Unknown tags/statuses return 400.
Empty list selections return an empty `papers` array.

The version-1 JSON response is:

```typescript
{
  version: 1;
  papers: Array<{
    id: number;
    title: string;
    abstract: string | null;
    conference: string;
    year: number;
    source_url: string;
    pdf_url: string | null;
    arxiv_id: string | null;
    tags: string[];
  }>;
}
```

No introductions, errors, settings, API keys, or job mutations are included.

The existing page exposes a nonexecuting JSON script element,
`#research-tagger-zotero`, containing either `null` or:

```typescript
{
  version: 1;
  mode: "single" | "multiple";
  endpoint: string; // same-origin /api/conferences/{id}/zotero?... path
  count: number;
}
```

`updateZoteroContext() -> void` publishes the open dialog before the list context,
clears detection in Settings, and dispatches `ZoteroItemUpdated` only when the
context changes. `state.zoteroPaper` and `state.zoteroList` retain the two contexts.
Filter changes invalidate old list context; request IDs reject stale list
responses. Dialog close (including Escape) restores list detection. Regular
polling does not repeatedly emit unchanged detection events.

The translator's `getContext(doc) -> object|null` validates the contract and API
path. `detectWeb(doc, url) -> string|false` recognizes loopback app roots only and
returns `conferencePaper` or `multiple`. `doWeb(doc, url) -> Promise<void>` captures
the context, reads metadata once using `requestJSON`, and calls
`Zotero.selectItems` for batches. Selection cancellation returns immediately.
`savePaper(paper) -> Promise<void>` maps a record to a Zotero item, escapes note
text, supplies the PDF attachment URL, and calls `item.complete()`. It makes no
PDF requests itself. Unsupported versions fail explicitly.

The translator follows Zotero's [web translator and attachment API](https://www.zotero.org/support/dev/translators/coding).
Dynamic detection uses the documented [ZoteroItemUpdated event](https://www.zotero.org/support/dev/exposing_metadata#force_zotero_to_refresh_metadata).

## Verify and troubleshoot

```sh
.venv/bin/python -m unittest discover -s tests -q
node --check research_tagger/static/app.js
node tests/test_selection_ui.js
node tests/test_zotero_translator.js
```

The translator file starts with Zotero's JSON metadata header, so it is not a
standalone Node program. The dedicated Node harness parses that header and runs
the actual translator functions against synthetic Zotero APIs. It verifies
single/batch metadata, selection cancellation, PDF attachments, absent PDF URLs,
loopback scope, and contract validation without modifying a Zotero library.
API tests verify all-page filter intersections and single-paper scope.

For an end-to-end check after installation: open a paper whose PDF link works in
the browser, save it to a test Zotero collection, and verify both the metadata and
PDF attachment. Then close the dialog, filter the list, and select two papers via
the Connector. The automated tests do not establish that a particular host allows
Zotero to download its PDFs.

If Zotero detects **Web Page** instead of **PaperRadar**, confirm that the
translator is installed in the active data directory, desktop Zotero is open,
the Connector's translators were refreshed, and the app tab was reloaded. If
metadata saves but a PDF does not, verify that the app has a saved PDF link and
that it opens, then inspect Zotero's save error. No matching PDF is guessed when
its URL is missing.
