"""LLM-as-judge evaluation: score each metric by comparing state to ground truth."""

from __future__ import annotations

import json
from typing import Any

from src.utils.logging import get_logger

logger = get_logger(__name__)

# Ground rules and scoring thresholds for each metric (used in prompts).
# Source of truth: "Ground truth" is always the curated evaluation file (e.g. timothy_overturf.json)
# with expected_facts, expected_entities, expected_relationships, expected_risk_flags.
# "Research output" is the pipeline state (extracted/verified facts, entities, relationships, risk_flags).

METRIC_GROUND_RULES = {
    "network_fidelity": {
        "description": "Semantic quality of the discovered entity-relationship network: how similar and how important the discovered entities and relationships are relative to the ground truth. Do NOT score by raw count.",
        "thresholds": "0.90+ = excellent, 0.70–0.89 = good, 0.50–0.69 = fair, below 0.50 = poor.",
        "compare": "expected_entities + expected_relationships (ground truth) vs entities + relationships (state). Evaluate (1) semantic similarity: same or equivalent entities (name variants, same person/org) and relationships (e.g. WORKS_AT vs FOUNDER_CEO_OWNER, OWNS vs OWNER); (2) importance: core entities (subject, key orgs, regulators, key people) and critical relationships (litigation, ownership, family, employment) matter more than peripheral ones. A small set of high-value, semantically correct discoveries can score better than many low-value or noisy ones.",
        "weighting": "Score 0–1 based on how well the research captured the important network structure. Prioritize: subject and direct affiliates, regulatory/legal links, key business relationships. Missing minor entities is less penalized than missing critical ones. Do not use a simple count-based fraction.",
    },
    "risk_detection_rate": {
        "description": "How well the research identified critical and important verifiable risks. Rate by whether the most important, verifiable risk aspects are covered — not by raw count.",
        "thresholds": "0.90+ = excellent, 0.70–0.89 = good, 0.50–0.69 = fair, below 0.50 = poor.",
        "compare": "expected_risk_flags (ground truth) vs risk_flags (state). Consider severity (critical > high > moderate > low) and verifiability. Critical, well-documented risks (e.g. SEC action, registration revoked, father's fraud/suspension, trading against client instructions) should carry much more weight than low-severity or speculative ones.",
        "weighting": "Weight by severity: critical=4, high=3, moderate=2, low=1. If the research captures the critical and important verifiable risks (even if it misses some moderate/low), score toward good or excellent. If it misses critical risks, score toward fair or poor. Score = weighted detection quality, not just count of matches.",
    },
    "depth_score": {
        "description": "How well the research captured critical and important verifiable information among hard-to-find facts. Rate by importance and verifiability of what was found — not by raw count of hard facts.",
        "thresholds": "0.90+ = excellent, 0.70–0.89 = good, 0.50–0.69 = fair, below 0.50 = poor.",
        "compare": "expected_facts with difficulty=hard (ground truth) vs extracted/verified facts (state). Among hard facts, prioritize: legally/regulatorily significant (SEC allegations, unsuitable products, crypto plan), verifiable from primary sources. If the research found the most critical, verifiable hard facts (e.g. SEC complaint details, inverse volatility products, bank stock / crypto plan), give a higher score even if some niche hard facts are missing.",
        "weighting": "Weight hard facts by how critical and verifiable they are. Critical verifiable hard facts (legal, financial, regulatory) count more than obscure or speculative hard facts. Score 0–1 based on coverage of important verifiable hard information.",
    },
    "efficiency": {
        "description": "Productivity of the research: findings per search query, with a reasonable expected range.",
        "thresholds": "2.0+ findings/query = excellent, 1.0–1.99 = good, 0.5–0.99 = fair, below 0.5 = poor. Cap score at 1.0.",
        "compare": "total_findings (facts + entities + risk_flags) divided by number of search_queries_executed. Normalize to 0–1 using the thresholds.",
        "weighting": None,
    },
    "source_quality": {
        "description": "Average confidence/reliability of the reported facts (state-reported confidence scores).",
        "thresholds": "0.80+ = excellent, 0.60–0.79 = good, 0.40–0.59 = fair, below 0.40 = poor.",
        "compare": "Use confidence/final_confidence from extracted_facts or verified_facts. If no facts, score 0. No ground truth comparison; this is intrinsic to the research output.",
        "weighting": None,
    },
}


def _get_facts_for_comparison(state: dict[str, Any]) -> list[dict]:
    """Use verified_facts if present and non-empty, else extracted_facts."""
    verified = state.get("verified_facts") or []
    if verified:
        return verified
    return state.get("extracted_facts") or []


