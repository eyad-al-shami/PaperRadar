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

## More information

- [App settings and detailed usage](docs/WEB.md)
- [Included dataset and coverage](dataset/README.md)
- [Zotero setup](docs/ZOTERO.md)
- [Command-line usage and Python API](docs/CLI.md)

The app requires Python 3.11+; `uv sync` manages the project environment. The launch command retains its original name, `research-tagger-web`.

## License

App code and project documentation use the [MIT license](LICENSE). The bundled dataset is excluded from that license; paper text retains its original rights. See the [dataset rights notice](dataset/README.md#source-attribution-and-rights).
