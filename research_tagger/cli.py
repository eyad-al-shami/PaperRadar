"""Scrape, classify, and display saved paper collections from the terminal."""

import argparse
import os
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel, TypeAdapter

from .classifier import classify_paper
from .models import Corpus, Domain, TaggedCorpus, TaggedPaper, validate_domains
from .scraper import scrape_eccv


def save(path: Path, record: BaseModel) -> None:
    """Create a UTF-8 JSON output file; never overwrite an existing result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(record.model_dump_json(indent=2) + "\n")


def show_results(run: TaggedCorpus, domain_id: str | None = None) -> None:
    """Print domain groups ranked by probability, then papers with no tags.

    A tag is assigned when its yes probability is >= the saved threshold.
    A paper may appear in multiple groups; no match does not imply irrelevance.
    """
    if domain_id is not None and domain_id not in {domain.id for domain in run.domains}:
        raise ValueError(f"Unknown domain ID: {domain_id}")
    print(f"{run.conference} {run.year} | {len(run.papers)} papers | threshold {run.threshold:g}")

    def show_paper(paper: TaggedPaper) -> None:
        print(f"\n  {paper.title}\n  {paper.source_url}\n  {paper.abstract}")
        print("  " + "; ".join(
            f"{domain.label}: {paper.probabilities[domain.id]:.1%}"
            for domain in run.domains
        ))

    for domain in run.domains:
        if domain_id is not None and domain.id != domain_id:
            continue
        matched = sorted(
            (paper for paper in run.papers if paper.probabilities[domain.id] >= run.threshold),
            key=lambda paper: paper.probabilities[domain.id], reverse=True,
        )
        print(f"\n{domain.label} ({len(matched)})")
        for paper in matched:
            show_paper(paper)
    if domain_id is None:
        untagged = [paper for paper in run.papers if max(paper.probabilities.values()) < run.threshold]
        print(f"\nNo domain above threshold ({len(untagged)})")
        for paper in untagged:
            show_paper(paper)


def main(argv: list[str] | None = None) -> int:
    """Run a subcommand; return zero on success and one on a reported failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scrape = commands.add_parser("scrape", help="Fetch an ECCV edition from its official virtual site")
    scrape.add_argument("--year", type=int, required=True)
    scrape.add_argument("--output", type=Path, required=True)
    scrape.add_argument("--limit", type=int, help="Explicit sample size; omit for all papers")
    classify = commands.add_parser("classify", help="Classify a saved corpus with Jev")
    classify.add_argument("--papers", type=Path, required=True)
    classify.add_argument("--domains", type=Path, required=True)
    classify.add_argument("--output", type=Path, required=True)
    classify.add_argument("--threshold", type=float, default=0.7)
    classify.add_argument("--model", default="jev-latest")
    show = commands.add_parser("show", help="View results without calling Jev again")
    show.add_argument("results", type=Path)
    show.add_argument("--domain", help="Show one configured domain ID")
    args = parser.parse_args(argv)

    try:
        if args.command == "show":
            show_results(TaggedCorpus.model_validate_json(args.results.read_text(encoding="utf-8")), args.domain)
            return 0
        if args.output.exists():
            raise ValueError(f"Output already exists: {args.output}. Choose a new path.")
        if args.command == "scrape":
            if args.year < 2000 or args.year % 2:
                raise ValueError("ECCV year must be an even year from 2000 onward.")
            papers = scrape_eccv(args.year, args.limit)
            for index, paper in enumerate(papers, 1):
                print(f"Scraped {index}: {paper.title}", file=sys.stderr)
            save(args.output, Corpus(year=args.year, papers=papers))
            print(f"Saved {len(papers)} papers to {args.output}")
        else:
            if not 0 <= args.threshold <= 1:
                raise ValueError("Threshold must be a finite number between 0 and 1.")
            api_key = os.environ.get("TYPESAFE_API_KEY", "")
            if not api_key.strip():
                raise ValueError("Set TYPESAFE_API_KEY in your local environment before classifying.")
            corpus = Corpus.model_validate_json(args.papers.read_text(encoding="utf-8"))
            domains = validate_domains(TypeAdapter(list[Domain]).validate_json(args.domains.read_text(encoding="utf-8")))
            tagged = []
            with httpx.Client(timeout=60) as client:
                for index, paper in enumerate(corpus.papers, 1):
                    tagged.append(classify_paper(client, paper, domains, api_key, args.model))
                    print(f"Classified {index}/{len(corpus.papers)}", file=sys.stderr)
            run = TaggedCorpus(year=corpus.year, domains=domains, threshold=args.threshold, papers=tagged)
            save(args.output, run)
            show_results(run)
            print(f"\nSaved results to {args.output}")
        return 0
    except (OSError, ValueError, httpx.HTTPError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
