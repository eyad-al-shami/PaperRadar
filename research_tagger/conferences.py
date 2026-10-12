"""Official virtual-site, CVF and NeurIPS proceedings adapters."""

import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from scrapy import Selector
from scrapy.http import HtmlResponse

from .arxiv import normalized_title
from .web_models import ConferenceConfig


def text_content(element: Selector) -> str:
    """Normalize an element's visible text, excluding scripts and styles."""
    return " ".join(" ".join(element.xpath(".//text()[not(ancestor::script) and not(ancestor::style)]").getall()).split())


def required_text(response: HtmlResponse, selector: str, field: str) -> str:
    """Require exactly one matching element containing nonnumeric text."""
    elements = response.css(selector)
    if len(elements) != 1:
        raise ValueError(f"Expected one {field} at {response.url}.")
    value = text_content(elements[0])
    if not any(character.isalpha() for character in value):
        raise ValueError(f"Missing or invalid {field} at {response.url}.")
    return value


def parse_index(response: HtmlResponse, config: ConferenceConfig) -> list[dict]:
    """Extract edition-matched CVPR/ICCV titles, page URLs and optional PDF URLs."""
    papers = {}
    for anchor in response.css("dt.ptitle a[href]"):
        if not anchor.attrib.get("href"):
            raise ValueError("The paper link selector matched an element without an href.")
        url = urljoin(response.url, anchor.attrib["href"])
        parsed = urlsplit(url)
        if parsed.hostname != config.index_url.host:
            continue
        if not re.fullmatch(rf"/content/{config.name}{config.year}/html/[^/]+\.html", parsed.path):
            continue
        title = text_content(anchor)
        if not any(c.isalpha() for c in title):
            raise ValueError(f"Invalid indexed title at {url}.")
        if url in papers and papers[url]["title"] != title:
            raise ValueError(f"Conflicting titles for {url}.")
        papers[url] = {"source_url": url, "title": title}
        links = []
        for sibling in anchor.xpath("../following-sibling::*"):
            if sibling.root.tag == "dt":
                break
            links.extend(sibling.css("a[href]"))
        try:
            pdf_url = pdf_link(links, response.url)
            if pdf_url:
                target = urlsplit(pdf_url)
                if target.hostname != config.index_url.host or not re.fullmatch(
                    rf"/content/{config.name}{config.year}/papers/[^/]+\.pdf", target.path,
                ):
                    raise ValueError("The indexed PDF link does not belong to this conference edition.")
                papers[url]["pdf_url"] = pdf_url
        except ValueError as error:
            papers[url]["pdf_error"] = str(error)
    if not papers:
        raise ValueError("No paper links matched this conference edition; its paper index may not be published.")
    return list(papers.values())


def parse_abstract(response: HtmlResponse, config: ConferenceConfig, paper: dict) -> str:
    """Extract an abstract only when the page title matches the indexed paper."""
    validate_paper_title(response, config, paper)
    if config.source == "neurips":
        return neurips_abstract(response)
    selector = "#abstractText .abstract-text-inner" if config.source == "virtual" else "#abstract"
    return required_text(response, selector, "abstract")


def validate_paper_title(response: HtmlResponse, config: ConferenceConfig, paper: dict,
                         proceedings: bool = False) -> None:
    """Reject metadata or text from a page that does not match the indexed title."""
    title_selector = "h1.event-title" if config.source == "virtual" and not proceedings else "#papertitle"
    if config.source == "neurips":
        title_selector = "h1.paper-title"
    title = required_text(response, title_selector, "title")
    if normalized_title(title) != normalized_title(paper["title"]):
        raise ValueError("The abstract page title does not match the indexed paper.")


