"""Role alias resolution — friendly CLI names to canonical AT-SPI2 roles.

Every alias maps to exactly one canonical role name. Canonical names
map to themselves (identity). Unknown names are rejected.
"""

from __future__ import annotations

from typing import Dict


# Alias -> canonical AT-SPI2 role name.
# Canonical names are also included as identity mappings.
ROLE_ALIAS_TABLE: Dict[str, str] = {
    # Friendly aliases
    "button": "push_button",
    "checkbox": "check_box",
    "textbox": "text",
    "radio": "radio_button",
    "tab": "page_tab",
    "toolbar": "tool_bar",
    "scrollbar": "scroll_bar",
    "combobox": "combo_box",
    "menuitem": "menu_item",
    "listitem": "list_item",
    "treeitem": "tree_item",
    "statusbar": "status_bar",
    "progressbar": "progress_bar",
    # Canonical identity mappings
    "push_button": "push_button",
    "check_box": "check_box",
    "text": "text",
    "radio_button": "radio_button",
    "page_tab": "page_tab",
    "tool_bar": "tool_bar",
    "scroll_bar": "scroll_bar",
    "combo_box": "combo_box",
    "menu_item": "menu_item",
    "list_item": "list_item",
    "tree_item": "tree_item",
    "status_bar": "status_bar",
    "progress_bar": "progress_bar",
    "dialog": "dialog",
    "label": "label",
    "image": "image",
    "frame": "frame",
    "panel": "panel",
    "menu": "menu",
    "menu_bar": "menu_bar",
    "separator": "separator",
    "table": "table",
    "table_cell": "table_cell",
    "table_row": "table_row",
    "table_column_header": "table_column_header",
    "tree": "tree",
    "tree_table": "tree_table",
    "scroll_pane": "scroll_pane",
    "split_pane": "split_pane",
    "slider": "slider",
    "spin_button": "spin_button",
    "toggle_button": "toggle_button",
    "window": "window",
    "application": "application",
    "filler": "filler",
    "redundant_object": "redundant_object",
    "section": "section",
    "heading": "heading",
    "paragraph": "paragraph",
    "link": "link",
    "list": "list",
    "icon": "icon",
    "document_frame": "document_frame",
    "layered_pane": "layered_pane",
    "embedded": "embedded",
    "alert": "alert",
    "notification": "notification",
    "tooltip": "tooltip",
    "popup_menu": "popup_menu",
    "file_chooser": "file_chooser",
    "color_chooser": "color_chooser",
    "option_pane": "option_pane",
    "viewport": "viewport",
    "ruler": "ruler",
    "password_text": "password_text",
    "autocomplete": "autocomplete",
    "animation": "animation",
    "canvas": "canvas",
    "drawing_area": "drawing_area",
    "glass_pane": "glass_pane",
    "html_container": "html_container",
    "internal_frame": "internal_frame",
    "root_pane": "root_pane",
    "desktop_frame": "desktop_frame",
    "desktop_icon": "desktop_icon",
    "directory_pane": "directory_pane",
    "editbar": "editbar",
    "grouping": "grouping",
    "header": "header",
    "footer": "footer",
    "form": "form",
    "input_method_window": "input_method_window",
    "terminal": "terminal",
    "block_quote": "block_quote",
    "page": "page",
}


def resolve_role(alias: str) -> str:
    """Resolve a friendly alias or canonical role name.

    Returns the canonical AT-SPI2 role name.
    Raises ValueError for unknown aliases.
    """
    canonical = ROLE_ALIAS_TABLE.get(alias)
    if canonical is None:
        raise ValueError(f"Unknown role alias: {alias!r}")
    return canonical
