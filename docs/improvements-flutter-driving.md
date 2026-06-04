# aiyes improvements — driving Flutter apps reliably

**Status:** backlog / discovery note (not yet contracted).
**Discovered:** 2026-06-04, while driving the **socialzzz** Flutter app (Android emulator, AT‑SPI/Android‑a11y backend) for its C5/C6 end‑to‑end scenarios. All 7 `socialzzz/test/scenarios/*.json` failed — **none were app bugs**; they hit three aiyes‑addressable issues below. aiyes version at time of writing: **0.2.0**.

Two of the three were worked around on the *scenario* side already (in the socialzzz repo); this note captures the **aiyes‑side** fixes that would make Flutter driving robust generally so the scenarios don't have to compensate.

---

## 1. `text_or_name_matches` ignores `expect_present` (assertion negation)

**Where:** `src/aiyes/domain/scenario_assertions.py:42` (the `text_or_name_matches` branch).

```python
if kind == "text_or_name_matches":
    pattern = str(assertion.get("pattern", "")).lower()
    ok = bool(pattern) and any(pattern in value.lower() for value in _tree_strings(context))
    return _result(assertion_id, kind, ok, "pattern not found in tree text/name")
```

It only checks **presence**. Scenarios written as "assert X is *absent*" pass `expect_present: false`, but the field is **silently dropped** (the scenario schema is `additionalProperties: true`, so validation lets it through). Result: every "no‑redscreen" assertion (`pattern: "Failed assertion", expect_present: false`) *fails* — because "Failed assertion" is correctly **not** in the tree, which the matcher scores as a failed presence check. The author's intent (absence) is inverted.

**Fix (small):** honor `expect_present` (default `true`); when `false`, negate the match:
```python
expect_present = bool(assertion.get("expect_present", True))
present = bool(pattern) and any(pattern in v.lower() for v in _tree_strings(context))
ok = present if expect_present else (bool(pattern) and not present)
```
Then add `expect_present` to the `text_or_name_matches` validator in `domain/scenario.py` so a typo can't silently pass.

**Note:** aiyes *does* already have a real absence primitive — the `wait` step with `absent: true` (`domain/use_cases/wait.py:52`). The socialzzz scenarios were repaired by converting the broken asserts to `wait … absent: true`. But honoring `expect_present` on the assertion is the cleaner general fix (scenarios then work as authored).

---

## 2. `scroll_into_view` uses a blind viewport swipe (Flutter lazy lists / off‑screen targets)

**Where:** `src/aiyes/adapters/scenario_use_case_executor.py:192` (loop: `find` → `self._gesture.swipe(session_id, x1, y1, x2, y2, 300)`), with `x1..y2` computed **once** from the viewport by `_swipe_coords_for_direction` (`:673`). The standalone `"scroll"` action (`src/aiyes/adapters/android_action_adapter.py:232`) is **also** a blind `input swipe` (centered on the node, `cy → cy-300`). aiyes never uses a native a11y scroll action anywhere.

A fixed viewport swipe is fragile on Flutter: it can land on a non‑scrollable region or get intercepted (e.g. the bottom nav bar), so the list never actually moves; meanwhile Flutter lazy‑builds list children, so an off‑screen target isn't in the a11y tree until scrolled near. The loop then burns all `max_scrolls` (15 here) and fails `scroll_into_view_target_not_found`. Observed on socialzzz's `compositor-share-image/-video` reaching the per‑platform "Share to Linkedin/Instagram" cards on the Preview tab even with the *correct* `role: Button` target.

**Fix (durable):** add an **accessibility scroll action** path — locate the scrollable's node and perform the native `ACTION_SCROLL_FORWARD` / `ACTION_SCROLL_BACKWARD` (Flutter exposes `scrollUp`/`scrollDown`/scroll semantic actions on `Scrollable`s through the a11y bridge; surfaceable via uiautomator). This scrolls the real scrollable regardless of where it sits on screen, is immune to nav‑bar interception, and converges with lazy list‑building. Wire it as the default scroll mechanism for `scroll_into_view` (fall back to the swipe when no scrollable node is found).

**Cheaper interim:** make the swipe **node/region‑targeted** instead of full‑viewport — swipe within the scrollable's bounds and stay clear of the bottom system/nav inset.

---

## 3. Role drift: Flutter tappable items are role `Button`, not `View` (matching resilience)

**Symptom:** socialzzz scenarios that scrolled/found `role: "View"` for More‑screen list items (`Target Markets`, `Load Demo Company`, `Data Management`) failed — the items are exposed as role **`Button`** (verified live: `find role=* name="Target Markets"` → role `Button` with a `click` action). A sibling scenario using `role: "Button"` for the same item scrolled fine. The app's semantics had evolved; the hardcoded `role: "View"` silently stopped matching, and `scroll_into_view`'s internal `find` then never converged.

**aiyes‑side options (pick one):**
- Treat `role` as **advisory** in `find`/`scroll_into_view` when a `name_pattern` is given — match on name, rank by role, rather than hard‑filtering on an exact role. (Most forgiving; mirrors how a human reads the UI.)
- Or document/encourage `role: "*"` (the wildcard `scroll_into_view` already defaults to) in the scenario authoring guide, and emit a diagnostic when a `name_pattern` matches under a *different* role than the one requested ("found 'Target Markets' as Button, you asked for View") so drift is obvious instead of a silent 15‑scroll timeout.

---

## Bottom line
None of these are platform limitations — Flutter semantics *are* exposed to the Android a11y tree (aiyes finds/taps Flutter `Button`s fine). They're three concrete, in‑scope aiyes improvements: **(1)** assertion `expect_present` negation, **(2)** a11y‑action scrolling, **(3)** role‑drift‑resilient matching. (1) and (3)‑diagnostic are small; (2) is the highest‑value robustness upgrade for any lazy‑list Flutter app.
