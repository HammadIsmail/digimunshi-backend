import json
import glob
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def get_color(fills):
    if fills and isinstance(fills, list) and len(fills) > 0:
        f = fills[0]
        if f.get("type") == "SOLID" and "color" in f:
            c = f["color"]
            r = int(round(c.get("r", 0) * 255))
            g = int(round(c.get("g", 0) * 255))
            b = int(round(c.get("b", 0) * 255))
            a = f.get("opacity", 1)
            return f"#{r:02x}{g:02x}{b:02x}" + (f" (opacity {a})" if a < 1 else "")
    return ""

def summarize_node(node, depth=0):
    indent = "  " * depth
    name = node.get("name", "")
    ntype = node.get("type", "")
    text = node.get("characters")
    if not text and isinstance(node.get("textContent"), str):
        text = node.get("textContent")

    color = get_color(node.get("fills", []))
    stroke = get_color(node.get("strokes", []))
    
    extra = []
    w = node.get("width")
    h = node.get("height")
    if w is not None and h is not None:
        extra.append(f"{w}x{h}")
    if color:
        extra.append(f"bg:{color}")
    if stroke:
        extra.append(f"border:{stroke}")
    if "cornerRadius" in node:
        extra.append(f"r:{node['cornerRadius']}")
    if "fontSize" in node:
        extra.append(f"font:{node.get('fontFamily', '')} {node['fontSize']}px w{node.get('fontWeight', '')}")
    if text:
        clean_text = text.replace('\n', ' ').strip()
        extra.append(f"'{clean_text}'")
    
    info = " | ".join(extra)
    print(f"{indent}- [{ntype}] {name}" + (f" ({info})" if info else ""))
    
    for ch in node.get("children", []):
        summarize_node(ch, depth + 1)

files = glob.glob(os.path.join(os.path.dirname(__file__), "..", "design", "*.json"))
for f in sorted(files):
    print("=" * 80)
    print(os.path.basename(f))
    print("=" * 80)
    with open(f, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    summarize_node(data)
