"""Unit tests for the evaluation metrics."""

from __future__ import annotations

import pytest

from src.evaluation.metrics import compute_metrics


def test_compute_metrics_empty_state():
    state = {
        "verified_facts": [],
        "entities": [],
        "relationships": [],
        "risk_flags": [],
        "search_queries_executed": [],
    }
    gt = {
        "expected_facts": [{"fact": "Some fact", "difficulty": "easy"}],
        "expected_entities": [{"name": "Test", "type": "person"}],
        "expected_relationships": [],
        "expected_risk_flags": [],
    }
    metrics = compute_metrics(state, gt)
    assert metrics.fact_recall == 0.0
    assert metrics.entity_coverage == 0.0


def test_compute_metrics_perfect_match():
    state = {
        "verified_facts": [
            {"fact": "Timothy Overturf is CEO of Sisu Capital", "final_confidence": 0.9}
        ],
        "entities": [
            {"name": "Timothy Overturf", "type": "person"},
            {"name": "Sisu Capital", "type": "organization"},
        ],
        "relationships": [
            {"source_entity": "Timothy Overturf", "target_entity": "Sisu Capital", "relationship_type": "WORKS_AT"}
        ],
        "risk_flags": [
            {"category": "legal", "flag": "test"}
        ],
        "search_queries_executed": [{"query": "test"}],
    }
    gt = {
        "expected_facts": [
            {"fact": "CEO of Sisu Capital", "difficulty": "easy"}
        ],
        "expected_entities": [
            {"name": "Timothy Overturf", "type": "person"},
            {"name": "Sisu Capital", "type": "organization"},
        ],
        "expected_relationships": [
            {"source": "Timothy Overturf", "target": "Sisu Capital", "type": "WORKS_AT"}
        ],
        "expected_risk_flags": [
            {"category": "legal", "description": "test"}
        ],
    }
    metrics = compute_metrics(state, gt)
    assert metrics.fact_recall == 1.0
    assert metrics.entity_coverage == 1.0
    assert metrics.relationship_accuracy == 1.0
    assert metrics.risk_detection_rate == 1.0
    assert metrics.source_quality == 0.9
