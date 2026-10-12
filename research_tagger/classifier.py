"""One Jev call per paper, with one independent Noul question per domain."""

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from .models import Domain, Paper, Probability, TaggedPaper, TaggingPrompt, Text, validate_domains

API_URL = "https://api.typesafe.ai/v1/systemone"


class NoulAnswer(BaseModel):
    """The documented Jev yes/no answer; reject invalid types and probabilities."""

    model_config = ConfigDict(strict=True)
    type: Literal["noul"]
    noul: Probability


class JevResponse(BaseModel):
    """Required classification fields; API usage metadata is not persisted."""

    model_config = ConfigDict(strict=True)
    model: Text
    answers: dict[str, NoulAnswer]


def domain_questions(domains: list[Domain], fields: list[str], prompt: TaggingPrompt | None = None) -> dict:
    """Render the shared question for each domain and selected fields, without criteria.

    The web app supplies its saved prompt; omitted prompts use the original CLI
    defaults. Domain names and definitions remain separate instruction fields.
    """
    validate_domains(domains)
    content = " and ".join(fields)
    if prompt is None:
        prompt = TaggingPrompt()
    return {
        domain.id: {
            "type": "noul",
            "instructions": {
                "question": prompt.question.format(fields=content),
                "domain": domain.label,
                "definition": domain.description,
            },
        }
        for domain in domains
    }


def classify_paper(
    client: httpx.Client,
    paper: Paper,
    domains: list[Domain],
    api_key: str,
    model: str = "jev-latest",
) -> TaggedPaper:
    """Send only title and abstract as state, returning validated probabilities.

    Domain IDs are response keys; labels and definitions go in the instructions
    because Jev does not see the keys. An empty key, HTTP error, or response
    mismatch raises immediately. The supplied client is owned by the caller.
    """
    validate_domains(domains)
    if not api_key.strip():
        raise ValueError("TYPESAFE_API_KEY must be set and nonempty.")
    if not model.strip():
        raise ValueError("The Jev model name must be nonempty.")
    questions = domain_questions(domains, ["title", "abstract"])
    response = client.post(
        API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "state": {"title": paper.title, "abstract": paper.abstract},
            "questions": questions,
        },
    )
    response.raise_for_status()
    result = JevResponse.model_validate(response.json())
    if set(result.answers) != set(questions):
        raise ValueError(f"Jev returned mismatched domain IDs for {paper.source_url}.")
    return TaggedPaper(
        **paper.model_dump(),
        probabilities={key: answer.noul for key, answer in result.answers.items()},
        model=result.model,
    )