def pdf_link(anchors: list[Selector], base_url: str) -> str | None:
    """Read a unique Paper PDF/pdf/OpenReview link without requesting its contents.

    OpenReview forum IDs map to its public PDF URL. A CVF HTML target is returned
    unchanged so the spider can read its explicit pdf link. Missing links return
    None; ambiguous or malformed links fail instead of selecting a different file.
    """
    urls = set()
    for anchor in anchors:
        label = text_content(anchor).strip("[] ").casefold()
        if label not in {"paper pdf", "pdf", "openreview"}:
            continue
        url = urljoin(base_url, anchor.attrib["href"])
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("The paper PDF link must be an HTTPS URL without credentials.")
        if parsed.hostname == "openreview.net" and parsed.path in {"/forum", "/pdf"}:
            ids = parse_qs(parsed.query).get("id", [])
            if len(ids) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]+", ids[0]):
                raise ValueError("The OpenReview link has no unique valid paper ID.")
            url = "https://openreview.net/pdf?" + urlencode({"id": ids[0]})
        elif label == "openreview":
            raise ValueError("The OpenReview button does not link to an OpenReview paper.")
        urls.add(url)
    if len(urls) > 1:
        raise ValueError("Multiple different paper PDF links were found.")
    return next(iter(urls), None)


def is_cvf_paper_page(url: str, config: ConferenceConfig) -> bool:
    """Identify a CVF HTML landing page from the selected conference edition."""
    parsed = urlsplit(url)
    return parsed.hostname == "openaccess.thecvf.com" and bool(re.fullmatch(
        rf"/content/{config.name}{config.year}/html/[^/]+\.html", parsed.path,
    ))


def parse_pdf_link(response: HtmlResponse, config: ConferenceConfig, paper: dict,
                   proceedings: bool = False) -> str:
    """Require a matching paper title and its explicit PDF or proceedings link."""
    validate_paper_title(response, config, paper, proceedings)
    if config.source == "neurips":
        return neurips_pdf(response, config)
    url = pdf_link(list(response.css("a[href]")), response.url)
    if not url:
        raise ValueError("No paper PDF link was provided on this page.")
    parsed = urlsplit(url)
    if not (parsed.path.lower().endswith(".pdf") or
            parsed.hostname == "openreview.net" and parsed.path == "/pdf" or
            parsed.hostname == "arxiv.org" and parsed.path.startswith("/pdf/") or
            not proceedings and is_cvf_paper_page(url, config)):
        raise ValueError("The Paper PDF button points to an unsupported page, not a direct PDF link.")
    return url


def parse_virtual_index(response: HtmlResponse, config: ConferenceConfig) -> list[dict]:
    """Read the edition's server-rendered poster list from its noscript HTML.

    The official papers.html pages publish one full list inside main/noscript/ul.
    Navigation links, presentation links, and blocked JSON feeds are not used.
    Missing lists, malformed entries, wrong editions and conflicting titles fail.
    """
    if response.url != str(config.index_url):
        raise ValueError("The conference index redirected to a different edition or page.")
    lists = response.css("main noscript.noscript > ul")
    if len(lists) != 1 or not lists[0].xpath("./li"):
        raise ValueError("Expected one nonempty paper list in the conference index HTML; no index was saved.")
    papers = {}
    for item in lists[0].xpath("./li"):
        anchors = item.css("a[href]")
        if len(anchors) != 1:
            raise ValueError("A conference paper list entry must contain exactly one paper link.")
        anchor = anchors[0]
        url = urljoin(response.url, anchor.attrib["href"])
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != config.index_url.host or parsed.query or parsed.fragment
                or not re.fullmatch(rf"/virtual/{config.year}/poster/\d+", parsed.path)):
            raise ValueError("A paper list entry belongs to a different edition or has an invalid paper URL.")
        title = text_content(anchor)
        if not any(character.isalpha() for character in title):
            raise ValueError(f"Invalid indexed title at {url}.")
        if url in papers and papers[url]["title"] != title:
            raise ValueError(f"Conflicting titles for {url}.")
        papers[url] = {"title": title, "source_url": url}
    return list(papers.values())


