"""Shared builders, fakes and oracles for the accessible-description tests.

Trees are built as raw node dicts (the shape the Linux worker emits) and
converted with ``raw_tree_to_domain``, so a test reaches the description only
through the production conversion. The oracles below restate the selection,
scope, role-drift and listing rules from first principles; they reuse only
helpers whose behaviour is unchanged by the feature (``prune_tree``,
``flatten_nodes``, ``role_matches``, ``name_matches``).
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from aiyes.domain.matching import name_matches, role_matches
from aiyes.domain.session import Session
from aiyes.domain.tree import (
    AccessibilityTree,
    flatten_nodes,
    prune_tree,
    raw_tree_to_domain,
)
from aiyes.domain.types import ActionPortResult, StoredTree
from aiyes.domain.use_cases.compound_do import CompoundDoUseCase
from aiyes.domain.use_cases.find import FindUseCase
from aiyes.domain.use_cases.wait import WaitUseCase

# Marker for "the caller does not pass the description selector at all".
ABSENT = object()

# Values of the description selector that select nothing by themselves:
# null, empty, spaces, and a tab followed by a no-break space.
NOT_GIVEN_VALUES: Tuple[Optional[str], ...] = (None, "", "   ", "\t ")

SESSION_ID = "s1"


# ──────────────────────────────────────────────────────────────────────
# Raw trees
# ──────────────────────────────────────────────────────────────────────


def node(
    role: str,
    name: str = "",
    description: Optional[str] = None,
    children: Sequence[Dict[str, Any]] = (),
    states: Sequence[str] = ("enabled",),
    actions: Sequence[str] = ("click",),
    node_id: Optional[str] = None,
) -> Dict[str, Any]:
    """A raw node dict; the description key is present only when given."""
    raw: Dict[str, Any] = {
        "role": role,
        "name": name,
        "bounds": [0, 0, 10, 10],
        "states": list(states),
        "actions": list(actions),
        "children": list(children),
    }
    if node_id is not None:
        raw["id"] = node_id
    if description is not None:
        raw["description"] = description
    return raw


def forest(*roots: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Copy the roots and give every node without an id one, in pre-order."""
    copied = copy.deepcopy(list(roots))
    counter = [0]

    def assign(raw: Dict[str, Any]) -> None:
        counter[0] += 1
        raw.setdefault("id", f"n_{counter[0]:03d}")
        for child in raw["children"]:
            assign(child)

    for root in copied:
        assign(root)
    return copied


def to_tree(raw_roots: Sequence[Dict[str, Any]]) -> AccessibilityTree:
    """Convert raw node dicts to the domain tree (production conversion)."""
    return raw_tree_to_domain({"tree": copy.deepcopy(list(raw_roots))})


