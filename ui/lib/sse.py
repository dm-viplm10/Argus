"""SSE stream parser for research progress events."""

from __future__ import annotations

import json
from typing import Iterator


def parse_sse_stream(response) -> Iterator[tuple[str, str]]:
    """Consume a requests Response with stream=True and yield (event_type, data) pairs.

    SSE format: lines like "event: xxx" and "data: yyy", separated by blank line.
    """
    event_type = "message"
    data_buf: list[str] = []

    for line in response.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line == "":
            if data_buf:
                yield (event_type, "\n".join(data_buf))
            event_type = "message"
            data_buf = []
            continue
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_buf.append(line[5:].strip())

    if data_buf:
        yield (event_type, "\n".join(data_buf))