def _build_metric_prompt(
    metric_name: str,
    state_slice: Any,
    ground_truth_slice: Any,
    rule: dict,
    ground_truth_file: str = "",
) -> str:
    source_note = (
        "Source of truth: Ground truth (expected) = curated evaluation file "
        f"({ground_truth_file or 'e.g. timothy_overturf.json'}) with expected_facts, "
        "expected_entities, expected_relationships, expected_risk_flags. "
        "Research output (actual) = OSINT pipeline state for this research run.\n\n"
    )
    weighting_block = ""
    if rule.get("weighting"):
        weighting_block = f"**Scoring guidance (required):** {rule['weighting']}\n\n"
    return f"""You are an evaluation judge for an OSINT research system. Score a single metric by comparing the research output to the ground truth.

{source_note}**Metric:** {metric_name}
**Definition:** {rule["description"]}
**Scoring thresholds:** {rule["thresholds"]}
**What to compare:** {rule["compare"]}
{weighting_block}**Ground truth (expected) data:**
```json
{json.dumps(ground_truth_slice, indent=2)[:8000]}
```

**Research output (actual) data:**
```json
{json.dumps(state_slice, indent=2)[:8000]}
```

Output a JSON object with exactly two keys:
- "score": a number between 0.0 and 1.0
- "reasoning": a short explanation (1-3 sentences) of how you applied the thresholds and what you counted (include weighted vs unweighted if applicable)

Output only the JSON object, no other text."""


async def score_metric(
    metric_name: str,
    state: dict[str, Any],
    ground_truth: dict[str, Any],
    llm: Any,
    ground_truth_file: str = "",
) -> tuple[float, str]:
    """Call the LLM to score one metric. Returns (score, reasoning)."""
    rule = METRIC_GROUND_RULES.get(metric_name)
    if not rule:
        return 0.0, f"Unknown metric: {metric_name}"

    # Build state and ground truth slices per metric
    if metric_name == "depth_score":
        facts = _get_facts_for_comparison(state)
        expected_facts = [f for f in ground_truth.get("expected_facts", []) if f.get("difficulty") == "hard"]
        state_slice = facts
        gt_slice = expected_facts
    elif metric_name == "network_fidelity":
        state_slice = {
            "entities": state.get("entities", []),
            "relationships": state.get("relationships", []),
        }
        gt_slice = {
            "expected_entities": ground_truth.get("expected_entities", []),
            "expected_relationships": ground_truth.get("expected_relationships", []),
        }
    elif metric_name == "risk_detection_rate":
        state_slice = state.get("risk_flags", [])
        gt_slice = ground_truth.get("expected_risk_flags", [])
    elif metric_name == "efficiency":
        queries = state.get("search_queries_executed", [])
        total = (
            len(_get_facts_for_comparison(state))
            + len(state.get("entities", []))
            + len(state.get("risk_flags", []))
        )
        state_slice = {
            "search_queries_count": len(queries),
            "total_findings": total,
            "findings_per_query": total / max(len(queries), 1),
        }
        gt_slice = {}
    elif metric_name == "source_quality":
        facts = _get_facts_for_comparison(state)
        confidences = [f.get("final_confidence", f.get("confidence", 0.5)) for f in facts]
        state_slice = {
            "fact_count": len(facts),
            "confidence_scores": confidences,
            "mean_confidence": sum(confidences) / max(len(confidences), 1),
        }
        gt_slice = {}
    else:
        return 0.0, "Unsupported metric"

    prompt = _build_metric_prompt(metric_name, state_slice, gt_slice, rule, ground_truth_file)

    try:
        from langchain_core.messages import HumanMessage

        messages = [HumanMessage(content=prompt)]
        response = await llm.ainvoke(messages)
        text = response.content if hasattr(response, "content") else str(response)
        text = text.strip()
        # Strip markdown code block if present
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        data = json.loads(text)
        score = float(data.get("score", 0.0))
        score = max(0.0, min(1.0, score))
        reasoning = str(data.get("reasoning", ""))[:2000]
        return score, reasoning
    except Exception as e:
        logger.warning("llm_judge_parse_error", metric=metric_name, error=str(e))
        return 0.0, f"Scoring failed: {e}"


async def run_llm_judge(
    state: dict[str, Any],
    ground_truth: dict[str, Any],
    llm: Any,
    metrics_order: list[str] | None = None,
    ground_truth_file: str = "",
) -> dict[str, dict[str, Any]]:
    """Run LLM-as-judge for each metric in sequence. Returns dict of metric -> {score, reasoning}."""
    order = metrics_order or list(METRIC_GROUND_RULES.keys())
    results: dict[str, dict[str, Any]] = {}
    for metric_name in order:
        score, reasoning = await score_metric(
            metric_name, state, ground_truth, llm, ground_truth_file
        )
        results[metric_name] = {"score": round(score, 3), "reasoning": reasoning}
    return results
