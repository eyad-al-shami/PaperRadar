"""Match arXiv titles and extract introductions from source without executing TeX."""

import gzip
import io
import posixpath
import re
import tarfile
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

import httpx
from bs4 import BeautifulSoup
from pylatexenc.latex2text import LatexNodes2Text

from .network import download

ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?\Z")
MAX_SOURCE = 64_000_000
_lock = threading.Lock()
_last_request = 0.0


def normalized_title(title: str) -> str:
    """Normalize typography for exact matching, without fuzzy paper substitution."""
    title = LatexNodes2Text().latex_to_text(title)
    return "".join(c for c in unicodedata.normalize("NFKD", title).casefold() if c.isalnum())


def arxiv_download(client: httpx.Client, url: str) -> bytes:
    """Serialize arXiv requests and retry brief API/capacity rejections.

    arXiv occasionally returns 406, 429, or a server error for a valid query.
    Each retry observes the same three-second request spacing required by arXiv.
    """
    global _last_request
    for attempt in range(3):
        with _lock:
            time.sleep(max(0, 3 - (time.monotonic() - _last_request)))
            _last_request = time.monotonic()
            try:
                return download(client, url, limit=32_000_000)
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                if attempt == 2 or (status not in {406, 429} and status < 500):
                    raise
    raise AssertionError("The bounded arXiv retry loop did not return or raise.")


def find_arxiv(client: httpx.Client, title: str, identifier: str | None = None) -> str:
    """Resolve one exact normalized title, returning a versioned arXiv ID.

    An explicitly supplied ID is still checked against the title. Missing and
    ambiguous matches are errors; callers can expose an ID field for correction.
    """
    if identifier and not ARXIV_ID.fullmatch(identifier):
        raise ValueError("Invalid arXiv ID; use a value such as 2403.12345v1.")
    if identifier:
        soup = BeautifulSoup(arxiv_download(client, f"https://arxiv.org/abs/{identifier}"), "html.parser")
        titles = soup.select('meta[name="citation_title"]')
        versions = soup.select('meta[property="og:url"]')
        if len(titles) != 1 or normalized_title(titles[0].get("content", "")) != normalized_title(title):
            raise ValueError("The specified arXiv paper does not match the conference title.")
        if len(versions) != 1:
            raise ValueError("The arXiv page did not identify its source version.")
        version = versions[0].get("content", "").removeprefix("https://arxiv.org/abs/")
        if not ARXIV_ID.fullmatch(version) or not re.search(r"v\d+$", version):
            raise ValueError("The arXiv page did not provide a valid versioned ID.")
        if re.sub(r"v\d+$", "", version) != re.sub(r"v\d+$", "", identifier):
            raise ValueError("The arXiv page returned a different paper ID.")
        if re.search(r"v\d+$", identifier) and version != identifier:
            raise ValueError("The arXiv page returned a different paper version.")
        return version
    params = {
        "search_query": 'ti:"' + re.sub(r'["\\]', " ", title) + '"', "max_results": 10,
    }
    url = str(httpx.URL("https://export.arxiv.org/api/query", params=params))
    root = ET.fromstring(arxiv_download(client, url))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    matches = set()
    for entry in root.findall("a:entry", ns):
        if normalized_title(entry.findtext("a:title", "", ns)) == normalized_title(title):
            entry_id = entry.findtext("a:id", "", ns).split("/abs/")[-1]
            if not ARXIV_ID.fullmatch(entry_id):
                raise ValueError("arXiv returned an invalid article ID.")
            matches.add(entry_id)
    if len(matches) != 1:
        raise ValueError(f"Expected one exact arXiv title match; found {len(matches)}. Set or correct the arXiv ID.")
    return matches.pop()


