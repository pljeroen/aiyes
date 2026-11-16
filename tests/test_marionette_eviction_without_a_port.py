"""Marionette adapter: evicting a session that has no port.

The adapter caches one transport per port number. Evicting a session whose port
reads as None looks None up in that cache, so a cached port whose hash is None's
is asked whether it equals None: when it answers yes, its transport is dropped
and closed; when it answers no, the cache is left as it was; and when the
comparison fails, the error reaches the caller.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from aiyes.adapters.marionette_adapter import MarionetteAdapter


class Transport:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class PortHashingLikeNone(int):
    """A port number with None's hash whose answer to ``== None`` is given."""

    def __new__(cls, value: int, answer: Any) -> PortHashingLikeNone:
        port = super().__new__(cls, value)
        port.answer = answer
        port.comparisons_with_none = 0
        return port

    def __hash__(self) -> int:
        return hash(None)

    def __eq__(self, other: object) -> bool:
        if other is None:
            self.comparisons_with_none += 1
            if isinstance(self.answer, Exception):
                raise self.answer
            return bool(self.answer)
        return int.__eq__(self, other)


def _adapter_caching(port: int) -> tuple[MarionetteAdapter, Transport]:
    transport = Transport()
    adapter = MarionetteAdapter(connect=lambda host, connect_port: transport)
    adapter._transport_for(SimpleNamespace(marionette_port=port))
    return adapter, transport


def _served_from_cache(adapter: MarionetteAdapter, port: int) -> bool:
    _, from_cache = adapter._transport_for(SimpleNamespace(marionette_port=port))
    return from_cache


@pytest.mark.parametrize(
    ("answer", "closed", "still_cached"),
    [
        pytest.param(True, 1, False, id="port-equal-to-none"),
        pytest.param(False, 0, True, id="port-not-equal-to-none"),
    ],
)
def test_evicting_a_session_without_a_port_asks_a_cached_port_that_hashes_like_none(
    answer: bool, closed: int, still_cached: bool
) -> None:
    port = PortHashingLikeNone(2828, answer)
    adapter, transport = _adapter_caching(port)

    returned = adapter._evict(SimpleNamespace(marionette_port=None))

    assert returned is None
    assert port.comparisons_with_none == 1
    assert transport.closed == closed
    assert _served_from_cache(adapter, port) is still_cached


def test_evicting_a_session_without_a_port_propagates_a_failing_comparison_with_none() -> (
    None
):
    port = PortHashingLikeNone(2828, LookupError("cannot compare this port with None"))
    adapter, transport = _adapter_caching(port)

    with pytest.raises(LookupError, match="cannot compare this port with None"):
        adapter._evict(SimpleNamespace(marionette_port=None))

    assert transport.closed == 0
    assert _served_from_cache(adapter, port) is True
