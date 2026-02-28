"""Evaluation framework runner service with LangSmith integration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langsmith import traceable

from src.evaluation.evaluator import run_evaluation
from src.evaluation.metrics import compute_metrics
from src.utils.logging import get_logger

logger = get_logger(__name__)


class EvaluationService:
    """Orchestrates evaluation runs and LangSmith dataset management."""

    @traceable(run_type="chain", name="evaluation_run")
    async def evaluate(
        self,
        state: dict[str, Any],
        ground_truth_file: str,
    ) -> dict[str, Any]:
        metrics, summary = await run_evaluation(state, ground_truth_file)
        return {"metrics": metrics.model_dump(), "summary": summary}

    @staticmethod
    async def upload_ground_truth_to_langsmith(
        ground_truth_file: str,
        dataset_name: str = "research-agent-eval",
    ) -> None:
        """Upload ground truth as a LangSmith dataset for reproducible evaluation."""
        try:
            from langsmith import Client

            client = Client()

            gt_path = Path(__file__).parent.parent / "evaluation" / "ground_truth" / ground_truth_file
            ground_truth = json.loads(gt_path.read_text())

            examples = [
                {
                    "inputs": {
                        "target_name": ground_truth["target"],
                        "target_context": ground_truth["context"],
                    },
                    "outputs": {
                        "expected_facts": ground_truth["expected_facts"],
                        "expected_entities": ground_truth["expected_entities"],
                        "expected_risk_flags": ground_truth.get("expected_risk_flags", []),
                    },
                }
            ]

            client.create_examples(dataset_name=dataset_name, examples=examples)
            logger.info("langsmith_dataset_uploaded", dataset=dataset_name, file=ground_truth_file)
        except Exception as exc:
            logger.warning("langsmith_upload_failed", error=str(exc))
