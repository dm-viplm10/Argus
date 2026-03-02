"""Render identity graph (nodes + edges) to PNG/JPEG image bytes."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.api.v1.schemas.graph import GraphResponse
    pass

# Matplotlib backend flag — ensures we call matplotlib.use("Agg") exactly once
# no matter how many times render_graph_image / _empty_image_bytes are called.
_AGG_BACKEND_CONFIGURED = False


def _ensure_agg_backend() -> None:
    """Set the non-interactive Agg backend before the first pyplot import.

    Safe to call multiple times — the flag guarantees a single effective call.
    Calling matplotlib.use() after pyplot is imported emits a warning in some
    versions; keeping this here avoids that in both render paths.
    """
    global _AGG_BACKEND_CONFIGURED
    if not _AGG_BACKEND_CONFIGURED:
        import matplotlib
        matplotlib.use("Agg")
        _AGG_BACKEND_CONFIGURED = True


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
    _ensure_agg_backend()
    import matplotlib.pyplot as plt
    import networkx as nx

    if not graph.nodes:
        # Empty graph: return a small placeholder image
        return _empty_image_bytes(format, dpi)

    G = nx.DiGraph()  # noqa: N806 — G is the standard graph-theory variable name
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

    nx.draw_networkx_nodes(G, pos, node_color="#4a90d9", node_size=800, alpha=0.9, ax=ax)
    nx.draw_networkx_edges(G, pos, edge_color="#666666", arrows=True, arrowsize=12, ax=ax)
    nx.draw_networkx_labels(G, pos, labels=labels, font_size=8, font_color="black", ax=ax)

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
    _ensure_agg_backend()
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
