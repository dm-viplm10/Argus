"""CLI evaluation runner — evaluates research output against ground truth."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from src.evaluation.evaluator import run_evaluation
from src.utils.logging import setup_logging


async def main() -> None:
    setup_logging(log_level="INFO", log_format="console")

    gt_dir = Path(__file__).parent.parent / "src" / "evaluation" / "ground_truth"
    gt_files = list(gt_dir.glob("*.json"))

    if not gt_files:
        print("No ground truth files found.")
        sys.exit(1)

    print(f"Found {len(gt_files)} ground truth files.\n")

    # Without a completed research state, we run a dry evaluation
    # showing what metrics would be computed.
    for gt_file in gt_files:
        gt = json.loads(gt_file.read_text())
        print(f"--- {gt.get('target', gt_file.stem)} ---")
        print(f"  Expected facts: {len(gt.get('expected_facts', []))}")
        print(f"  Expected entities: {len(gt.get('expected_entities', []))}")
        print(f"  Expected risk flags: {len(gt.get('expected_risk_flags', []))}")

        # Dry run with empty state to show metric framework
        empty_state: dict = {
            "verified_facts": [],
            "entities": [],
            "relationships": [],
            "risk_flags": [],
            "search_queries_executed": [],
        }

        metrics, summary = await run_evaluation(empty_state, gt_file.name)
        print(summary)
        print()


if __name__ == "__main__":
    asyncio.run(main())
