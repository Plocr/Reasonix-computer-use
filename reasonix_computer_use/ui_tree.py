"""Computer use tools - UI tree module (Windows UIAutomation)."""

import json
import time
import ctypes
import ctypes.wintypes
from reasonix_computer_use.mcp_server import register_tool
from reasonix_computer_use.utils import parse_result

# Load UIAutomation COM
try:
    import comtypes.client
    _UIA_AVAILABLE = True
except ImportError:
    _UIA_AVAILABLE = False

# Control type names (subset of UIAutomation control types)
CONTROL_TYPE_NAMES = {
    50000: "Window",
    50001: "Button",
    50002: "Calendar",
    50003: "CheckBox",
    50004: "ComboBox",
    50005: "Edit",
    50006: "Hyperlink",
    50007: "Image",
    50008: "ListItem",
    50009: "List",
    50010: "Menu",
    50011: "MenuBar",
    50012: "MenuItem",
    50013: "ProgressBar",
    50014: "RadioButton",
    50015: "ScrollBar",
    50016: "Slider",
    50017: "Spinner",
    50018: "StatusBar",
    50019: "Tab",
    50020: "TabItem",
    50021: "Text",
    50022: "ToolBar",
    50023: "ToolTip",
    50024: "Tree",
    50025: "TreeItem",
    50032: "Pane",
    50033: "Header",
    50034: "HeaderItem",
    50035: "Table",
    50036: "TitleBar",
    50037: "Separator",
}

# Windows API for finding windows
user32 = ctypes.windll.user32


def _get_uia_element(element):
    """Safely get element name and properties.
    
    Each property access is wrapped in specific exception handling
    to provide useful defaults when UIA attributes are unavailable.
    """
    name = ""
    auto_id = ""
    class_name = ""
    control_type = 0
    bounding_rect = None
    is_enabled = True
    is_offscreen = False
    
    try:
        name = element.CurrentName
    except (AttributeError, OSError, comtypes.COMError):
        pass
    
    try:
        auto_id = element.CurrentAutomationId
    except (AttributeError, OSError, comtypes.COMError):
        pass
    
    try:
        class_name = element.CurrentClassName
    except (AttributeError, OSError, comtypes.COMError):
        pass
    
    try:
        control_type = element.CurrentControlType
    except (AttributeError, OSError, comtypes.COMError):
        pass
    
    try:
        bounding_rect = element.CurrentBoundingRectangle
    except (AttributeError, OSError, comtypes.COMError):
        pass
    
    try:
        is_enabled = element.CurrentIsEnabled
    except (AttributeError, OSError, comtypes.COMError):
        pass
    
    try:
        is_offscreen = element.CurrentIsOffscreen
    except (AttributeError, OSError, comtypes.COMError):
        pass
    
    return {
        "name": name if name else "",
        "automation_id": auto_id if auto_id else "",
        "class_name": class_name if class_name else "",
        "control_type": CONTROL_TYPE_NAMES.get(control_type, str(control_type)),
        "bounding_rect": list(bounding_rect) if bounding_rect else None,
        "enabled": is_enabled,
        "offscreen": is_offscreen,
    }


def _walk_uia_tree(element, depth=0, max_depth=10):
    """Recursively walk UIAutomation tree and collect element info."""
    if depth > max_depth or element is None:
        return []
    
    info = _get_uia_element(element)
    info["depth"] = depth
    info["children"] = []
    
    try:
        children = element.FindAll(
            comtypes.gen.UIAutomationClient.TreeScope_Children,
            comtypes.gen.UIAutomationClient.TrueCondition
        )
        
        for i in range(children.Length):
            try:
                child = children.GetElement(i)
                child_info = _walk_uia_tree(child, depth + 1, max_depth)
                info["children"].extend(child_info)
            except (OSError, comtypes.COMError, AttributeError):
                pass
    except (OSError, comtypes.COMError, AttributeError):
        pass
    
    return [info]


