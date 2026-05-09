"""AIYES-66: Android stable node identity fingerprints."""

from __future__ import annotations

from aiyes.domain.output_formatter import node_to_dict
from aiyes.domain.tree import Node, raw_tree_to_domain


XML_WITH_RESOURCE_ID = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="Create" resource-id="com.example.publicdemo:id/create"
        class="android.widget.Button" package="com.example.publicdemo"
        content-desc="Create post" bounds="[10,20][110,80]" clickable="true" />
</hierarchy>
"""


XML_WITHOUT_RESOURCE_ID = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.ImageButton"
        package="com.app" content-desc="Back" bounds="[0,0][48,48]"
        clickable="true" />
</hierarchy>
"""


def test_android_parser_preserves_legacy_node_id_and_adds_stable_id() -> None:
    from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

    tree, registry = parse_uiautomator_xml(XML_WITH_RESOURCE_ID)
    node = tree.roots[0]

    assert node.id == "n_001"
    assert registry.has_id("n_001")
    assert node.stable_id == (
        "android:rid=com.example.publicdemo:id/create;"
        "class=android.widget.Button;"
        "name=Create post;"
        "bounds=10,20,100,60;"
        "path=0"
    )


def test_android_parser_falls_back_to_semantics_when_resource_id_missing() -> None:
    from aiyes.adapters.android_tree_adapter import parse_uiautomator_xml

    tree, _ = parse_uiautomator_xml(XML_WITHOUT_RESOURCE_ID)
    node = tree.roots[0]

    assert node.id == "n_001"
    assert node.stable_id == (
        "android:rid=;"
        "class=android.widget.ImageButton;"
        "name=Back;"
        "bounds=0,0,48,48;"
        "path=0"
    )


def test_stable_id_is_serialized_and_restored() -> None:
    node = Node(
        id="n_001",
        role="Button",
        name="Create",
        bounds=(10, 20, 100, 60),
        states=("enabled",),
        actions=("click",),
        stable_id="android:rid=com.app:id/create;class=Button;name=Create;bounds=10,20,100,60;path=0",
    )

    data = node_to_dict(node)
    restored = raw_tree_to_domain({"tree": [data]}).roots[0]

    assert data["stable_id"] == node.stable_id
    assert restored.stable_id == node.stable_id