def source_files(content: bytes) -> dict[str, str]:
    """Read a plain/gzipped TeX file or tar archive into memory.

    Limit decompression and TeX size; reject traversal, links, duplicate names,
    undecodable text, and non-TeX payloads. Never extract files to disk.
    """
    if content.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
            content = stream.read(MAX_SOURCE + 1)
    if len(content) > MAX_SOURCE:
        raise ValueError("Uncompressed arXiv source exceeds 64 MB.")
    if content.lstrip().startswith(b"%PDF"):
        raise ValueError("arXiv provides a PDF, not TeX source, for this paper.")
    files = {}
    try:
        archive = tarfile.open(fileobj=io.BytesIO(content), mode="r:")
    except tarfile.ReadError:
        text = content.decode("utf-8")
        if "\\documentclass" not in text or "\\begin{document}" not in text:
            raise ValueError("The arXiv download is not a supported TeX source.")
        return {"main.tex": text}
    total = 0
    with archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise ValueError("Unsafe path or link in arXiv source archive.")
            if not member.isfile() or path.suffix != ".tex":
                continue
            total += member.size
            if total > 8_000_000 or len(files) >= 500:
                raise ValueError("TeX source exceeds extraction limits.")
            name = str(path)
            if name in files:
                raise ValueError("Duplicate TeX filename in source archive.")
            files[name] = archive.extractfile(member).read().decode("utf-8")
    if not files:
        raise ValueError("No TeX files available in the arXiv source archive.")
    return files


def without_comments(text: str) -> str:
    """Remove unescaped TeX comments while retaining escaped percent signs."""
    return re.sub(r"(?<!\\)%[^\n]*", "", text)


def extract_introduction(content: bytes) -> str:
    """Expand local input/include files and extract the Introduction section.

    Require one main document and one section titled Introduction (optionally
    Introduction and …). Preserve subsections until the next section/chapter.
    Unresolved includes, cycles, unsupported structures, and empty output fail.
    TeX is parsed as text and never compiled or executed.
    """
    files = {name: without_comments(text) for name, text in source_files(content).items()}
    mains = [name for name, text in files.items() if re.search(r"\\begin\s*\{document\}", text)]
    if len(mains) != 1:
        raise ValueError(f"Expected one main TeX document; found {len(mains)}.")

    def expand(name: str, stack: tuple[str, ...] = ()) -> str:
        if name in stack or len(stack) >= 20:
            raise ValueError("Cyclic or too deeply nested TeX includes.")

        def include(match: re.Match) -> str:
            target = match.group(1).strip()
            if PurePosixPath(target).is_absolute() or ".." in PurePosixPath(target).parts:
                raise ValueError("Unsafe TeX include path.")
            if not target.endswith(".tex"):
                target += ".tex"
            candidates = {posixpath.normpath(posixpath.join(posixpath.dirname(name), target)), target} & files.keys()
            if len(candidates) != 1:
                raise ValueError(f"Missing or ambiguous TeX include: {target}")
            return expand(candidates.pop(), (*stack, name))

        result = re.sub(r"\\(?:input|include)\s*\{([^{}]+)\}", include, files[name])
        if len(result) > 8_000_000:
            raise ValueError("Expanded TeX exceeds extraction limits.")
        return result

    text = expand(mains[0])
    text = re.split(r"\\begin\s*\{document\}", text, maxsplit=1)[1]
    text = re.split(r"\\end\s*\{document\}", text, maxsplit=1)[0]
    headings = list(re.finditer(r"\\(?:section|chapter)\*?\s*(?:\[[^\]]*\]\s*)?\{([^{}]*)\}", text))
    introductions = [i for i, heading in enumerate(headings) if re.fullmatch(
        r"introduction(?:\s+and\s+.+)?", heading.group(1).strip(), re.IGNORECASE,
    )]
    if len(introductions) != 1:
        raise ValueError("Could not identify exactly one Introduction section in the TeX source.")
    index = introductions[0]
    stop = headings[index + 1].start() if index + 1 < len(headings) else len(text)
    section = text[headings[index].end():stop]
    section = re.split(r"\\(?:bibliography|printbibliography|appendix)\b", section, maxsplit=1)[0]
    introduction = LatexNodes2Text().latex_to_text(section)
    introduction = "\n\n".join(" ".join(p.split()) for p in introduction.split("\n\n") if p.strip())
    if len(introduction.split()) < 10:
        raise ValueError("Extracted Introduction is empty or too short to validate.")
    return introduction


def fetch_introduction(client: httpx.Client, title: str, identifier: str | None = None) -> tuple[str, str]:
    """Return (versioned arXiv ID, introduction text) from a verified title match."""
    article_id = find_arxiv(client, title, identifier)
    content = arxiv_download(client, f"https://arxiv.org/src/{article_id}")
    return article_id, extract_introduction(content)
