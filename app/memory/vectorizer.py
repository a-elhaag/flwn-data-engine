"""Turn supplied text into storable, embedded memories.

Single seam between raw input and vectors: compression, importance, and embedding live
here, so the write path behaves the same whether the steward calls it inline or a
background worker does.
"""

from dataclasses import dataclass

from app.clients import inference
from app.memory import prompts


@dataclass
class Prepared:
    raw_text: str
    fact: str
    importance: int


def prepare(text: str) -> Prepared:
    reply = inference.chat("memory_steward.compress", prompts.compress_prompt(text))
    fact, importance = prompts.parse_compress(reply)
    return Prepared(raw_text=text, fact=fact or text.strip(), importance=importance)


def embed_one(fact: str) -> list[float]:
    return inference.embed(fact)


def embed_many(facts: list[str]) -> list[list[float]]:
    return inference.embed_many(facts)