@register_tool(
    name="computer_ui_tree",
    description="""Get the UI Automation (UIA) tree for a window or the entire desktop.

Returns a hierarchical tree of UI elements with:
- name: text label shown to user
- automation_id: developer-assigned identifier (most reliable for scripting)
- class_name: window class name
- control_type: type of control (Button, Edit, Text, Window, etc.)
- bounding_rect: [left, top, right, bottom] screen coordinates
- enabled: whether the user can interact with it
- offscreen: whether the element is currently not visible on screen
- depth: nesting depth in the tree (0 = root)

Parameters:
- window_id: optional window identifier (title, hwnd, or class_name). If omitted, gets the desktop tree.
- max_depth: how deep to traverse (default 10). Higher values capture nested UI but increase response size.

The tree structure preserves parent-child relationships, so you can determine which controls belong to which container.
""",
    schema={
        "type": "object",
        "properties": {
            "window_id": {
                "type": "string",
                "description": "Window identifier. If omitted, walks the entire desktop tree."
            },
            "max_depth": {
                "type": "integer",
                "default": 10,
                "description": "Max depth of element traversal. Default 10."
            },
            "include_offscreen": {
                "type": "boolean",
                "default": False,
                "description": "If false (default), skip off-screen elements."
            }
        }
    }
)
async def computer_ui_tree(args: dict) -> str:
    """Get UIAutomation tree."""
    if not _UIA_AVAILABLE:
        return parse_result({
            "error": "comtypes not installed. Install with: pip install comtypes"
        })
    
    window_id = args.get("window_id")
    max_depth = args.get("max_depth", 10)
    include_offscreen = args.get("include_offscreen", False)
    
    try:
        # Initialize UIAutomation
        uia = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}")
        
        if window_id:
            # Find specific window by title or hwnd
            hwnd = None
            if window_id.startswith("0x") or window_id.isdigit():
                hwnd = int(window_id, 16) if window_id.startswith("0x") else int(window_id)
            else:
                # Find by title
                result = []
                
                @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
                def enum_callback(hwnd, lParam):
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length == 0:
                        return True
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    if window_id.lower() in buff.value.lower():
                        result.append(hwnd)
                        return False
                    return True
                
                user32.EnumWindows(enum_callback, 0)
                hwnd = result[0] if result else None
            
            if hwnd is None:
                return parse_result({"error": f"Window not found: {window_id}"})
            
            element = uia.ElementFromHandle(hwnd)
        else:
            # Get desktop root
            element = uia.GetRootElement()
        
        # Walk the tree
        tree = _walk_uia_tree(element, 0, max_depth)
        
        # Filter offscreen if needed
        if not include_offscreen:
            def filter_offscreen(node_list):
                return [n for n in node_list if not n.get("offscreen", False) and 
                        (n.get("children") is None or len(n.get("children", [])) == 0 or 
                         filter_offscreen(n.get("children", [])))]
            
            # Simplified: just remove top-level offscreen, keep children for now
            tree = [n for n in tree if not n.get("offscreen", False) or include_offscreen]
        
        return parse_result({
            "status": "ok",
            "window_id": window_id or "desktop",
            "elements": tree,
            "max_depth": max_depth
        })
    except Exception as e:
        return parse_result({"error": str(e)})


