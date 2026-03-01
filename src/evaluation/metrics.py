"""Evaluation metrics: precision, recall, F1, coverage, depth scoring."""

from __future__ import annotations

from src.api.v1.schemas.evaluation import EvaluationMetrics


def fact_precision_from_state(state: dict) -> float:
    """Fact precision from state only: verified_facts / (verified_facts + unverified_claims)."""
    verified = state.get("verified_facts") or []
    unverified = state.get("unverified_claims") or []
    total = len(verified) + len(unverified)
    if total == 0:
        return 0.0
    return len(verified) / total


def compute_metrics(
    state: dict,
    ground_truth: dict,
) -> EvaluationMetrics:
    """Compute evaluation metrics by comparing research output to ground truth.

    Uses fuzzy matching for fact comparison. For fact-based metrics, uses
    verified_facts when present and non-empty, otherwise extracted_facts.
    """
    expected_facts = ground_truth.get("expected_facts", [])
    expected_entities = ground_truth.get("expected_entities", [])
    expected_relationships = ground_truth.get("expected_relationships", [])
    expected_risks = ground_truth.get("expected_risk_flags", [])

    verified = state.get("verified_facts") or []
    fact_list = verified if verified else state.get("extracted_facts", [])
    found_entities = state.get("entities", [])
    found_relationships = state.get("relationships", [])
    found_risks = state.get("risk_flags", [])

    # Fact precision: from state only — verified / (verified + unverified)
    fact_precision = fact_precision_from_state(state)

    # Entity + relationship → single network fidelity (rule-based: average of coverage and relationship match)
    entity_cov = _entity_coverage(expected_entities, found_entities)
    rel_acc = _relationship_accuracy(expected_relationships, found_relationships)
    network_fidelity = (entity_cov + rel_acc) / 2 if (expected_entities or expected_relationships) else 0.0

    # Risk detection rate
    risk_detection = _risk_detection(expected_risks, found_risks)

    # Depth score: hard facts found / total hard facts
    depth_score = _depth_score(expected_facts, fact_list)

    # Efficiency: findings per search query
    searches = len(state.get("search_queries_executed", []))
    total_findings = len(fact_list) + len(found_entities) + len(found_risks)
    efficiency = total_findings / max(searches, 1)

    # Source quality: mean confidence of facts
    confidences = [f.get("final_confidence", f.get("confidence", 0.5)) for f in fact_list]
    source_quality = sum(confidences) / max(len(confidences), 1)

    return EvaluationMetrics(
        fact_precision=round(fact_precision, 3),
        network_fidelity=round(network_fidelity, 3),
        risk_detection_rate=round(risk_detection, 3),
        depth_score=round(depth_score, 3),
        efficiency=round(efficiency, 3),
        source_quality=round(source_quality, 3),
    )


def _entity_coverage(expected: list[dict], found: list[dict]) -> float:
    if not expected:
        return 1.0
    found_names = {e.get("name", "").lower() for e in found}
    hits = sum(1 for e in expected if e["name"].lower() in found_names)
    return hits / len(expected)


def _relationship_accuracy(expected: list[dict], found: list[dict]) -> float:
    if not expected:
        return 1.0
    hits = 0
    for er in expected:
        for fr in found:
            src_match = er["source"].lower() in str(fr.get("source_entity", "")).lower()
            tgt_match = er["target"].lower() in str(fr.get("target_entity", "")).lower()
            if src_match and tgt_match:
                hits += 1
                break
    return hits / len(expected)


def _risk_detection(expected: list[dict], found: list[dict]) -> float:
    if not expected:
        return 1.0
    hits = 0
    for er in expected:
        cat = er.get("category", "").lower()
        for fr in found:
            if fr.get("category", "").lower() == cat:
                hits += 1
                break
    return hits / len(expected)


def _depth_score(expected_facts: list[dict], found: list[dict]) -> float:
    hard = [f for f in expected_facts if f.get("difficulty") == "hard"]
    if not hard:
        return 1.0
    hits = sum(1 for h in hard if _fuzzy_match(h["fact"], found))
    return hits / len(hard)


def _fuzzy_match(expected: str, candidates: list[dict], field: str = "fact") -> bool:
    """Check if any candidate contains the key terms of the expected string."""
    terms = expected.lower().split()
    key_terms = [t for t in terms if len(t) > 3]
    if not key_terms:
        key_terms = terms
    for c in candidates:
        text = str(c.get(field, "")).lower()
        if sum(1 for t in key_terms if t in text) >= max(len(key_terms) * 0.5, 1):
            return True
    return False
