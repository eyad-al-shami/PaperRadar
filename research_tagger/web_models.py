"""Validated browser API inputs; persisted configuration uses the same types."""

from datetime import datetime, timezone
from typing import Annotated, Literal, Self

from pydantic import Field, HttpUrl, model_validator

from .models import Domain, DomainId, Probability, Record, TaggingPrompt, Text, validate_domains

PaperField = Literal["title", "abstract", "introduction"]
ConferenceName = Literal["ICML", "ECCV", "ICLR", "CVPR", "ICCV", "NeurIPS"]
CONFERENCE_URLS = {
    "ICML": "https://icml.cc/virtual/{year}/papers.html",
    "ECCV": "https://eccv.ecva.net/virtual/{year}/papers.html",
    "ICLR": "https://iclr.cc/virtual/{year}/papers.html",
    "CVPR": "https://openaccess.thecvf.com/CVPR{year}?day=all",
    "ICCV": "https://openaccess.thecvf.com/ICCV{year}?day=all",
    "NeurIPS": "https://proceedings.neurips.cc/",
}


def conference_options() -> list[dict]:
    """Return fixed providers and selectable years; discovery checks actual availability."""
    current_year = datetime.now(timezone.utc).year
    return [{"name": name, "url_template": template,
             "years": [year for year in range(current_year, 1999, -1)
                       if not (name == "ECCV" and year % 2 or name == "ICCV" and not year % 2)]}
            for name, template in CONFERENCE_URLS.items()]


class ConferenceSelection(Record):
    """Public conference input: a supported name, one year, and selected paper fields."""

    name: ConferenceName
    year: Annotated[int, Field(ge=2000, le=2100)]
    fields: Annotated[list[PaperField], Field(min_length=1)] = ["title", "abstract"]

    @model_validator(mode="after")
    def validate_edition(self) -> Self:
        """Enforce year parity/current-year bounds and reject repeated paper fields."""
        if self.name == "ECCV" and self.year % 2:
            raise ValueError("ECCV requires an even year.")
        if self.name == "ICCV" and not self.year % 2:
            raise ValueError("ICCV requires an odd year.")
        if self.year > datetime.now(timezone.utc).year:
            raise ValueError("Choose the current year or an earlier edition.")
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("Select each paper field only once.")
        return self

    def to_config(self) -> "ConferenceConfig":
        """Derive the official URL and Scrapy adapter; callers cannot supply source URLs."""
        return ConferenceConfig(name=self.name, year=self.year, fields=self.fields,
                                source="neurips" if self.name == "NeurIPS" else "cvf" if self.name in {"CVPR", "ICCV"} else "virtual",
                                index_url=CONFERENCE_URLS[self.name].format(year=self.year))


class ConferenceConfig(ConferenceSelection):
    """Stored edition, derived source, and optional active domain selection."""

    source: Literal["virtual", "cvf", "neurips"]
    index_url: HttpUrl
    domain_ids: list[DomainId] | None = None

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        """Reject obsolete sources and URLs inconsistent with the selected edition."""
        expected_source = "neurips" if self.name == "NeurIPS" else "cvf" if self.name in {"CVPR", "ICCV"} else "virtual"
        if self.source != expected_source or str(self.index_url) != CONFERENCE_URLS[self.name].format(year=self.year):
            raise ValueError("Use the official source and URL for the selected conference and year.")
        if self.domain_ids is not None and (not self.domain_ids or len(self.domain_ids) != len(set(self.domain_ids))):
            raise ValueError("Select one or more unique research tags.")
        return self


class TaggingConfig(Record):
    """Tag definitions, prompt/model/pricing, and the 1–16 conference request limit."""

    domains: list[Domain]
    prompt: TaggingPrompt = Field(default_factory=TaggingPrompt)
    model: Text = "jev-1.13.0"
    parallel_requests: Annotated[int, Field(ge=1, le=16)] = 4
    threshold: Probability = 0.7
    price_per_million: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.042
    pricing_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] = "2026-09-22"

    @model_validator(mode="after")
    def check_domains(self) -> Self:
        """Validate a nonempty taxonomy before estimation or paid inference."""
        validate_domains(self.domains)
        return self


class JobRequest(Record):
    """Start a full job or an explicitly limited sample; no implicit sample cap."""

    kind: Literal["discover", "scrape", "tag"]
    limit: Annotated[int, Field(ge=1)] | None = None
    estimate_signature: str | None = None
    domain_ids: list[DomainId] | None = None
    retry_failed_only: bool = False

    @model_validator(mode="after")
    def validate_domain_selection(self) -> Self:
        """Require a unique domain selection only for paid conference tagging."""
        if self.kind == "tag" and not self.domain_ids:
            raise ValueError("Select at least one tag before starting.")
        if self.kind != "tag" and self.domain_ids is not None:
            raise ValueError("Tag selection is only supported for tagging jobs.")
        if self.kind != "tag" and self.retry_failed_only:
            raise ValueError("Failed-only retry is only supported for tagging jobs.")
        if self.domain_ids is not None and len(self.domain_ids) != len(set(self.domain_ids)):
            raise ValueError("Select each research tag only once.")
        return self


class TagEstimateRequest(Record):
    """Research domains to include in a conference tagging estimate and prompt."""

    domain_ids: Annotated[list[DomainId], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_domains(self) -> Self:
        """Reject duplicate domain questions before estimating or billing."""
        if len(self.domain_ids) != len(set(self.domain_ids)):
            raise ValueError("Select each research tag only once.")
        return self


class PromptTestRequest(Record):
    """Preview or run an ephemeral prompt test on a small deterministic paper sample."""

    conference_id: Annotated[int, Field(ge=1)]
    limit: Annotated[int, Field(ge=1, le=10)] = 2
    tagging: TaggingConfig
    estimate_signature: str | None = None


class ArxivUpdate(Record):
    """Explicit arXiv identifier used for matching on the next scrape attempt."""

    arxiv_id: Text


class PaperFilterRequest(Record):
    """Current Papers-view filters used for arXiv metadata or PDF-link collection.

    Domain lists require every chosen tag. An empty list/string or an omitted domain
    means all domains; a single string retains the original API contract.
    """

    search: Annotated[str, Field(max_length=500)] = ""
    status: Literal["all", "pending", "failed", "scraped", "tagged"] = "all"
    domain: list[DomainId] | DomainId | Literal[""] | None = None