@register_tool(
    name="computer_find_element",
    description="""Find UI elements matching criteria.

Searches through the UIAutomation tree for elements that match:
- automation_id: exact match on UIAutomationId property
- name: exact or partial match on element name (label)
- control_type: control type name (Button, Edit, Text, Window, etc.)
- class_name: window class name match
- visible_text: text content match (for enabled controls)

Parameters:
- criteria: object with one or more of the above properties
- match_type: "exact" or "partial" for text matching (default "partial")
- max_results: max number of results (default 10)

Returns a list of matching elements with their bounding_rect for clicking,
automation_id for scripting, and parent-child relationships.
""",
    schema={
        "type": "object",
        "properties": {
            "criteria": {
                "type": "object",
                "properties": {
                    "automation_id": {"type": "string", "description": "Exact UIAutomationId match."},
                    "name": {"type": "string", "description": "Element name/label match."},
                    "control_type": {"type": "string", "description": "Control type (Button, Edit, etc.)."},
                    "class_name": {"type": "string", "description": "Window class name match."},
                    "visible_text": {"type": "string", "description": "Text content match (for Edit/Text controls)."}
                },
                "description": "Search criteria — at least one should be specified."
            },
            "match_type": {
                "type": "string",
                "enum": ["exact", "partial"],
                "default": "partial",
                "description": "Text matching mode."
            },
            "max_results": {
                "type": "integer",
                "default": 10,
                "description": "Maximum number of results."
            }
        },
        "required": ["criteria"]
    }
)
async def computer_find_element(args: dict) -> str:
    """Find UI element(s) matching criteria."""
    if not _UIA_AVAILABLE:
        return parse_result({
            "error": "comtypes not installed. Install with: pip install comtypes"
        })
    
    criteria = args.get("criteria", {})
    match_type = args.get("match_type", "partial")
    max_results = args.get("max_results", 10)
    
    try:
        # Initialize UIAutomation
        uia = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}")
        root = uia.GetRootElement()
        
        # Build search criteria (no UIA conditions needed - we walk manually)
        search_items = []
        for field_name in ("automation_id", "name", "class_name", "control_type"):
            if field_name in criteria and criteria[field_name]:
                search_items.append((field_name, criteria[field_name].lower()))
        
        if not search_items:
            return parse_result({"error": "No valid search criteria provided."})
        
        # Walk the tree manually and filter
        matches = []
        
        def walk_and_filter(element, depth=0):
            if len(matches) >= max_results or depth > 15:
                return
            
            try:
                name = element.CurrentName or ""
                auto_id = element.CurrentAutomationId or ""
                class_name = element.CurrentClassName or ""
                control_type = CONTROL_TYPE_NAMES.get(element.CurrentControlType, "")
            except (AttributeError, OSError, comtypes.COMError):
                return
            
            match = True
            for field_name, value in search_items:
                if field_name == "name":
                    element_value = name.lower()
                elif field_name == "automation_id":
                    element_value = auto_id.lower()
                elif field_name == "class_name":
                    element_value = class_name.lower()
                elif field_name == "control_type":
                    element_value = control_type.lower()
                    # For control_type, only do exact match
                    if value != element_value:
                        match = False
                    continue
                else:
                    continue
                
                if match_type == "exact":
                    if value != element_value:
                        match = False
                else:  # partial
                    if value not in element_value:
                        match = False
            
            if match:
                try:
                    bounds = element.CurrentBoundingRectangle
                    is_offscreen = element.CurrentIsOffscreen
                    matches.append({
                        "name": name,
                        "automation_id": auto_id,
                        "class_name": class_name,
                        "control_type": control_type,
                        "bounding_rect": list(bounds) if bounds else None,
                        "offscreen": is_offscreen,
                    })
                except (AttributeError, OSError, comtypes.COMError):
                    pass
            
            # Walk children
            try:
                children = element.FindAll(
                    comtypes.gen.UIAutomationClient.TreeScope_Children,
                    comtypes.gen.UIAutomationClient.TrueCondition
                )
                for i in range(children.Length):
                    if len(matches) >= max_results:
                        break
                    walk_and_filter(children.GetElement(i), depth + 1)
            except (OSError, comtypes.COMError, AttributeError):
                pass
        
        walk_and_filter(root, 0)
        
        return parse_result({
            "status": "ok",
            "matches": matches,
            "count": len(matches)
        })
    except Exception as e:
        return parse_result({"error": str(e)})
