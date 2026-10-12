# PaperRadar

Browse conference papers, filter them by research tags, and export your selection to Zotero. Collect and tag papers from ICML, ECCV, ICLR, CVPR, ICCV, and NeurIPS.

## Quick start

Install [Git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```sh
git clone https://github.com/eyad-al-shami/PaperRadar.git
cd PaperRadar
uv sync
uv run research-tagger-web
```

Open **http://127.0.0.1:8000** in your browser.

On first launch, PaperRadar automatically loads **19,327 paper records and 25,933 saved tagging results**. No import or API key is needed to browse them.

Keep the terminal open while using the app. Press **Ctrl+C** to stop it. To start it again, run `uv run research-tagger-web` from the project folder.

## Use the app

- **Browse:** select a conference, search for papers, and filter by tags. Selecting multiple tags shows papers matching all of them.
- **Collect:** click **Add conference**, choose a year, then **Scrape papers**. This collects titles, abstracts, and PDF links. Introduction text is optional.
- **Tag:** add your TypeSafe API key in **Settings**, choose **Tags to apply**, review the estimated cost, then click **Tag papers**. New tagging requests are paid.
- **Customize:** edit tag descriptions and the shared prompt in **Settings**. Use **Test this prompt** to try a small sample.
- **Export:** use **Export to Zotero** for the matching papers. See the [Zotero guide](docs/ZOTERO.md) for PDF attachment support.

Your changes are saved locally in `data/research-tagger.sqlite3`. Restarting or updating the app preserves your existing database. PDFs are linked, not bundled.

## Add a new tag

1. Open **Settings → Tagging settings → Research tags** and click **＋ Add tag**.
2. Enter a **Tag name** and **Definition and inclusion criteria** using the guide below.
3. Optionally use **Test this prompt**: select a conference and **1–10 papers**, click **Test prompt**, and review the estimated cost before confirming. Tests use the current form values, incur API charges, and do not save paper tags.
4. Click **Save tagging settings**. Tag definitions and the shared prompt apply across conferences.
5. Return to a conference, open **Tag conference papers → Tags to apply**, select the new tag, then click **Tag papers** and confirm the estimated cost.

| Field | What it means |
| --- | --- |
| **Tag name** | The name shown in paper tags and filters, such as **World models**. It is also sent to the model. |
| **Definition and inclusion criteria** | Explains what qualifies for this tag. Describe the research focus, what to include, and closely related work to exclude. The model receives this text with each paper; it directly guides classification. |
| **Advanced → Stable ID** | An automatically generated, read-only identifier connecting the tag to saved results. Renaming a tag does not change its ID. |
| **Tag probability threshold** | A shared cutoff for assigning all tags. For example, `0.70` means a probability of at least 70%. Changing only this cutoff reuses saved probabilities without new model calls. |
| **Tagging prompt → Question / instructions** | The shared question sent for every selected tag, alongside that tag's name and definition. `{fields}` becomes the selected paper sections, such as “title and abstract”. |

For example, a **World models** definition could be:

> Include papers that learn environment dynamics to predict future states or support planning and control. The learned dynamics should be a substantive contribution. Exclude papers that only generate images without modeling environment dynamics.

Saving changes does not start tagging automatically. Changes to a tag's name, definition, or the shared prompt can make saved results outdated for the new request; re-tagging requires new paid calls. The previous results remain stored.

## Zotero

**RIS export:** choose **Export to Zotero → Download matching papers (.RIS)**, then import the file into Zotero. The export covers all matching papers across pages and uses the saved PDF link as the item URL so Zotero's **Find Full Text** can try it. No PDF file or attachment (`L1`) field is exported. Conference and arXiv page links are preserved in notes; when no PDF link is saved, the item URL uses the arXiv page if available, otherwise the conference page. Re-importing does not update existing Zotero items automatically.

**Zotero Connector:** open **Export to Zotero → Connector setup and help** (also available in Settings) to download and install the custom translator. The browser extension can then save the open paper dialog or choose from all papers matching your list filters, with Zotero downloading PDFs directly from saved URLs. See [setup and usage](docs/ZOTERO.md).

## More information

- [App settings and detailed usage](docs/WEB.md)
- [Included dataset and coverage](dataset/README.md)
- [Zotero setup](docs/ZOTERO.md)
- [Command-line usage and Python API](docs/CLI.md)

The app requires Python 3.11+; `uv sync` manages the project environment. The launch command retains its original name, `research-tagger-web`.

## TODOs

- Support open-source models as alternatives to Jev for paper tagging.
- Add support for more conferences.
- Simplify Zotero setup and paper export.

## License

App code and project documentation use the [MIT license](LICENSE). The bundled dataset is excluded from that license; paper text retains its original rights. See the [dataset rights notice](dataset/README.md#source-attribution-and-rights).
