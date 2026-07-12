"""Query-DOM use case — structured, measured view of CSS-selected elements.

Pure domain logic: builds the getBoundingClientRect + fixed 15-prop computed
subset (DEC-06) + classList + textContent + count JS snippet, then shapes the
port outcome into a capped, truncation-aware result (SC-02: node cap 50, count is
the TRUE total). Empty match is status='ok' (not an error). All socket/protocol
I/O lives behind MarionettePort (NFR-02 / C-PURITY).
"""

from __future__ import annotations

import dataclasses
from typing import Any, List, Optional

from aiyes.ports.marionette import MarionettePort
from aiyes.ports.storage import SessionRepositoryPort

# DEC-06 — the frozen 15-prop computed-style subset, in declared order. A caller
# override (SC-01) is explicitly OUT of scope: the subset is fixed here.
COMPUTED_STYLE_SUBSET: List[str] = [
    "display",
    "visibility",
    "position",
    "width",
    "height",
    "color",
    "background-color",
    "border-radius",
    "transform",
    "opacity",
    "font-size",
    "font-weight",
    "margin",
    "padding",
    "z-index",
]

# SC-02 — the returned node list is capped; ``count`` still reports the true
# total and ``truncated`` flags an over-cap match.
QUERY_DOM_NODE_LIMIT = 50

# The 8 getBoundingClientRect keys every node exposes (fixed shape).
_RECT_KEYS: List[str] = ["x", "y", "width", "height", "top", "right", "bottom", "left"]


@dataclasses.dataclass(frozen=True)
class DomNodeView:
    """A single measured DOM node: rect(8) + computed(15) + classList + text."""

    rect: dict
    computed: dict
    classList: list
    textContent: str


@dataclasses.dataclass(frozen=True)
class QueryDomResult:
    """Result of a query_dom operation."""

    status: str
    session_id: str
    selector: Optional[str] = None
    count: int = 0
    nodes: List[DomNodeView] = dataclasses.field(default_factory=list)
    truncated: bool = False
    reason: Optional[str] = None


def _project_node(payload: Any) -> DomNodeView:
    """Shape one raw node payload into the fixed rect(8)/computed(15) view."""
    payload = payload if isinstance(payload, dict) else {}
    raw_rect = payload.get("rect") or {}
    raw_computed = payload.get("computed") or {}
    rect = {k: raw_rect.get(k) for k in _RECT_KEYS}
    computed = {k: raw_computed.get(k) for k in COMPUTED_STYLE_SUBSET}
    class_list = list(payload.get("classList") or [])
    text = payload.get("textContent")
    return DomNodeView(
        rect=rect,
        computed=computed,
        classList=class_list,
        textContent=text if isinstance(text, str) else "",
    )


class QueryDomUseCase:
    """Return a structured, measured view of the elements matching a CSS selector."""

    def __init__(
        self, marionette: MarionettePort, session_repo: SessionRepositoryPort
    ) -> None:
        self._marionette = marionette
        self._session_repo = session_repo

    def execute(self, session_id: str, selector: str) -> QueryDomResult:
        """Query ``selector`` and return count + capped node views.

        Guarded paths return status='error' with ZERO MarionettePort I/O; an
        unknown session raises RuntimeError. Zero matches is status='ok' with an
        empty node list — an empty match is not an error.
        """
        session = self._session_repo.load(session_id)
        if session is None:
            raise RuntimeError(f"Session not found: {session_id}")

        # FR-06 guard — inlined per A-R1 (no shared helper).
        backend = getattr(session, "backend", "linux")
        if backend != "linux":
            return QueryDomResult(
                status="error",
                session_id=session_id,
                selector=selector,
                reason=(
                    "query_dom is a linux firefox/marionette primitive; "
                    f"not applicable to backend={backend!r}"
                ),
            )
        if getattr(session, "marionette_port", None) is None:
            return QueryDomResult(
                status="error",
                session_id=session_id,
                selector=selector,
                reason=(
                    "query_dom requires a marionette-enabled session; restart "
                    "the session with marionette enabled "
                    "(session start --marionette)"
                ),
            )

        outcome = self._marionette.execute_script(
            session, _build_query_dom_script(), [selector]
        )
        if not outcome.ok:
            return QueryDomResult(
                status="error",
                session_id=session_id,
                selector=selector,
                reason=outcome.error,
            )

        value = outcome.value if isinstance(outcome.value, dict) else {}
        try:
            count = int(value.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        raw_nodes = value.get("nodes") or []
        nodes = [_project_node(n) for n in raw_nodes[:QUERY_DOM_NODE_LIMIT]]
        return QueryDomResult(
            status="ok",
            session_id=session_id,
            selector=selector,
            count=count,
            nodes=nodes,
            truncated=count > QUERY_DOM_NODE_LIMIT,
        )


def _build_query_dom_script() -> str:
    """Build the content-context JS returning {count, nodes:[{rect,computed,...}]}.

    Runs against arguments[0] (the CSS selector). Reads getBoundingClientRect
    for the 8-key rect and getComputedStyle for the fixed 15-prop subset; count
    is the TRUE match total while the node list is capped at QUERY_DOM_NODE_LIMIT.
    """
    props = ",".join(f'"{p}"' for p in COMPUTED_STYLE_SUBSET)
    return (
        "var els = document.querySelectorAll(arguments[0]);"
        f"var props = [{props}];"
        f"var limit = {QUERY_DOM_NODE_LIMIT};"
        "var nodes = [];"
        "for (var i = 0; i < els.length && i < limit; i++) {"
        "  var el = els[i];"
        "  var r = el.getBoundingClientRect();"
        "  var cs = window.getComputedStyle(el);"
        "  var computed = {};"
        "  for (var j = 0; j < props.length; j++) {"
        "    computed[props[j]] = cs.getPropertyValue(props[j]);"
        "  }"
        "  nodes.push({"
        "    rect: {x: r.x, y: r.y, width: r.width, height: r.height,"
        "           top: r.top, right: r.right, bottom: r.bottom, left: r.left},"
        "    computed: computed,"
        "    classList: Array.prototype.slice.call(el.classList),"
        "    textContent: (el.textContent || '')"
        "  });"
        "}"
        "return {count: els.length, nodes: nodes};"
    )