def flat_raw(raw_roots: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pre-order list of the raw nodes."""
    out: List[Dict[str, Any]] = []

    def walk(raw: Dict[str, Any]) -> None:
        out.append(raw)
        for child in raw["children"]:
            walk(child)

    for root in raw_roots:
        walk(root)
    return out


def strip_descriptions(raw_roots: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The same raw trees without any description key."""
    copied = copy.deepcopy(list(raw_roots))
    for raw in flat_raw(copied):
        raw.pop("description", None)
    return copied


def relabel_descriptions(
    raw_roots: Sequence[Dict[str, Any]], labels: Sequence[Optional[str]]
) -> List[Dict[str, Any]]:
    """The same raw trees with the descriptions replaced, in pre-order.

    ``None`` removes the key; labels are reused cyclically.
    """
    copied = copy.deepcopy(list(raw_roots))
    for index, raw in enumerate(flat_raw(copied)):
        label = labels[index % len(labels)] if labels else None
        raw.pop("description", None)
        if label is not None:
            raw["description"] = label
    return copied


def description_of(raw: Dict[str, Any]) -> str:
    value = raw.get("description")
    return value if isinstance(value, str) else ""


def node_description(value: Any) -> Any:
    """The description a domain or result object carries (None when it has none)."""
    return getattr(value, "description", None)


def drop_description_keys(value: Any) -> Any:
    """A JSON value with every node-level 'description' key removed."""
    if isinstance(value, dict):
        return {
            key: drop_description_keys(item)
            for key, item in value.items()
            if key != "description"
        }
    if isinstance(value, list):
        return [drop_description_keys(item) for item in value]
    return value


# ──────────────────────────────────────────────────────────────────────
# The comparison rule, stated independently of the production code
# ──────────────────────────────────────────────────────────────────────

_WHITESPACE_RUN = re.compile(r"\s+")


def cmp(text: str) -> str:
    """Whitespace runs to one space, ends stripped, lower-cased."""
    return _WHITESPACE_RUN.sub(" ", text).strip().lower()


def given(value: Any) -> bool:
    """A description selector is given when it is text with something in it."""
    return isinstance(value, str) and cmp(value) != ""


# ──────────────────────────────────────────────────────────────────────
# Fakes (structural implementations of the ports)
# ──────────────────────────────────────────────────────────────────────


class ScriptedTree:
    """Tree port returning the scripted snapshots in turn (the last repeats)."""

    def __init__(self, snapshots: Sequence[AccessibilityTree], log: List[Any]):
        self._snapshots = list(snapshots)
        self._log = log
        self._index = 0
        self.last_registry = None

    def get_tree(self, session: Any) -> AccessibilityTree:
        self._log.append(("get_tree",))
        snapshot = self._snapshots[min(self._index, len(self._snapshots) - 1)]
        self._index += 1
        return snapshot


class RecordingStore:
    """Tree store keeping the last saved tree and logging each save."""

    def __init__(self, log: List[Any]):
        self._log = log
        self.saved: Optional[AccessibilityTree] = None

    def save_tree(self, session_id: str, tree: Any, node_id_registry: Any = None):
        self._log.append(("save_tree",))
        self.saved = tree

    def load_tree(self, session_id: str) -> Optional[StoredTree]:
        if self.saved is None:
            return None
        return StoredTree(tree=self.saved, registry=None)


class FakeClock:
    """Clock that only moves when the code sleeps; every sleep is logged."""

    def __init__(self, log: List[Any]):
        self._log = log
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self._log.append(("sleep", seconds))
        self.t += seconds


class RecordingAction:
    """Action port that logs every action it is asked to perform."""

    def __init__(self, log: List[Any], success: bool = True):
        self._log = log
        self._success = success

    def do_action(
        self,
        session: Any,
        node_id: str,
        action_name: str,
        value: Optional[str] = None,
        registry: Any = None,
    ) -> ActionPortResult:
        self._log.append(("do_action", node_id, action_name))
        return ActionPortResult(success=self._success, available_actions=("click",))


class SessionRepo:
    """Session repository holding one session of the given backend."""

    def __init__(self, backend: str = "linux"):
        self._session = Session(
            session_id=SESSION_ID,
            app_pid=1,
            app_command="app",
            app_args=(),
            name=None,
            resolution="1280x800",
            backend=backend,
        )

    def load(self, session_id: str) -> Optional[Session]:
        return self._session if session_id == self._session.session_id else None

    def save(self, session: Any) -> None:  # pragma: no cover - not used
        raise AssertionError("not expected")

    def load_all(self) -> List[Session]:
        return [self._session]

    def delete(self, session_id: str) -> None:  # pragma: no cover - not used
        raise AssertionError("not expected")


@dataclasses.dataclass
class Ports:
    """One set of fakes sharing one port-call log."""

    snapshots: Sequence[AccessibilityTree]
    backend: str = "linux"
    action_success: bool = True

    def __post_init__(self) -> None:
        self.log: List[Any] = []
        self.tree = ScriptedTree(self.snapshots, self.log)
        self.store = RecordingStore(self.log)
        self.clock = FakeClock(self.log)
        self.action = RecordingAction(self.log, self.action_success)
        self.repo = SessionRepo(self.backend)

    def find_uc(self) -> FindUseCase:
        return FindUseCase(self.tree, self.repo, self.store)

    def wait_uc(self) -> WaitUseCase:
        return WaitUseCase(self.tree, self.repo, self.store, self.clock)

    def do_uc(self) -> CompoundDoUseCase:
        return CompoundDoUseCase(
            self.tree, self.action, self.repo, self.store, self.clock
        )


def ports_for(
    *raw_snapshots: Sequence[Dict[str, Any]],
    backend: str = "linux",
    action_success: bool = True,
) -> Ports:
    return Ports(
        snapshots=[to_tree(raw) for raw in raw_snapshots],
        backend=backend,
        action_success=action_success,
    )


def takes_description_selector(function: Any) -> bool:
    parameters = inspect.signature(function).parameters
    return "description_pattern" in parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )


def run(function: Any, description: Any = ABSENT, **kwargs: Any) -> Any:
    """Call a use case, passing description_pattern unless it is ABSENT.

    A use case without the selector fails here with an assertion that names
    the missing parameter, not with an unrelated error.
    """
    if description is not ABSENT:
        assert takes_description_selector(function), (
            f"{function.__qualname__} takes no description_pattern selector"
        )
        kwargs["description_pattern"] = description
    return function(**kwargs)


# ──────────────────────────────────────────────────────────────────────
# Oracles
# ──────────────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class Query:
    role: str = "*"
    name_pattern: Optional[str] = None
    state: Optional[str] = None
    description: Any = ABSENT
    within_role: Optional[str] = None
    within_name: Optional[str] = None
    no_prune: bool = False
    resource_id: Optional[str] = None

    @property
    def d(self) -> Any:
        return None if self.description is ABSENT else self.description

    @property
    def scope_requested(self) -> bool:
        return self.within_role is not None or self.within_name is not None


def _role_ok(raw: Dict[str, Any], role: str) -> bool:
    return role == "*" or role_matches(raw["role"], role)


def _name_ok(raw: Dict[str, Any], name_pattern: Optional[str]) -> bool:
    return name_pattern is None or name_matches(raw["name"], name_pattern)


def _describes(raw: Dict[str, Any], d: Any) -> bool:
    return cmp(description_of(raw)) == cmp(d)


def _contains(raw: Dict[str, Any], d: Any) -> bool:
    return cmp(d) in cmp(description_of(raw))


def _listing(candidates: List[Dict[str, Any]]) -> Optional[Tuple[List[str], int]]:
    distinct: List[str] = []
    for raw in candidates:
        text = description_of(raw)
        if text not in distinct:
            distinct.append(text)
    if not distinct:
        return None
    return distinct[:5], len(distinct)


def _drift(
    pool: List[Dict[str, Any]], role: str, name_pattern: Optional[str], d: Any
) -> List[Tuple[str, str, str]]:
    if role == "*":
        return []
    if given(d):
        picked = [
            raw
            for raw in pool
            if not role_matches(raw["role"], role)
            and _describes(raw, d)
            and _name_ok(raw, name_pattern)
        ]
    elif name_pattern:
        picked = [
            raw
            for raw in pool
            if not role_matches(raw["role"], role)
            and name_matches(raw["name"], name_pattern)
        ]
    else:
        picked = []
    return [(raw["id"], raw["role"], raw["name"]) for raw in picked]


def is_named_filler(raw: Dict[str, Any]) -> bool:
    return role_matches(raw["role"], "filler") and cmp(raw["name"]) != ""


def pruned_ids(raw_roots: Sequence[Dict[str, Any]], no_prune: bool) -> List[str]:
    tree = prune_tree(to_tree(raw_roots), prune=not no_prune)
    return [n.id for n in flatten_nodes(tree.roots)]


