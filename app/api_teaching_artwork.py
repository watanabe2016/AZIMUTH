"""按模型提供的点位与标注排版教学航图，不重新解题或修正数值。"""
import math
from html import escape
from PySide6.QtGui import QColor
import theme as T


def artwork_palette():
    """从当前主题取语义色，保证图中文字在当前画布上可读。"""
    def solid(value, fallback):
        color = QColor(value)
        return color.name() if color.isValid() else fallback

    canvas = solid(T.P.get("shell", ""), "#0E1622" if T.mode == "dark" else "#F2F7FE")
    ink = solid(T.P.get("ink", ""), "#EAF1FA" if T.mode == "dark" else "#101720")

    def blend(a, b, amount):
        ca, cb = QColor(a), QColor(b)
        return QColor(*(round(x*(1-amount)+y*amount) for x, y in
                        zip(ca.getRgb()[:3], cb.getRgb()[:3]))).name()

    def luminance(value):
        channels = [n/255 for n in QColor(value).getRgb()[:3]]
        linear = [n/12.92 if n <= .04045 else ((n+.055)/1.055)**2.4 for n in channels]
        return sum(n*w for n, w in zip(linear, (.2126, .7152, .0722)))

    def readable(value):
        color = solid(value, ink)
        bg = luminance(canvas)
        for step in range(11):
            candidate = blend(color, ink, step/10)
            fg = luminance(candidate)
            if (max(fg, bg)+.05)/(min(fg, bg)+.05) >= 4.5:
                return candidate
        return ink

    primary = readable(T.C_BLUE)
    return {"canvas": canvas, "ink": ink, "primary": primary,
            "access": readable(T.P.get("warn_fg", ink)),
            "fill": blend(canvas, primary, .14 if T.mode == "dark" else .10)}


