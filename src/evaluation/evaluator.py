"""Evaluation runner — compares research findings to ground truth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.api.v1.schemas.evaluation import EvaluationMetrics
from src.evaluation.llm_judge import run_llm_judge
from src.evaluation.metrics import compute_metrics, fact_precision_from_state
from src.utils.logging import get_logger

logger = get_logger(__name__)

GROUND_TRUTH_DIR = Path(__file__).parent / "ground_truth"

METRIC_ORDER = [
    "network_fidelity",
    "risk_detection_rate",
    "depth_score",
    "efficiency",
    "source_quality",
]


async def run_evaluation(
    state: dict[str, Any],
    ground_truth_file: str,
    use_llm_judge: bool = True,
    registry: Any = None,
) -> tuple[EvaluationMetrics, str, str]:
    """Run evaluation comparing research state to a ground truth file.

    When use_llm_judge is True and registry is provided, each metric is scored
    by an LLM (GPT-4.1) in sequence. Otherwise rule-based compute_metrics is used.

    Returns:
        Tuple of (metrics, summary_text, evaluation_report).
    """
    gt_path = GROUND_TRUTH_DIR / ground_truth_file
    if not gt_path.exists():
        raise FileNotFoundError(f"Ground truth not found: {gt_path}")

    ground_truth = json.loads(gt_path.read_text())
    target_name = ground_truth.get("target", "unknown")

    if use_llm_judge and registry is not None:
        try:
            llm = registry.get_model("evaluator")
        except KeyError:
            llm = None
        if llm is not None:
            judge_results = await run_llm_judge(
                state, ground_truth, llm,
                metrics_order=METRIC_ORDER,
                ground_truth_file=ground_truth_file,
            )
            fact_precision = fact_precision_from_state(state)
            metric_reasoning = {k: v.get("reasoning", "") for k, v in judge_results.items()}
            metric_reasoning["fact_precision"] = (
                "Computed from state: verified_facts / (verified_facts + unverified_claims)."
            )
            metrics = EvaluationMetrics(
                fact_precision=round(fact_precision, 3),
                network_fidelity=judge_results.get("network_fidelity", {}).get("score", 0.0),
                risk_detection_rate=judge_results.get("risk_detection_rate", {}).get("score", 0.0),
                depth_score=judge_results.get("depth_score", {}).get("score", 0.0),
                efficiency=judge_results.get("efficiency", {}).get("score", 0.0),
                source_quality=judge_results.get("source_quality", {}).get("score", 0.0),
                metric_reasoning=metric_reasoning,
            )
            summary_lines = [
                f"Evaluation for target: {target_name} (LLM-as-judge)",
                f"  Fact Precision:    {metrics.fact_precision:.1%}",
                f"  Network Fidelity:  {metrics.network_fidelity:.1%}",
                f"  Risk Detection:    {metrics.risk_detection_rate:.1%}",
                f"  Depth Score:       {metrics.depth_score:.1%}",
                f"  Efficiency:        {metrics.efficiency:.1%}",
                f"  Source Quality:    {metrics.source_quality:.1%}",
            ]
            summary = "\n".join(summary_lines)
            report_lines = [
                f"# Evaluation Report: {target_name}",
                "",
                "## Source of truth",
                f"- **Ground truth (expected):** Curated evaluation file `{ground_truth_file}` — "
                "expected_facts, expected_entities, expected_relationships, expected_risk_flags.",
                "- **Research output (actual):** Pipeline state for this run (extracted/verified facts, "
                "entities, relationships, risk_flags from Redis eval state or inline state).",
                "- **Critical/important weighting:** risk_detection_rate and depth_score rate by critical and important verifiable information; severity (critical > high > moderate > low) and verifiability matter more than raw count.",
                "- **Network fidelity:** entity and relationship coverage merged; score by semantic similarity and importance of discoveries, not by count.",
                "- **Fact precision:** computed from state only as verified_facts / (verified_facts + unverified_claims); not LLM-scored.",
                "",
                "## Summary",
                summary,
                "",
                "## Per-metric reasoning (LLM judge)",
            ]
            report_order = ["fact_precision"] + METRIC_ORDER
            for name in report_order:
                reasoning = metrics.metric_reasoning.get(name, "")
                report_lines.append(f"### {name}")
                report_lines.append(f"**Score:** {getattr(metrics, name, 0):.1%}")
                report_lines.append(f"**Reasoning:** {reasoning}")
                report_lines.append("")
            evaluation_report = "\n".join(report_lines)
            logger.info(
                "evaluation_complete",
                target=target_name,
                method="llm_judge",
                metrics=metrics.model_dump(),
            )
            return metrics, summary, evaluation_report

    # Fallback: rule-based metrics
    metrics = compute_metrics(state, ground_truth)
    summary_lines = [
        f"Evaluation for target: {target_name}",
        f"  Fact Precision:    {metrics.fact_precision:.1%}",
        f"  Network Fidelity:  {metrics.network_fidelity:.1%}",
        f"  Risk Detection:    {metrics.risk_detection_rate:.1%}",
        f"  Depth Score:       {metrics.depth_score:.1%}",
        f"  Efficiency:        {metrics.efficiency:.2f} findings/query",
        f"  Source Quality:    {metrics.source_quality:.1%}",
    ]
    summary = "\n".join(summary_lines)
    evaluation_report = summary
    logger.info(
        "evaluation_complete",
        target=target_name,
        method="rule_based",
        metrics=metrics.model_dump(),
    )
    return metrics, summary, evaluation_report