def _strict_ancestors(raw_roots: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    above: Dict[str, List[str]] = {}

    def walk(raw: Dict[str, Any], chain: List[str]) -> None:
        above[raw["id"]] = list(chain)
        for child in raw["children"]:
            walk(child, chain + [raw["id"]])

    for root in raw_roots:
        walk(root, [])
    return above


@dataclasses.dataclass(frozen=True)
class FindExpectation:
    pool: List[str]
    selected: List[str]
    scope_requested: bool
    scope_matched: bool
    ancestors: List[Tuple[str, str, str]]
    drift: List[Tuple[str, str, str]]
    listing: Optional[Tuple[List[str], int]]
    no_match: bool
    base_ancestors: List[Tuple[str, str, str]]
    base_selected: List[str]


def expect_find(raw_roots: Sequence[Dict[str, Any]], q: Query) -> FindExpectation:
    """find over one snapshot: pool, selection, scope, drift and listing."""
    flat = flat_raw(raw_roots)
    by_id = {raw["id"]: raw for raw in flat}
    kept = pruned_ids(raw_roots, q.no_prune)
    kept_set = set(kept)
    above = _strict_ancestors(raw_roots)

    def scope(raw: Dict[str, Any]) -> bool:
        return (q.within_role is None or role_matches(raw["role"], q.within_role)) and (
            q.within_name is None or name_matches(raw["name"], q.within_name)
        )

    def matches(raw: Dict[str, Any]) -> bool:
        return (
            _role_ok(raw, q.role)
            and _name_ok(raw, q.name_pattern)
            and (q.state is None or q.state in raw["states"])
            and (not q.resource_id or raw.get("resource_id", "") == q.resource_id)
        )

    def scoped_pool(ancestor_ids: set) -> List[str]:
        return [i for i in kept if set(above[i]) & ancestor_ids]

    # The scope rule of the implementation base: ancestors among the nodes
    # the command keeps.
    base_anc = [raw for raw in flat if raw["id"] in kept_set and scope(raw)]
    if q.scope_requested and q.no_prune:
        anc = [raw for raw in flat if scope(raw)]
    elif q.scope_requested:
        anc = [
            raw
            for raw in flat
            if (raw["id"] in kept_set or is_named_filler(raw)) and scope(raw)
        ]
    else:
        anc = []

    def triple(raw: Dict[str, Any]) -> Tuple[str, str, str]:
        return (raw["id"], raw["role"], raw["name"])

    base_selected: List[str] = []
    if not q.scope_requested:
        base_pool = kept
    elif base_anc:
        base_pool = scoped_pool({raw["id"] for raw in base_anc})
    else:
        base_pool = []
    base_selected = [i for i in base_pool if matches(by_id[i])]

    if q.scope_requested and not anc:
        return FindExpectation(
            pool=[],
            selected=[],
            scope_requested=True,
            scope_matched=False,
            ancestors=[],
            drift=[],
            listing=None,
            no_match=False,
            base_ancestors=[triple(raw) for raw in base_anc],
            base_selected=base_selected,
        )
    pool = scoped_pool({raw["id"] for raw in anc}) if q.scope_requested else kept
    pool_raw = [by_id[i] for i in pool]
    selected = [
        raw["id"]
        for raw in pool_raw
        if matches(raw) and (not given(q.d) or _describes(raw, q.d))
    ]
    no_match = not selected
    drift = _drift(pool_raw, q.role, q.name_pattern, q.d) if no_match else []
    listing = None
    if no_match and given(q.d):
        listing = _listing(
            [raw for raw in pool_raw if matches(raw) and _contains(raw, q.d)]
        )
    return FindExpectation(
        pool=pool,
        selected=selected,
        scope_requested=q.scope_requested,
        scope_matched=True,
        ancestors=[triple(raw) for raw in anc],
        drift=drift,
        listing=listing,
        no_match=no_match,
        base_ancestors=[triple(raw) for raw in base_anc],
        base_selected=base_selected,
    )


def expected_find_output(e: FindExpectation) -> Any:
    """The result-level shape of find's JSON (node dicts reduced to ids)."""
    if not (e.scope_requested or e.drift or e.listing):
        return list(e.selected)
    out: Dict[str, Any] = {"nodes": list(e.selected)}
    if e.scope_requested:
        out["scope_matched"] = e.scope_matched
        out["matched_ancestors"] = [
            {"id": i, "role": r, "name": n} for i, r, n in e.ancestors
        ]
    if e.drift:
        out["role_drift"] = [{"id": i, "role": r, "name": n} for i, r, n in e.drift]
    if e.listing:
        out["descriptions_containing"] = list(e.listing[0])
        out["descriptions_containing_count"] = e.listing[1]
    return out


def reduce_find_output(parsed: Any) -> Any:
    """find's parsed JSON with each node dict reduced to its id."""
    if isinstance(parsed, list):
        return [n["id"] for n in parsed]
    out = dict(parsed)
    out["nodes"] = [n["id"] for n in parsed["nodes"]]
    return out


@dataclasses.dataclass(frozen=True)
class PollExpectation:
    found: bool
    timeout: bool
    id: Optional[str]
    transient: bool
    drift: List[Tuple[str, str, str]]
    listing: Optional[Tuple[List[str], int]]
    no_match: bool
    port_calls: List[Any]
    deciding: int  # index of the snapshot the outcome was decided on
    error: Optional[str] = None
    acted_on: Optional[str] = None


def _snapshot(raw_snapshots: Sequence[Any], index: int) -> List[Dict[str, Any]]:
    return flat_raw(raw_snapshots[min(index, len(raw_snapshots) - 1)])


def expect_wait(
    raw_snapshots: Sequence[Any], q: Query, mode: str, timeout: float
) -> PollExpectation:
    """wait (present, absent or transient) over scripted snapshots."""
    interval = 0.2 if mode == "transient" else 0.5
    t = 0.0
    calls: List[Any] = []
    seen: List[str] = []
    poll = 0

    def matches(raw: Dict[str, Any]) -> bool:
        return (
            _role_ok(raw, q.role)
            and _name_ok(raw, q.name_pattern)
            and (q.state is None or q.state in raw["states"])
        )

    while True:
        calls.append(("get_tree",))
        flat = _snapshot(raw_snapshots, poll)
        selected = [
            raw
            for raw in flat
            if matches(raw) and (not given(q.d) or _describes(raw, q.d))
        ]
        if mode == "transient":
            for raw in selected:
                if raw["id"] not in seen:
                    seen.append(raw["id"])
        elif mode == "absent":
            if not selected:
                return PollExpectation(
                    False, False, None, False, [], None, False, calls, poll
                )
        elif selected:
            return PollExpectation(
                True, False, selected[0]["id"], False, [], None, False, calls, poll
            )
        if t >= timeout:
            if mode == "absent":
                return PollExpectation(
                    True, True, None, False, [], None, False, calls, poll
                )
            if mode == "transient" and selected:
                return PollExpectation(
                    True, False, selected[0]["id"], False, [], None, False, calls, poll
                )
            if mode == "transient" and seen:
                return PollExpectation(
                    True, False, seen[0], True, [], None, False, calls, poll
                )
            drift = _drift(flat, q.role, q.name_pattern, q.d)
            listing = None
            if given(q.d):
                listing = _listing(
                    [raw for raw in flat if matches(raw) and _contains(raw, q.d)]
                )
            return PollExpectation(
                False, True, None, False, drift, listing, True, calls, poll
            )
        calls.append(("sleep", interval))
        t += interval
        poll += 1


def expect_do(
    raw_snapshots: Sequence[Any],
    q: Query,
    timeout: Optional[float],
    action: str = "click",
    verify: bool = False,
) -> PollExpectation:
    """do over scripted snapshots: first selected node, or no match."""
    t = 0.0
    calls: List[Any] = []
    poll = 0

    def matches(raw: Dict[str, Any]) -> bool:
        return _role_ok(raw, q.role) and _name_ok(raw, q.name_pattern)

    while True:
        calls.append(("get_tree",))
        flat = _snapshot(raw_snapshots, poll)
        selected = [
            raw
            for raw in flat
            if matches(raw) and (not given(q.d) or _describes(raw, q.d))
        ]
        if selected:
            target = selected[0]["id"]
            calls.append(("do_action", target, action))
            if verify:
                calls.append(("get_tree",))
            return PollExpectation(
                True, False, target, False, [], None, False, calls, poll, None, target
            )
        if timeout is None or t >= timeout:
            listing = None
            if given(q.d):
                listing = _listing(
                    [raw for raw in flat if matches(raw) and _contains(raw, q.d)]
                )
            return PollExpectation(
                False,
                False,
                None,
                False,
                [],
                listing,
                True,
                calls,
                poll,
                "No matching node found",
            )
        calls.append(("sleep", 0.5))
        t += 0.5
        poll += 1


def selected_ids_on(
    raw_flat: List[Dict[str, Any]], q: Query, command: str
) -> List[str]:
    """Ids a command's selectors accept from one unpruned snapshot (wait, do)."""
    out = []
    for raw in raw_flat:
        ok = _role_ok(raw, q.role) and _name_ok(raw, q.name_pattern)
        if command == "wait":
            ok = ok and (q.state is None or q.state in raw["states"])
        if ok and (not given(q.d) or _describes(raw, q.d)):
            out.append(raw["id"])
    return out


def base_find_dicts(raw: Any, no_prune: bool) -> Dict[str, List[Tuple[str, Any]]]:
    """Node dicts the implementation base gives: the same tree without descriptions."""
    from aiyes.cli.presenter import format_find_nodes

    ports = ports_for(strip_descriptions(raw))
    result = ports.find_uc().execute(session_id=SESSION_ID, role="*", no_prune=no_prune)
    return {d["id"]: list(d.items()) for d in json.loads(format_find_nodes(result))}


def check_node_dict(
    emitted: Dict[str, Any], base: List[Tuple[str, Any]], raw: Any, d: Any
) -> None:
    """One emitted node: the base dict plus the raw description when not empty."""
    # Existing keys, values and order are the base's; nested children are
    # compared without their own description keys.
    assert [
        (k, drop_description_keys(v)) for k, v in emitted.items() if k != "description"
    ] == base
    text = description_of(raw)
    if text:
        assert emitted.get("description") == text
    else:
        assert "description" not in emitted
    if given(d):
        assert cmp(emitted["description"]) == cmp(d)
