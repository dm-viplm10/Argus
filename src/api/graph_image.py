"""Render identity graph (nodes + edges) to PNG/JPEG image bytes."""

from __future__ import annotations

import io
from typing import Literal

from src.api.v1.schemas.graph import GraphResponse


def render_graph_image(
    graph: GraphResponse,
    format: Literal["png", "jpeg", "jpg"] = "png",
    dpi: int = 100,
    figsize: tuple[float, float] = (12, 8),
) -> bytes:
    """Render the graph to image bytes using NetworkX + Matplotlib.

    Args:
        graph: GraphResponse with nodes and edges.
        format: Output format: "png", "jpeg", or "jpg".
        dpi: Dots per inch for the image.
        figsize: Figure size (width, height) in inches.

    Returns:
        Image bytes (PNG or JPEG).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx

    if not graph.nodes:
        # Empty graph: return a small placeholder image
        return _empty_image_bytes(format, dpi)

    G = nx.DiGraph()
    for node in graph.nodes:
        G.add_node(node.id, labels=node.labels, **node.properties)
    for edge in graph.edges:
        G.add_edge(edge.source, edge.target, type=edge.type)

    # Layout: spring works well for small/medium graphs
    try:
        pos = nx.spring_layout(G, k=1.5, iterations=50, seed=42)
    except Exception:
        pos = nx.shell_layout(G)

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # Node labels: use name property or id, truncate for display
    labels = {}
    for node in graph.nodes:
        name = node.properties.get("name", node.id)
        if isinstance(name, str) and len(name) > 20:
            name = name[:17] + "..."
        labels[node.id] = name or node.id

    nx.draw_networkx_nodes(
        G,
        pos,
        node_color="#4a90d9",
        node_size=800,
        alpha=0.9,
        ax=ax,
    )
    nx.draw_networkx_edges(
        G,
        pos,
        edge_color="#666666",
        arrows=True,
        arrowsize=12,
        ax=ax,
    )
    nx.draw_networkx_labels(
        G,
        pos,
        labels=labels,
        font_size=8,
        font_color="black",
        ax=ax,
    )

    ax.axis("off")
    plt.tight_layout(pad=0.5)

    buf = io.BytesIO()
    save_fmt = "jpg" if format in ("jpeg", "jpg") else "png"
    plt.savefig(buf, format=save_fmt, bbox_inches="tight", facecolor="white", dpi=dpi)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _empty_image_bytes(format: Literal["png", "jpeg", "jpg"], dpi: int) -> bytes:
    """Return a small placeholder image when the graph has no nodes."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(4, 2), dpi=dpi)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.text(0.5, 0.5, "No nodes in graph", ha="center", va="center", fontsize=12)
    ax.axis("off")
    buf = io.BytesIO()
    save_fmt = "jpg" if format in ("jpeg", "jpg") else "png"
    plt.savefig(buf, format=save_fmt, bbox_inches="tight", facecolor="white", dpi=dpi)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
