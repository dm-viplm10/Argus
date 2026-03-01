"""Call the evaluate API with inline state (e.g. example Timothy Overturf state).

Usage:
  # With state from a JSON file (e.g. saved research state or example state)
  python scripts/run_evaluate_api.py path/to/state.json

  # With default example state from stdin
  cat example_state.json | python scripts/run_evaluate_api.py -

  # Ground truth file (default: timothy_overturf.json)
  python scripts/run_evaluate_api.py state.json --ground-truth timothy_overturf.json

Requires the API server to be running (e.g. uvicorn src.main:app) and OPENROUTER_API_KEY
set for LLM-as-judge scoring.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evaluation via API with inline state")
    parser.add_argument(
        "state_file",
        type=str,
        help="Path to state JSON file, or '-' for stdin",
    )
    parser.add_argument(
        "--ground-truth",
        default="timothy_overturf.json",
        help="Ground truth filename (default: timothy_overturf.json)",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000/api/v1/evaluate",
        help="Evaluate endpoint URL",
    )
    parser.add_argument(
        "--no-llm-judge",
        action="store_true",
        help="Disable LLM-as-judge; use rule-based metrics only",
    )
    args = parser.parse_args()

    if args.state_file == "-":
        state = json.load(sys.stdin)
    else:
        path = Path(args.state_file)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        state = json.loads(path.read_text())

    payload = {
        "research_id": state.get("research_id", ""),
        "ground_truth_file": args.ground_truth,
        "state": state,
        "use_llm_judge": not args.no_llm_judge,
    }

    with httpx.Client(timeout=300.0) as client:
        try:
            resp = client.post(args.url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            print(f"HTTP error: {e.response.status_code}", file=sys.stderr)
            print(e.response.text, file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Request failed: {e}", file=sys.stderr)
            sys.exit(1)

    data = resp.json()
    print("=== Evaluation Summary ===")
    print(data.get("summary", ""))
    print()
    print("=== Metrics ===")
    metrics = data.get("metrics", {})
    for k, v in metrics.items():
        if k == "metric_reasoning":
            continue
        if isinstance(v, (int, float)):
            print(f"  {k}: {v}")
    if data.get("evaluation_report"):
        print()
        print("=== Evaluation Report (LLM judge) ===")
        print(data["evaluation_report"])
    print()
    print(f"evaluation_id: {data.get('evaluation_id', '')}")


if __name__ == "__main__":
    main()
