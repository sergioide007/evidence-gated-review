"""Añade puntitos que viajan por las aristas de un SVG renderizado con Mermaid (mmdc).

Pipeline:
  mmdc -i evidence-gated-review/docs/architecture.mmd -o /tmp/arch.svg      (Mermaid -> SVG)
  python tools/animate_mermaid_svg.py /tmp/arch.svg evidence-gated-review/docs/architecture-animated.svg

Usa SMIL (<animateMotion>), que se reproduce en navegadores y en <img src="...svg"> de GitHub sin JavaScript.
Cada arista recibe 3 puntos escalonados; las aristas bidireccionales reciben también puntos en sentido inverso.
"""
from __future__ import annotations

import math
import re
import sys
import xml.etree.ElementTree as ET

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", XLINK)

TEAL, AMBER, RED = "#0E9AA7", "#F2A900", "#D64545"
# Edge ids (see docs/architecture.mmd): model calls in amber, discards in red, everything else teal.
COLORS = {"e7": AMBER, "e8": AMBER, "e14": AMBER, "e12": RED, "e17": RED}
SPEED_PX_S = 110.0
DOTS_PER_EDGE = 3


def q(tag: str) -> str:
    return f"{{{SVG}}}{tag}"


def path_length(d: str) -> float:
    """Approximate length from the polyline through the path's coordinate pairs."""
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", d)]
    pts = list(zip(nums[0::2], nums[1::2]))
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:])) or 1.0


def dot(path_id: str, color: str, dur: float, begin: float, reverse: bool) -> ET.Element:
    g = ET.Element(q("g"), {"class": "flow-dot", "opacity": "0"})
    ET.SubElement(g, q("circle"), {"r": "10", "fill": color, "opacity": "0.25"})
    ET.SubElement(g, q("circle"), {"r": "4.6", "fill": color, "stroke": "#FFFFFF", "stroke-width": "1.2"})
    # visible only while travelling (avoids a dot parked at the origin before `begin`)
    ET.SubElement(g, q("set"), {"attributeName": "opacity", "to": "1", "begin": f"{begin:.2f}s", "fill": "freeze"})
    attrs = {"dur": f"{dur:.2f}s", "begin": f"{begin:.2f}s", "repeatCount": "indefinite", "rotate": "0"}
    if reverse:
        attrs.update({"keyPoints": "1;0", "keyTimes": "0;1", "calcMode": "linear"})
    am = ET.SubElement(g, q("animateMotion"), attrs)
    ET.SubElement(am, q("mpath"), {f"{{{XLINK}}}href": f"#{path_id}", "href": f"#{path_id}"})
    return g


def main(src: str, dst: str) -> None:
    tree = ET.parse(src)
    root = tree.getroot()

    # static lines: stop Mermaid's own dash animation, dots carry the motion
    for el in root.iter(q("path")):
        cls = el.get("class")
        if cls:
            el.set("class", re.sub(r"\bedge-animation-(fast|slow)\b", "", cls))

    layer = ET.SubElement(root, q("g"), {"id": "flow-dots"})
    edges = [p for p in root.iter(q("path")) if re.fullmatch(r".*-e\d+", p.get("id", ""))]
    for n, p in enumerate(edges):
        pid = p.get("id")
        key = pid.rsplit("-", 1)[1]
        color = COLORS.get(key, TEAL)
        dur = max(2.6, path_length(p.get("d", "")) / SPEED_PX_S)
        offset = (n * 0.37) % dur
        for k in range(DOTS_PER_EDGE):
            layer.append(dot(pid, color, dur, offset + k * dur / DOTS_PER_EDGE, reverse=False))
        if p.get("marker-start"):  # bidirectional edge: traffic in both directions
            for k in range(2):
                layer.append(dot(pid, color, dur * 1.15, offset + 0.6 + k * dur / 2, reverse=True))

    # legend + explicit size (width="100%" alone collapses inside <img>)
    vb = [float(x) for x in root.get("viewBox").split()]
    w, h = vb[2], vb[3]
    extra = 78.0
    root.set("viewBox", f"0 0 {w:.2f} {h + extra:.2f}")
    root.set("width", f"{w:.0f}")
    root.set("height", f"{h + extra:.0f}")
    style = root.get("style", "")
    root.set("style", re.sub(r"max-width:[^;]+;?", "", style).strip())
    lg = ET.SubElement(root, q("g"), {"id": "legend", "font-family": "DejaVu Sans, Arial, sans-serif", "font-size": "15"})
    x = 40.0
    y = h + 36
    for color, label in ((TEAL, "flujo de datos entre componentes"), (AMBER, "llamadas a modelos (con presupuesto y circuit breaker)"),
                         (RED, "hallazgos descartados")):
        ET.SubElement(lg, q("circle"), {"cx": f"{x:.0f}", "cy": f"{y:.0f}", "r": "10", "fill": color, "opacity": "0.25"})
        ET.SubElement(lg, q("circle"), {"cx": f"{x:.0f}", "cy": f"{y:.0f}", "r": "4.6", "fill": color})
        t = ET.SubElement(lg, q("text"), {"x": f"{x + 20:.0f}", "y": f"{y + 5:.0f}", "fill": "#0B1F3A"})
        t.text = label
        x += 20 + len(label) * 8.6 + 30
    tree.write(dst, encoding="utf-8", xml_declaration=True)
    print(f"ok {dst}: {len(edges)} aristas, {len(layer)} puntos")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
