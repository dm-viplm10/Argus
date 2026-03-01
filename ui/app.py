"""Argus Streamlit UI — separate from src, uses backend APIs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow importing ui.lib when running as: streamlit run ui/app.py
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from lib.api import (
    get_base_url,
    get_graph,
    get_graph_image,
    get_research,
    health,
    ready,
    run_evaluation,
    start_research,
    stream_research,
)
from lib.sse import parse_sse_stream

# One-liner message for each graph node (shown in expander with spinner).
NODE_STEP_MESSAGES = {
    "supervisor": "Coordinating next step…",
    "planner": "Planning research phases…",
    "phase_strategist": "Deciding phase strategy…",
    "query_refiner": "Refining search queries…",
    "search_and_analyze": "Searching and analyzing sources…",
    "verifier": "Verifying facts…",
    "risk_assessor": "Assessing risks…",
    "graph_builder": "Building identity graph…",
    "synthesizer": "Writing final report…",
}


# Page config
st.set_page_config(
    page_title="Argus",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Sidebar navigation
st.sidebar.title("Argus")
st.sidebar.caption("AI OSINT investigation")
nav = st.sidebar.radio(
    "Section",
    ["Research", "Evaluate", "Health", "Graphs"],
    label_visibility="collapsed",
)

# API base URL (optional)
api_url = st.sidebar.text_input(
    "API base URL",
    value=get_base_url(),
    help="Backend API root, e.g. http://localhost:8000",
)
if api_url:
    import os
    os.environ["ARGUS_API_URL"] = api_url.rstrip("/")

# ----- Research tab -----
if nav == "Research":
    st.header("Research")

    OBJECTIVES = [
        "biographical",
        "professional",
        "financial",
        "legal",
        "social",
        "behavioral",
        "connections",
        "risk_assessment",
    ]

    with st.form("research_form"):
        target_name = st.text_input("Target name", placeholder="e.g. Timothy Overturf", value="")
        target_context = st.text_input(
            "Target context (optional)",
            placeholder="e.g. CEO of Sisu Capital",
            value="",
        )
        objectives = st.multiselect(
            "Objectives",
            options=OBJECTIVES,
            default=["biographical", "financial", "risk_assessment", "connections"],
            help="Research areas to cover",
        )
        max_depth = st.number_input(
            "Max depth (phases)",
            min_value=1,
            max_value=10,
            value=5,
            step=1,
            help="Leave default or set 1–10. Backend can use dynamic phases if configured.",
        )
        use_dynamic = st.checkbox(
            "Use dynamic phases (ignore max depth)",
            value=False,
            help="If checked, backend decides phases from Phase 1 findings.",
        )
        submitted = st.form_submit_button("Trigger research")

    if submitted:
        if not target_name.strip():
            st.error("Target name is required.")
        else:
            depth_param = None if use_dynamic else max_depth
            try:
                result = start_research(
                    target_name=target_name.strip(),
                    target_context=target_context.strip(),
                    objectives=objectives or OBJECTIVES[:4],
                    max_depth=depth_param,
                )
            except Exception as e:
                st.error(f"Failed to start research: {e}")
            else:
                research_id = result["research_id"]
                st.success(f"Research started: `{research_id}`")

                report_placeholder = st.empty()
                collapsible_placeholder = st.empty()

                current_node = ""
                current_tool = ""
                synthesizer_streaming = False
                report_content = ""

                try:
                    resp = stream_research(research_id)
                    resp.raise_for_status()

                    for event_type, data_str in parse_sse_stream(resp):
                        try:
                            data = json.loads(data_str) if data_str else {}
                        except json.JSONDecodeError:
                            data = {"raw": data_str[:200]}

                        node = data.get("node", "")

                        if event_type == "node_start":
                            if node == "synthesizer":
                                synthesizer_streaming = True
                                collapsible_placeholder.empty()
                                current_node = ""
                                current_tool = ""
                            else:
                                current_node = node
                                current_tool = ""
                        elif event_type == "tool_start" and not synthesizer_streaming:
                            current_tool = data.get("tool", "")
                        elif event_type == "tool_end" and not synthesizer_streaming:
                            current_tool = ""
                        elif event_type in ("token", "thinking"):
                            part = (data.get("content") or "")
                            if event_type == "thinking":
                                part = f"*{part}*" if part else ""
                            if synthesizer_streaming:
                                report_content += part
                                with report_placeholder.container():
                                    st.subheader("Final report")
                                    st.markdown(report_content)

                        if not synthesizer_streaming:
                            step_msg = NODE_STEP_MESSAGES.get(
                                current_node, "Running…" if current_node else "Starting…"
                            )
                            with collapsible_placeholder.container():
                                with st.expander("Research progress", expanded=True):
                                    with st.spinner(step_msg):
                                        if current_tool:
                                            st.caption(f"Using: **{current_tool}**")

                        if event_type == "done":
                            break
                        if event_type == "error":
                            st.error(data.get("error", data_str))
                            break

                except Exception as e:
                    st.error(f"Stream error: {e}")

                # Final report only: fetch if we have it, show in main area. No execution trace.
                try:
                    full = get_research(research_id)
                    final_report = full.get("final_report") or report_content
                    status = full.get("status", "unknown")

                    with report_placeholder:
                        st.subheader("Final report")
                        if final_report:
                            st.markdown(final_report)
                        else:
                            st.info(f"Status: {status}. Report not yet available.")

                    collapsible_placeholder.empty()
                except Exception as e:
                    st.warning(f"Could not load final result: {e}")

# ----- Evaluate tab -----
elif nav == "Evaluate":
    st.header("Evaluate")
    st.caption("Run evaluation for a completed research job (ground truth comparison + optional LLM judge).")

    with st.form("evaluate_form"):
        research_id = st.text_input(
            "Research ID",
            placeholder="e.g. 2acaa87f-e3fe-4011-8f07-8098b252855c",
            value="",
            help="ID of a completed research run to evaluate.",
        )
        ground_truth_file = st.text_input(
            "Ground truth file",
            value="timothy_overturf.json",
            help="Filename in the backend ground_truth directory.",
        )
        use_llm_judge = st.checkbox(
            "Use LLM judge (per-metric reasoning)",
            value=True,
            help="Score each metric using LLM-as-judge (GPT-4.1).",
        )
        submitted = st.form_submit_button("Run evaluation")

    if submitted:
        if not research_id.strip():
            st.error("Research ID is required.")
        else:
            try:
                with st.spinner("Running evaluation..."):
                    result = run_evaluation(
                        research_id=research_id.strip(),
                        ground_truth_file=ground_truth_file.strip() or "timothy_overturf.json",
                        use_llm_judge=use_llm_judge,
                    )
            except Exception as e:
                st.error(f"Evaluation failed: {e}")
            else:
                # Metadata as JSON-formatted markdown (above)
                meta = {
                    "evaluation_id": result.get("evaluation_id", ""),
                    "research_id": result.get("research_id", ""),
                    "metrics": result.get("metrics", {}),
                    "summary": result.get("summary", ""),
                }
                st.subheader("Evaluation metadata")
                st.markdown(f"```json\n{json.dumps(meta, indent=2)}\n```")

                # Evaluation report as markdown (below)
                report = result.get("evaluation_report") or ""
                if report:
                    st.subheader("Evaluation report")
                    st.markdown(report)
                else:
                    st.info("No evaluation report in response.")

# ----- Health tab -----
elif nav == "Health":
    st.header("Health")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Health")
        try:
            h = health()
            st.json(h)
        except Exception as e:
            st.error(str(e))
    with col2:
        st.subheader("Ready")
        try:
            r = ready()
            st.json(r)
        except Exception as e:
            st.error(str(e))

# ----- Graphs tab -----
elif nav == "Graphs":
    st.header("Graphs")
    st.caption("View the identity graph for a completed research job.")

    with st.form("graph_form"):
        graph_research_id = st.text_input(
            "Research ID",
            placeholder="e.g. 2acaa87f-e3fe-4011-8f07-8098b252855c",
            value="",
            help="ID of a research run whose graph you want to view.",
        )
        submitted = st.form_submit_button("Load graph")

    if submitted:
        if not graph_research_id.strip():
            st.error("Research ID is required.")
        else:
            try:
                graph_data = get_graph(graph_research_id.strip())
                node_count = graph_data.get("node_count", 0)
                edge_count = graph_data.get("edge_count", 0)

                if node_count == 0 and edge_count == 0:
                    st.warning("This research has no graph data (empty nodes and edges).")
                else:
                    with st.spinner("Rendering graph…"):
                        image_bytes = get_graph_image(graph_research_id.strip(), format="png")
                    st.caption(f"Nodes: **{node_count}** · Edges: **{edge_count}**")
                    st.image(image_bytes, use_container_width=True)
            except Exception as e:
                st.error(f"Failed to load graph: {e}")