def teaching_svg(result):
    points = result.get("points") or {}
    if len(points) < 2 or len(points) > 40:
        return None
    try:
        raw = {str(k): (float(v["x"]), float(v["y"])) for k, v in points.items()}
        if not all(math.isfinite(n) for p in raw.values() for n in p):
            return None
    except (TypeError, ValueError, KeyError):
        return None
    width, height = 900, 720
    xs, ys = zip(*raw.values())
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    scale = min(590 / max(span_x, 1), 390 / max(span_y, 1))
    down = result.get("y_axis") == "down"
    xy = {k: (450 + (x - (min(xs) + max(xs)) / 2) * scale,
              290 + (y - (min(ys) + max(ys)) / 2) * scale * (1 if down else -1))
          for k, (x, y) in raw.items()}
    palette = artwork_palette()
    blue, ink, orange = palette["primary"], palette["ink"], palette["access"]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
             f'<rect width="900" height="720" fill="{palette["canvas"]}"/>']
    labels = []

    def text(x, y, value, color=ink, size=28, anchor="middle"):
        # 标注只移动文字，避免覆盖已有标注；点位和线条不参与任何纠偏。
        estimated = sum(size * (1 if ord(c) > 255 else .58) for c in str(value))
        for offset in (0, -36, 36, -72, 72):
            yy = max(size+8, min(height-10, y+offset))
            left = x if anchor == "start" else x-estimated if anchor == "end" else x-estimated/2
            left = max(12, min(width-estimated-12, left))
            rect = (left, yy-size, left+estimated, yy+5)
            if not any(rect[0] < r[2]+5 and rect[2] > r[0]-5 and
                       rect[1] < r[3]+5 and rect[3] > r[1]-5 for r in labels):
                y = yy
                x = left if anchor == "start" else left+estimated if anchor == "end" else left+estimated/2
                labels.append(rect)
                break
        parts.append(f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" '
                     f'font-family="Microsoft YaHei, sans-serif" font-size="{size}" '
                     f'font-weight="600" fill="{color}">{escape(str(value))}</text>')

    def line(a, b, color, dashed=False, arrow=False):
        if a not in xy or b not in xy:
            return
        x, y = xy[a]
        xx, yy = xy[b]
        dash = ' stroke-dasharray="9 7"' if dashed else ''
        parts.append(f'<path d="M{x:.2f},{y:.2f} L{xx:.2f},{yy:.2f}" '
                     f'fill="none" stroke="{color}" stroke-width="4"{dash}/>')
        if arrow:
            dx, dy = xx - x, yy - y
            norm = math.hypot(dx, dy)
            if norm > 30:
                ux, uy = dx / norm, dy / norm
                cx, cy = x + dx * .57, y + dy * .57
                parts.append(f'<path d="M{cx+ux*10:.2f},{cy+uy*10:.2f} '
                             f'L{cx-ux*8-uy*7:.2f},{cy-uy*8+ux*7:.2f} '
                             f'L{cx-ux*8+uy*7:.2f},{cy-uy*8-ux*7:.2f} Z" fill="{color}"/>')

    geometry = result.get("geometry_edges") or []
    flight = result.get("flight_edges") or []
    access = result.get("access_edges") or []
    edges = [tuple(map(str, e)) for e in geometry if isinstance(e, (list, tuple)) and len(e) == 2]
    # 只对模型已经明确闭合的图形填色，不补造缺失的边。
    graph = {}
    for a, b in edges:
        if a in xy and b in xy:
            graph.setdefault(a, []).append(b)
            graph.setdefault(b, []).append(a)
    if len(graph) >= 3 and all(len(v) == 2 for v in graph.values()):
        start = next(iter(graph))
        cycle, previous, current = [], None, start
        while current not in cycle:
            cycle.append(current)
            following = next((v for v in graph[current] if v != previous), None)
            previous, current = current, following
        if current == start and len(cycle) == len(graph):
            vertices = " ".join(f"{xy[k][0]:.2f},{xy[k][1]:.2f}" for k in cycle)
            parts.append(f'<polygon points="{vertices}" fill="{palette["fill"]}"/>')
    for a, b in edges:
        line(a, b, blue)
    for edge in flight:
        if len(edge) == 2 and list(edge) not in access:
            line(str(edge[0]), str(edge[1]), blue, arrow=True)
    for edge in access:
        if len(edge) == 2:
            line(str(edge[0]), str(edge[1]), orange, dashed=True)
    legs = result.get("flight_legs") or []
    measurements = {(str(v.get("from")), str(v.get("to"))): v for v in legs if isinstance(v, dict)}
    for edge in access:
        if len(edge) != 2:
            continue
        a, b = map(str, edge)
        leg = measurements.get((a, b))
        if leg and a in xy and b in xy and leg.get("distance_m") is not None:
            text((xy[a][0]+xy[b][0])/2, (xy[a][1]+xy[b][1])/2-20,
                 f'{leg["distance_m"]} m', orange)
    # 弧线的几何只用于放置标注，角度文字完全采用模型字段。
    for annotation in result.get("annotations") or []:
        if not isinstance(annotation, dict) or annotation.get("type") != "angle":
            continue
        a, v, b = (str(annotation.get(k, "")) for k in ("from", "vertex", "to"))
        if any(k not in xy for k in (a, v, b)) or annotation.get("value_deg") is None:
            continue
        vx, vy = xy[v]
        rays = [(xy[k][0]-vx, xy[k][1]-vy) for k in (a, b)]
        norms = [math.hypot(*ray) for ray in rays]
        if min(norms) < 1:
            continue
        u, w = [(ray[0]/n, ray[1]/n) for ray, n in zip(rays, norms)]
        radius = min(55, min(norms)*.25)
        is_right = annotation.get("value_deg") in (90, 90.0, "90")
        color = ink if is_right else orange
        if is_right:
            radius = min(28, radius)
            coords = [(vx+u[0]*radius, vy+u[1]*radius),
                      (vx+(u[0]+w[0])*radius, vy+(u[1]+w[1])*radius),
                      (vx+w[0]*radius, vy+w[1]*radius)]
            path = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in coords)
        else:
            def phase(ray):
                value = math.acos(max(-1, min(1, ray[0])))
                return value if ray[1] >= 0 else -value
            begin, end = phase(u), phase(w)
            delta = (end-begin+math.pi) % (2*math.pi)-math.pi
            coords = [(vx+radius*math.cos(begin+delta*i/24),
                       vy+radius*math.sin(begin+delta*i/24)) for i in range(25)]
            path = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in coords)
        parts.append(f'<path d="{path}" stroke="{color}" stroke-width="2.5" fill="none"/>')
        if not is_right:
            bx, by = u[0]+w[0], u[1]+w[1]
            length = math.hypot(bx, by) or 1
            text(vx+bx/length*(radius+30), vy+by/length*(radius+30)+7,
                 f'{annotation["value_deg"]}°', orange)
    center = (450, 290)
    for name, (x, y) in xy.items():
        launch = name == "H" or "起飞" in name
        color = orange if launch else ink
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="10" fill="{palette["canvas"]}" '
                     f'stroke="{color}" stroke-width="4"/>')
        text(x+(-18 if x < center[0] else 18), y+(-18 if y < center[1] else 31),
             "起飞点 H" if name == "H" else name, color, 28,
             "end" if x < center[0] else "start")
    if result.get("north_up"):
        parts.append(f'<path d="M60,103 L60,48 M60,40 L51,57 L69,57 Z" '
                     f'stroke="{ink}" stroke-width="4" fill="{blue}"/>')
        text(60, 130, "N", ink, 24)
    # 航向按模型数值列在画布下方，避免多条边的文字互相遮挡。
    rows = []
    for leg in legs:
        if not isinstance(leg, dict) or leg.get("bearing_deg") is None:
            continue
        bearing = leg["bearing_deg"]
        compass = ""
        try:
            value = float(bearing)
            if 0 <= value <= 360:
                compass = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((value+22.5)//45) % 8]
        except (ValueError, TypeError):
            pass
        rows.append(f'{leg.get("from", "")}→{leg.get("to", "")}  {compass} {bearing}°')
    for i, value in enumerate(rows[:12]):
        text(55+(i % 3)*285, 555+(i//3)*37, value, blue, 23, "start")
    parts.append('</svg>')
    return "".join(parts)
