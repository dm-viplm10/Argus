# Argus UI

Streamlit UI for the Argus backend. Kept in a folder separate from `src`.

## Run

From the repo root, with the backend running (e.g. `make up`):

```bash
pip install -r ui/requirements.txt
streamlit run ui/app.py
```

Open http://localhost:8501. Set **API base URL** in the sidebar if the backend is not at `http://localhost:8000` (e.g. `ARGUS_API_URL` or the sidebar input).

## Sections

- **Research** — Trigger a research run with target name, context, objectives, and max depth. Streams events in a collapsible area, then shows the final report.
- **Evaluate** — Run evaluation for a completed research job. Form fields: Research ID (required), Ground truth file (default `timothy_overturf.json`), and “Use LLM judge” (per-metric reasoning via GPT-4.1). Click **Run evaluation** to POST to `/api/v1/evaluate`; a spinner shows “Running evaluation…” while the request is in progress. Results: **Evaluation metadata** (evaluation_id, research_id, metrics, summary) is shown as JSON in a markdown code block; **Evaluation report** is rendered as markdown below.
- **Health** — GET `/api/v1/health` and `/api/v1/ready`.
- **Graphs** — Placeholder for GET `/api/v1/graph/{id}` and export.