def proceedings_url(response: HtmlResponse, config: ConferenceConfig) -> str:
    """Resolve exactly one selected-year main volume; exclude separate Creative AI volumes."""
    urls = set()
    for anchor in response.css("ul.proceedings-list a[href]"):
        url = urljoin(response.url, anchor.attrib["href"])
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.netloc == config.index_url.host
                and not parsed.query and not parsed.fragment
                and re.fullmatch(rf"/paper_files/paper/{config.year}(?:/vol\d+-main-conference)?", parsed.path)
                and "creative ai" not in text_content(anchor).casefold()):
            urls.add(url)
    if len(urls) != 1:
        raise ValueError(f"Expected one main NeurIPS {config.year} proceedings link; the edition may not be published.")
    return urls.pop()


def parse_neurips_index(response: HtmlResponse, config: ConferenceConfig) -> list[dict]:
    """Return unique {title: str, source_url: str} records from the selected main volume."""
    parsed = urlsplit(response.url)
    if (parsed.scheme != "https" or parsed.netloc != config.index_url.host
            or parsed.query or parsed.fragment
            or not re.fullmatch(rf"/paper_files/paper/{config.year}(?:/vol\d+-main-conference)?", parsed.path)):
        raise ValueError("The NeurIPS index redirected to a different edition or page.")
    lists = response.css("ul.paper-list")
    if len(lists) != 1 or not lists[0].xpath("./li"):
        raise ValueError("Expected one nonempty NeurIPS paper list.")
    papers = {}
    for entry in lists[0].xpath("./li"):
        anchors = entry.css('a[title="paper title"][href]')
        if len(anchors) != 1:
            raise ValueError("Expected one title link per NeurIPS paper.")
        anchor = anchors[0]
        url = urljoin(response.url, anchor.attrib["href"])
        target = urlsplit(url)
        if (target.scheme != "https" or target.netloc != config.index_url.host
                or target.query or target.fragment
                or not re.fullmatch(rf"/paper_files/paper/{config.year}/hash/[a-f0-9]{{32}}-Abstract(?:-[A-Za-z_]+)?\.html", target.path)):
            raise ValueError("A NeurIPS paper link belongs to a different edition or has an invalid URL.")
        title = text_content(anchor)
        if not any(character.isalpha() for character in title):
            raise ValueError("Missing or invalid NeurIPS paper title.")
        if url in papers and papers[url]["title"] != title:
            raise ValueError(f"Conflicting titles for {url}.")
        papers[url] = {"title": title, "source_url": url}
    return list(papers.values())


def neurips_abstract(response: HtmlResponse) -> str:
    """Read the Abstract section, including nested paragraphs repaired by the HTML parser."""
    sections = response.xpath('//section[contains(concat(" ", normalize-space(@class), " "), " paper-section ")][h2[normalize-space()="Abstract"]]')
    if len(sections) != 1:
        raise ValueError("Expected one NeurIPS abstract section.")
    value = " ".join(" ".join(sections[0].xpath('.//text()[not(ancestor::h2) and not(ancestor::script) and not(ancestor::style)]').getall()).split())
    if not any(character.isalpha() for character in value):
        raise ValueError("Missing or invalid NeurIPS abstract.")
    return value


def neurips_pdf(response: HtmlResponse, config: ConferenceConfig) -> str:
    """Read the explicit Paper button; require its year/hash to match this paper, without download."""
    anchors = [anchor for anchor in response.css('.paper-actions a[href]') if text_content(anchor) == "Paper"]
    if len(anchors) != 1:
        raise ValueError("Expected one NeurIPS Paper PDF link.")
    url = urljoin(response.url, anchors[0].attrib["href"])
    parsed = urlsplit(url)
    expected = urlsplit(response.url).path.replace('/hash/', '/file/').replace('-Abstract', '-Paper').removesuffix('.html') + '.pdf'
    if (parsed.scheme != "https" or parsed.netloc != config.index_url.host
            or parsed.query or parsed.fragment or parsed.path != expected
            or not parsed.path.startswith(f"/paper_files/paper/{config.year}/file/")):
        raise ValueError("The NeurIPS PDF link does not match this paper and edition.")
    return url
