"""The Decision Ledger judge: does a proposed action go against decisions the team already made?

The model's answer is untrusted input. It must be strict JSON; anything else, or any decision it
fails to mention, counts as no conflict, so a bad reply can never raise a false alarm or crash a
caller. Decisions and the proposal are wrapped as data so text inside them cannot give orders.
"""

from app.memory.prompts import DATA_RULE, extract_json, wrap


def judge_prompt(proposal: str, decisions: list[tuple[str, str]]) -> str:
    blocks = "\n".join(wrap("decision", text, ref=ref) for ref, text in decisions)
    return (
        "A team has recorded decisions. Check whether a proposed action goes against any of them.\n"
        "For each decision, answer conflict=true only if carrying out the proposal would contradict "
        "or undo that decision. Related but compatible work is conflict=false. When unsure, false.\n"
        'Reply with only JSON: {"verdicts": [{"ref": "<ref>", "conflict": true|false, '
        '"explanation": "<one sentence>"}]}.\n'
        f"{DATA_RULE}\n\n{wrap('proposal', proposal)}\n{blocks}"
    )


def parse_verdicts(raw: str, refs: list[str]) -> dict[str, tuple[bool, str]]:
    """ref -> (conflict, explanation). Missing or malformed entries default to no conflict."""
    verdicts = {ref: (False, "") for ref in refs}
    data = extract_json(raw)
    if not data or not isinstance(data.get("verdicts"), list):
        return verdicts
    for item in data["verdicts"]:
        if (
            isinstance(item, dict)
            and item.get("ref") in verdicts
            and isinstance(item.get("conflict"), bool)
        ):
            explanation = item.get("explanation")
            verdicts[item["ref"]] = (
                item["conflict"],
                explanation.strip() if isinstance(explanation, str) else "",
            )
    return verdicts
