"""Transparent token/cost estimates, exact request identities, and coverage statistics."""

import hashlib
import json
import math

from .classifier import domain_questions
from .web_models import ConferenceConfig, TaggingConfig


def canonical(value: object) -> str:
    """Stable JSON encoding used for request identities and estimation."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def signature(value: object) -> str:
    """Hash exact selected content, model, and rubrics to prevent stale tag reuse."""
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def ready(paper: dict, config: ConferenceConfig) -> bool:
    """A paper is complete only if every currently selected field has content."""
    return all(bool(paper[field]) for field in config.fields)


def needs_scrape(paper: dict, config: ConferenceConfig) -> bool:
    """Collect mandatory title/abstract/PDF metadata plus any selected introduction.

    Collection completeness is independent of the text fields selected for Jev.
    A missing PDF URL never makes otherwise usable text ineligible for tagging.
    """
    return (not paper["title"] or not paper["abstract"] or not paper.get("pdf_url")
            or "introduction" in config.fields and not paper["introduction"])


def payload_for(paper: dict, config: ConferenceConfig, tagging: TaggingConfig) -> dict:
    """Return the exact Jev payload; missing selected fields are a strict error."""
    if not ready(paper, config):
        raise ValueError("Paper is missing one or more selected fields.")
    fields = [field for field in ("title", "abstract", "introduction") if field in config.fields]
    return {
        "model": tagging.model,
        "state": {field: paper[field] for field in fields},
        "questions": domain_questions(tagging.domains, fields, tagging.prompt),
    }


def estimate_payload(payload: dict) -> dict:
    """Estimate using four characters/token, with a three-to-five sensitivity range.

    This is not the Jev tokenizer or a guaranteed bound. Both paper content and
    per-paper questions/wrapping are included; actual usage comes from the API.
    """
    text = canonical(payload)
    paper_chars = len(canonical(payload["state"]))
    return {
        "paper_tokens": math.ceil(paper_chars / 4),
        "instruction_tokens": math.ceil(len(text) / 4) - math.ceil(paper_chars / 4),
        "input_tokens": math.ceil(len(text) / 4),
        "low_tokens": math.ceil(len(text) / 5),
        "high_tokens": math.ceil(len(text) / 3),
    }


def summarize(papers: list[dict], results: list[dict], config: ConferenceConfig, tagging: TaggingConfig) -> tuple[dict, list[dict]]:
    """Compute disjoint coverage counts and pending tagging cost from stored content.

    Attach current probabilities only when the exact request signature matches.
    An incomplete paper is never included in the pending classification estimate.
    """
    indexed = {(row["paper_id"], row["signature"]): row for row in results}
    stats = {"total": len(papers), "scraped": 0, "not_scraped": 0, "pending": 0, "failed": 0,
             "abstracts": 0, "introductions": 0, "tagged": 0, "ready_to_tag": 0,
             "missing_pdf": 0, "collection_pending": 0,
             "input_tokens": 0, "paper_tokens": 0, "instruction_tokens": 0,
             "low_tokens": 0, "high_tokens": 0, "context_blocked": 0,
             "domain_counts": {domain.id: 0 for domain in tagging.domains}}
    candidates = []
    enriched = []
    for original in papers:
        paper = dict(original)
        paper["probabilities"] = None
        paper["estimated_tokens"] = None
        paper["context_error"] = None
        stats["abstracts"] += bool(paper["abstract"])
        stats["introductions"] += bool(paper["introduction"])
        stats["missing_pdf"] += not bool(paper.get("pdf_url"))
        stats["collection_pending"] += needs_scrape(paper, config)
        if ready(paper, config):
            stats["scraped"] += 1
            paper["status"] = "scraped"
            payload = payload_for(paper, config, tagging)
            request_signature = signature(payload)
            estimate = estimate_payload(payload)
            paper["estimated_tokens"] = estimate["input_tokens"]
            stored = indexed.get((paper["id"], request_signature))
            longest = max(len(canonical(q)) for q in payload["questions"].values())
            if stored:
                paper["probabilities"] = json.loads(stored["probabilities"])
                paper["status"] = "tagged"
                stats["tagged"] += 1
                for domain, probability in paper["probabilities"].items():
                    stats["domain_counts"][domain] += probability >= tagging.threshold
            elif estimate["high_tokens"] > 64000 or (len(canonical(payload["state"])) + longest) / 3 > 32000:
                paper["context_error"] = "Estimated request may exceed the model's context limits. Select fewer fields or tags."
                stats["context_blocked"] += 1
            else:
                stats["ready_to_tag"] += 1
                candidates.append((paper["id"], request_signature))
                for name in ("input_tokens", "paper_tokens", "instruction_tokens", "low_tokens", "high_tokens"):
                    stats[name] += estimate[name]
        else:
            stats["not_scraped"] += 1
            has_error = any(paper.get(f"{field}_error") for field in config.fields if not paper[field])
            paper["status"] = "failed" if has_error else "pending"
            stats[paper["status"]] += 1
        enriched.append(paper)
    stats["estimated_cost"] = stats["input_tokens"] * tagging.price_per_million / 1_000_000
    stats["low_cost"] = stats["low_tokens"] * tagging.price_per_million / 1_000_000
    stats["high_cost"] = stats["high_tokens"] * tagging.price_per_million / 1_000_000
    stats["actual_input_tokens"] = sum(row["input_tokens"] for row in results)
    stats["actual_cost"] = sum(row["cost_usd"] for row in results)
    stats["estimate_signature"] = signature({"candidates": candidates, "price": tagging.price_per_million})
    return stats, enriched
