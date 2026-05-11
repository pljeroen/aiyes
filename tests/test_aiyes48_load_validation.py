"""AIYES-48: per-kind load-time parameter validation for new step kinds.

Catches the most common authoring mistakes at validate_scenario_document
time instead of mid-execution. Scope: wait, wait_stable, wait_reactive,
key, mouse_drag, mouse_scroll, gesture_pinch, gesture_two_finger_scroll,
swipe. Pre-AIYES-44 kinds are deferred.
"""

from __future__ import annotations

import pytest

from aiyes.domain.scenario import validate_scenario_document


def _doc(step: dict, target: str = "linux") -> dict:
    return {
        "schema_version": 1,
        "id": "t",
        "title": "t",
        "target": target,
        "steps": [step],
        "evidence_policy": {"bundle": False, "redact_environment": True},
    }


def _codes(document: dict) -> list[str]:
    result = validate_scenario_document(document)
    return [issue.code for issue in result.issues]


# ─── wait ────────────────────────────────────────────────────────────


def test_wait_without_role_rejected() -> None:
    codes = _codes(_doc({"id": "w", "kind": "wait"}))
    assert "wait_role_required" in codes


def test_wait_with_role_accepts() -> None:
    result = validate_scenario_document(
        _doc({"id": "w", "kind": "wait", "role": "button"})
    )
    assert result.ok, f"Issues: {result.issues}"


def test_wait_with_invalid_timeout_rejected() -> None:
    codes = _codes(_doc({"id": "w", "kind": "wait", "role": "button", "timeout": -1}))
    assert "wait_timeout_invalid" in codes


# ─── wait_reactive ────────────────────────────────────────────────────


def test_wait_reactive_without_condition_rejected() -> None:
    codes = _codes(_doc({"id": "r", "kind": "wait_reactive"}))
    assert "wait_reactive_condition_required" in codes


def test_wait_reactive_with_invalid_condition_rejected() -> None:
    codes = _codes(_doc({"id": "r", "kind": "wait_reactive", "condition": "nope"}))
    assert "wait_reactive_condition_invalid" in codes


def test_wait_reactive_with_valid_condition_accepts() -> None:
    for condition in (
        "screen-change",
        "node-appears",
        "node-disappears",
        "focus-change",
        "app-change",
    ):
        result = validate_scenario_document(
            _doc({"id": "r", "kind": "wait_reactive", "condition": condition})
        )
        assert result.ok, f"{condition} should validate: {result.issues}"


# ─── key ─────────────────────────────────────────────────────────────


def test_key_without_keys_rejected() -> None:
    codes = _codes(_doc({"id": "k", "kind": "key"}))
    assert "key_keys_required" in codes


def test_key_with_empty_keys_rejected() -> None:
    codes = _codes(_doc({"id": "k", "kind": "key", "keys": []}))
    assert "key_keys_required" in codes


def test_key_with_non_list_keys_rejected() -> None:
    codes = _codes(_doc({"id": "k", "kind": "key", "keys": "Escape"}))
    assert "key_keys_required" in codes


# ─── mouse_drag ──────────────────────────────────────────────────────


def test_mouse_drag_ambiguous_coord_mode_rejected_at_load() -> None:
    codes = _codes(
        _doc(
            {
                "id": "d",
                "kind": "mouse_drag",
                "x1": 0,
                "y1": 0,
                "x2": 10,
                "y2": 10,
                "source": "earlier",
                "dx": 0,
                "dy": 0,
            }
        )
    )
    assert "coord_mode_ambiguous" in codes


def test_mouse_drag_missing_coord_mode_rejected_at_load() -> None:
    codes = _codes(_doc({"id": "d", "kind": "mouse_drag"}))
    assert "coord_mode_missing" in codes


def test_mouse_drag_source_without_offsets_rejected_at_load() -> None:
    codes = _codes(_doc({"id": "d", "kind": "mouse_drag", "source": "earlier"}))
    assert "coord_mode_missing" in codes


# ─── mouse_scroll ────────────────────────────────────────────────────


def test_mouse_scroll_without_direction_rejected() -> None:
    codes = _codes(_doc({"id": "s", "kind": "mouse_scroll"}))
    assert "direction_invalid" in codes


def test_mouse_scroll_with_invalid_direction_rejected() -> None:
    codes = _codes(_doc({"id": "s", "kind": "mouse_scroll", "direction": "diagonal"}))
    assert "direction_invalid" in codes


def test_mouse_scroll_with_valid_direction_accepts() -> None:
    for direction in ("up", "down", "left", "right"):
        result = validate_scenario_document(
            _doc({"id": "s", "kind": "mouse_scroll", "direction": direction})
        )
        assert result.ok, f"{direction} should validate: {result.issues}"


# ─── gesture_pinch ───────────────────────────────────────────────────


def test_gesture_pinch_without_scale_factor_rejected() -> None:
    codes = _codes(
        _doc(
            {"id": "p", "kind": "gesture_pinch", "x": 0, "y": 0},
            target="android",
        )
    )
    assert "gesture_pinch_scale_factor_required" in codes


def test_gesture_pinch_with_scale_factor_accepts() -> None:
    result = validate_scenario_document(
        _doc(
            {
                "id": "p",
                "kind": "gesture_pinch",
                "x": 0,
                "y": 0,
                "scale_factor": 1.5,
            },
            target="android",
        )
    )
    assert result.ok, f"Issues: {result.issues}"


# ─── gesture_two_finger_scroll ───────────────────────────────────────


def test_gesture_two_finger_scroll_without_direction_rejected() -> None:
    codes = _codes(
        _doc(
            {"id": "t", "kind": "gesture_two_finger_scroll", "x": 0, "y": 0},
            target="android",
        )
    )
    assert "direction_invalid" in codes


# ─── swipe ───────────────────────────────────────────────────────────


def test_swipe_ambiguous_coord_mode_rejected_at_load() -> None:
    codes = _codes(
        _doc(
            {
                "id": "s",
                "kind": "swipe",
                "x1": 0,
                "y1": 0,
                "x2": 10,
                "y2": 10,
                "source": "earlier",
                "dx": 0,
                "dy": 0,
            },
            target="android",
        )
    )
    assert "coord_mode_ambiguous" in codes


def test_swipe_missing_coord_mode_rejected_at_load() -> None:
    codes = _codes(_doc({"id": "s", "kind": "swipe"}, target="android"))
    assert "coord_mode_missing" in codes


def test_swipe_with_duration_exceeded_rejected() -> None:
    codes = _codes(
        _doc(
            {
                "id": "s",
                "kind": "swipe",
                "x1": 0,
                "y1": 0,
                "x2": 100,
                "y2": 100,
                "duration_ms": 5001,
            },
            target="android",
        )
    )
    assert "swipe_duration_exceeded" in codes


def test_swipe_at_duration_ceiling_accepts() -> None:
    result = validate_scenario_document(
        _doc(
            {
                "id": "s",
                "kind": "swipe",
                "x1": 0,
                "y1": 0,
                "x2": 100,
                "y2": 100,
                "duration_ms": 5000,
            },
            target="android",
        )
    )
    assert result.ok, f"Issues: {result.issues}"


# ─── Existing kinds remain unaffected (no regression) ────────────────


def test_existing_kinds_still_validate() -> None:
    """Pre-AIYES-44 kinds keep their existing validation behaviour."""
    for kind, extra in (
        ("inspect", {}),
        ("type_text", {"text": "x"}),
        ("screenshot", {}),
    ):
        result = validate_scenario_document(_doc({"id": "x", "kind": kind, **extra}))
        assert result.ok, f"{kind} should validate: {result.issues}"
