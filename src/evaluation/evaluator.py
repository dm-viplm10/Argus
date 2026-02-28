"""Evaluation runner — compares research findings to ground truth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.api.v1.schemas.evaluation import EvaluationMetrics
from src.evaluation.metrics import compute_metrics
from src.utils.logging import get_logger

logger = get_logger(__name__)

GROUND_TRUTH_DIR = Path(__file__).parent / "ground_truth"


async def run_evaluation(
    state: dict[str, Any],
    ground_truth_file: str,
) -> tuple[EvaluationMetrics, str]:
    """Run evaluation comparing research state to a ground truth file.

    Returns:
        Tuple of (metrics, summary_text).
    """
    gt_path = GROUND_TRUTH_DIR / ground_truth_file
    if not gt_path.exists():
        raise FileNotFoundError(f"Ground truth not found: {gt_path}")

    ground_truth = json.loads(gt_path.read_text())
    metrics = compute_metrics(state, ground_truth)

    summary_lines = [
        f"Evaluation for target: {ground_truth.get('target', 'unknown')}",
        f"  Fact Recall:       {metrics.fact_recall:.1%}",
        f"  Fact Precision:    {metrics.fact_precision:.1%}",
        f"  Entity Coverage:   {metrics.entity_coverage:.1%}",
        f"  Rel. Accuracy:     {metrics.relationship_accuracy:.1%}",
        f"  Risk Detection:    {metrics.risk_detection_rate:.1%}",
        f"  Depth Score:       {metrics.depth_score:.1%}",
        f"  Efficiency:        {metrics.efficiency:.2f} findings/query",
        f"  Source Quality:    {metrics.source_quality:.1%}",
    ]
    summary = "\n".join(summary_lines)
    logger.info("evaluation_complete", target=ground_truth.get("target"), metrics=metrics.model_dump())

    return metrics, summary
