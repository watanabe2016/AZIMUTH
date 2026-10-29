# -*- coding: utf-8 -*-
"""地面站教学页 —— 零基础看懂地面站。

内容参考《地面站教学》PPT 重新组织（不是照搬）：把"什么是地面站、
八大图形、方位角、角度/边长计算、航向角、起飞点、参数设置"整理成
一张可滚动的图文长页，示意图全部用 QPainter 按当前主题色现场绘制，
浅色 / 深色两套主题下都保证清晰可读。

为什么要现场画而不是放图片素材：
  示意图只有线条和文字，矢量画不糊、不占体积，还能跟着主题换色 ——
  深色模式下不至于出现一张刺眼的白底图。
"""

import math

from PySide6.QtCore import (Qt, QEvent, QPoint, QPointF, QRect, QRectF,
                            QPropertyAnimation, QEasingCurve, QSize,
                            QVariantAnimation, QTimer)
from PySide6.QtGui import (QPixmap, QPainter, QColor, QPen, QFont, QPolygonF,
                           QPalette, QIcon, QPainterPath)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QScrollArea, QFrame, QGridLayout, QSizePolicy,
                               QPushButton, QButtonGroup, QGraphicsOpacityEffect,
                               QStackedLayout, QCheckBox)

import theme as T
from anna_art import AnnaHeaderArt
from industrial_details import IndustrialPageFooter, IndustrialPageMeta
from ui_kit import (glass, attach_shadow, refresh_glass, card_in,
                    prepare_card_in, cancel_card_in, animations_enabled, Combo)
from flight_motion import FlightRouteCanvas
from lesson_motion import LessonConceptCanvas
from motion_polish import fold
from motion_runtime import motion_page

# 2 倍超采样：QLabel 上看到的逻辑尺寸不变，高分屏下线条依然锐利
SS = 2


def _pm(w, h):
    """建一张 w×h（逻辑）的透明画布，返回 (pixmap, painter)"""
    pm = QPixmap(int(w * SS), int(h * SS))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(SS, SS)
    return pm, p


def _finish(pm):
    pm.setDevicePixelRatio(SS)
    return pm


def _chevron_icon(direction):
    pm, painter = _pm(18, 18)
    _pen(painter, QColor(T.C_BLUE), 2.0)
    if direction == "left":
        painter.drawLine(QPointF(11.5, 3.5), QPointF(6.5, 9))
        painter.drawLine(QPointF(6.5, 9), QPointF(11.5, 14.5))
    else:
        painter.drawLine(QPointF(6.5, 3.5), QPointF(11.5, 9))
        painter.drawLine(QPointF(11.5, 9), QPointF(6.5, 14.5))
    painter.end()
    return _finish(pm)


def _font(px, weight=600):
    f = QFont(T.pick_font())
    f.setPixelSize(px)
    # PySide6 的 setWeight 只吃 QFont.Weight 枚举，不吃裸数字 ——
    # 直接传 int 会 TypeError（实测 700 炸过一次），这里做个映射。
    m = {400: QFont.Weight.Normal, 500: QFont.Weight.Medium,
         600: QFont.Weight.DemiBold, 700: QFont.Weight.Bold,
         800: QFont.Weight.ExtraBold, 900: QFont.Weight.Black}
    f.setWeight(m.get(int(weight), QFont.Weight.DemiBold))
    return f


# ================================================================ 示意图
# 统一的配色取法：全部从当前主题调色板拿，切主题重画即换色。
def _c_line():
    return QColor(T.P["ink"])


def _c_dim():
    return QColor(T.P["ink3"])


def _c_pt():
    return QColor(T.C_BLUE)


def _c_fill():
    # 航线内部填充：浅色下用淡蓝，深色下用半透明蓝，避免糊底
    if T.app_theme == "industrial":
        return QColor(0, 0, 0, 0)
    return QColor(26, 86, 196, 36) if T.mode == "light" \
        else QColor(59, 125, 232, 46)


def _c_hi():
    return QColor(T.C_AMBER)


def _pen(p, color, w=2.0, style=Qt.SolidLine):
    p.setPen(QPen(color, w, style, Qt.RoundCap, Qt.RoundJoin))


def _waypoint(p, x, y, label, r=6.5, fill=None):
    """航点：实心圆点 + 序号。坐标是画布逻辑坐标。"""
    p.setPen(Qt.NoPen)
    p.setBrush(fill or _c_pt())
    p.drawEllipse(QPointF(x, y), r, r)
    p.setPen(QPen(QColor("#ffffff"), 1.2))
    p.setFont(_font(int(r * 1.7), 700))
    p.drawText(QRectF(x - r, y - r, 2 * r, 2 * r), Qt.AlignCenter, label)


def _poly_fig(pts, order=None, w=210, h=150, pad=20, fill=True,
              closed=True):
    """通用航线示意图：pts 归一化坐标(0~1)，order 为绘制连线顺序。"""
    if T.app_theme == "anna" and (w, h, pad) == (210, 150, 20):
        w, h, pad = 440, 330, 50
    pm, p = _pm(w, h)
    pw, ph = w - 2 * pad, h - 2 * pad
    xy = [(pad + px * pw, pad + py * ph) for px, py in pts]
    if fill and closed:
        poly = QPolygonF([QPointF(x, y) for x, y in xy])
        p.setPen(Qt.NoPen)
        p.setBrush(_c_fill())
        p.drawPolygon(poly)
    _pen(p, _c_line(), 3.2 if T.app_theme == "anna" else 2.2)
    seq = order if order is not None else list(range(len(xy)))
    loop = seq + [seq[0]] if closed else seq
    for a, b in zip(loop, loop[1:]):
        p.drawLine(QPointF(*xy[a]), QPointF(*xy[b]))
    for i, (x, y) in enumerate(xy):
        fill = None
        if T.app_theme == "industrial":
            fill = QColor(T.P["accent_fg"] if i == 0 else T.P["ink"])
        _waypoint(p, x, y, str(i + 1),
                  r=13 if T.app_theme == "anna" else 6.5, fill=fill)
    p.end()
    return _finish(pm)


def fig_tri_eq():
    return _poly_fig([(0.5, 0.08), (0.92, 0.86), (0.08, 0.86)])


def fig_tri_right():
    return _poly_fig([(0.14, 0.10), (0.86, 0.86), (0.14, 0.86)])


def fig_trapezoid():
    # 按题卡方向编号：1 左上、2 左下、3 右下、4 右上。
    # 左边垂直、上下底平行，右腰向内收成直角梯形。
    return _poly_fig([(0.14, 0.12), (0.14, 0.88), (0.68, 0.88),
                      (0.94, 0.12)])


def fig_pentagon():
    # 凹五边形：4 号点是向内折入的顶点，点序与题卡保持一致。
    return _poly_fig([(0.14, 0.88), (0.14, 0.12), (0.94, 0.12),
                      (0.50, 0.50), (0.94, 0.88)])


def fig_butterfly():
    # 交叉四边形按题卡顺序 1→2→3→4：两条斜边交叉，左右边垂直。
    return _poly_fig([(0.16, 0.88), (0.84, 0.12), (0.84, 0.88),
                      (0.16, 0.12)])


def fig_hexagon():
    pts = []
    for i in range(6):
        a = math.radians(-90 + i * 60)
        pts.append((0.5 + 0.38 * math.cos(a), 0.5 + 0.44 * math.sin(a)))
    return _poly_fig(pts)


def fig_scan():
    # 扫描航线：蛇形"犁地"折线，不闭合
    return _poly_fig([(0.10, 0.18), (0.90, 0.18), (0.90, 0.50),
                      (0.10, 0.50), (0.10, 0.84), (0.90, 0.84)],
                     closed=False, fill=False)


def fig_circle():
    w, h = 210, 150
    pm, p = _pm(w, h)
    cx, cy, r = w / 2, h / 2, 56
    p.setPen(Qt.NoPen)
    p.setBrush(_c_fill())
    p.drawEllipse(QPointF(cx, cy), r, r)
    _pen(p, _c_line(), 2.2)
    p.drawEllipse(QPointF(cx, cy), r, r)
    # 四个航点均分圆周（上右下左）
    for i in range(4):
        a = math.radians(-90 + i * 90)
        _waypoint(p, cx + r * math.cos(a), cy + r * math.sin(a), str(i + 1))
    p.end()
    return _finish(pm)


SHAPES = [
    ("等边三角形", "三边相等，三个内角都是 60°", fig_tri_eq),
    ("直角三角形", "一个 90° 角，常考 45°/45°/90°", fig_tri_right),
    ("梯形", "一组对边平行，常考直角梯形", fig_trapezoid),
    ("五边形", "内角和 540°，留意 4 号内凹点", fig_pentagon),
    ("蝴蝶形", "航线交叉飞行，航点顺序别连错", fig_butterfly),
    ("六边形", "内角和 720°，正六边形每角 120°", fig_hexagon),
    ("扫描航线", "来回“犁地”式折线，注意往返方向", fig_scan),
    ("圆形", "沿圆弧飞行，航点均分圆周", fig_circle),
]


def fig_compass():
    """方位角罗盘：0° 正北顺时针，并画出 210° 示例航线。"""
    w = h = 240
    pm, p = _pm(w, h)
    cx, cy, R = w / 2, h / 2 + 2, 88
    _pen(p, _c_dim(), 1.4)
    p.drawEllipse(QPointF(cx, cy), R, R)
    # 十字与斜向刻度
    for ang, lab, deg in ((-90, "N", "0°"), (0, "E", "90°"),
                          (90, "S", "180°"), (180, "W", "270°")):
        a = math.radians(ang)
        x1, y1 = cx + (R - 10) * math.cos(a), cy + (R - 10) * math.sin(a)
        x2, y2 = cx + R * math.cos(a), cy + R * math.sin(a)
        _pen(p, _c_line(), 2.4)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        lx, ly = cx + (R + 17) * math.cos(a), cy + (R + 17) * math.sin(a)
        p.setPen(QPen(_c_line(), 1))
        p.setFont(_font(13, 800))
        p.drawText(QRectF(lx - 26, ly - 9, 52, 18), Qt.AlignCenter, lab)
        p.setFont(_font(10, 600))
        p.drawText(QRectF(lx - 26, ly + 8, 52, 14), Qt.AlignCenter, deg)
    # 210° 示例方向（南偏西 30°）
    a = math.radians(210 - 90)      # 数学角度：210° 方位 = 罗盘左下
    ex, ey = cx + (R - 16) * math.cos(a), cy + (R - 16) * math.sin(a)
    _pen(p, _c_hi(), 2.6, Qt.DashLine)
    p.drawLine(QPointF(cx, cy), QPointF(ex, ey))
    p.setBrush(_c_hi())
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(ex, ey), 4.5, 4.5)
    tx, ty = cx + (R - 42) * math.cos(a), cy + (R - 42) * math.sin(a)
    p.setPen(QPen(_c_hi(), 1))
    p.setFont(_font(12, 800))
    p.drawText(QRectF(tx - 34, ty - 20, 68, 16), Qt.AlignCenter, "210°")
    # 中心起飞点
    p.setPen(QPen(_c_line(), 1.4))
    p.setBrush(QColor("#ffffff") if T.mode == "light" else QColor("#0E1622"))
    p.drawEllipse(QPointF(cx, cy), 5, 5)
    p.end()
    return _finish(pm)


def fig_angles():
    """内角 / 外角示意：三角形一个顶点的内角与延长边形成的外角。"""
    w, h = 300, 200
    pm, p = _pm(w, h)
    A = QPointF(150, 26)          # 顶点（标内外角的那个）
    B = QPointF(44, 168)
    C = QPointF(262, 168)
    poly = QPolygonF([A, B, C])
    p.setPen(Qt.NoPen)
    p.setBrush(_c_fill())
    p.drawPolygon(poly)
    _pen(p, _c_line(), 2.2)
    p.drawPolyline(QPolygonF([B, C, A]))
    # 内角弧（在 A 处，夹 AB 与 AC）
    import math as _m
    a1 = _m.degrees(_m.atan2(B.y() - A.y(), B.x() - A.x()))
    a2 = _m.degrees(_m.atan2(C.y() - A.y(), C.x() - A.x()))
    r = 34
    rect = QRectF(A.x() - r, A.y() - r, 2 * r, 2 * r)
    _pen(p, _c_pt(), 2.2)
    p.drawArc(rect, int(-a2 * 16), int((a2 - a1) * 16) % 5760)
    p.setFont(_font(12, 700))
    p.setPen(QPen(_c_pt(), 1))
    mid = _m.radians((a1 + a2) / 2)
    p.drawText(QPointF(A.x() + (r + 8) * _m.cos(mid) - 12,
                       A.y() + (r + 14) * _m.sin(mid)), "内角")
    # 外角：延长 CA 到 D，DA 与 AB 的夹角
    D = QPointF(A.x() + (A.x() - C.x()) * 0.30,
                A.y() + (A.y() - C.y()) * 0.30)
    _pen(p, _c_dim(), 2.0, Qt.DashLine)
    p.drawLine(A, D)
    r2 = 52
    a3 = _m.degrees(_m.atan2(D.y() - A.y(), D.x() - A.x()))
    rect2 = QRectF(A.x() - r2, A.y() - r2, 2 * r2, 2 * r2)
    _pen(p, _c_hi(), 2.2)
    p.drawArc(rect2, int(-a3 * 16), int((a3 - a1) * 16))
    p.setPen(QPen(_c_hi(), 1))
    mid2 = _m.radians((a3 + a1) / 2)
    p.drawText(QPointF(A.x() + (r2 + 6) * _m.cos(mid2) - 10,
                       A.y() + (r2 + 12) * _m.sin(mid2)), "外角")
    for pt, lab in ((A, "1"), (B, "2"), (C, "3")):
        _waypoint(p, pt.x(), pt.y(), lab)
    p.setFont(_font(11, 600))
    p.setPen(QPen(_c_dim(), 1))
    p.drawText(QRectF(0, h - 22, w, 18), Qt.AlignCenter,
               "外角 = 180° − 内角")
    p.end()
    return _finish(pm)


def _edge_label(p, A, B, text, out=11.0, inward=None):
    """把边长标注放在 A→B 这条边的**中点外侧**。

    为什么必须机械算、不能手填坐标：老版本这里是 `("√2", (w/2+14, h/2-4))`
    这种硬编码 —— 图一改尺寸、顶点一动，标注就跟角标叠在一起（用户反馈的
    "显示异常"就是这个）。改成按法向自动偏移之后，顶点怎么摆都不会撞。

    ``inward`` 给一个"朝图形内部"的参考点：法向有两个方向，选**背离**它的
    那个（外侧），这样标注永远在图外面，不会盖住填充色和内部线条。
    """
    mx, my = (A.x() + B.x()) / 2.0, (A.y() + B.y()) / 2.0
    dx, dy = B.x() - A.x(), B.y() - A.y()
    ln = math.hypot(dx, dy) or 1.0
    # 边的法向（两个候选方向）
    nx, ny = -dy / ln, dx / ln
    if inward is not None:
        # 取背离 inward 的那一侧
        if (mx - inward.x()) * nx + (my - inward.y()) * ny < 0:
            nx, ny = -nx, -ny
    lx, ly = mx + nx * out, my + ny * out
    p.setFont(_font(11, 700))
    p.setPen(QPen(_c_dim(), 1))
    p.drawText(QRectF(lx - 22, ly - 9, 44, 18), Qt.AlignCenter, text)
    # 从边中点到标注引一条极短的虚线，明确"这个数说的是这条边本身"
    p.setPen(QPen(_c_dim(), 0.9, Qt.DotLine))
    p.drawLine(QPointF(mx + nx * 2.5, my + ny * 2.5), QPointF(lx, ly))


def _vertex_angle(p, V, text, to1, to2, r=22.0):
    """在某顶点画角弧 + 度数，文字沿**角平分线方向**摆。

    沿角平分线是唯一稳妥的做法：角度的两个边方向一改，文字自动跟着走；
    要是像老版本那样写死 `(pad+16, pad+14)`，换个三角形就压到别的标注上。
    """
    a1 = math.atan2(to1.y() - V.y(), to1.x() - V.x())
    a2 = math.atan2(to2.y() - V.y(), to2.x() - V.x())
    diff = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi     # 取劣弧
    rect = QRectF(V.x() - r, V.y() - r, 2 * r, 2 * r)
    start = min(a1, a1 + diff)
    span = abs(diff)
    _pen(p, _c_pt(), 1.8)
    p.drawArc(rect, int(-math.degrees(start) * 16),
              int(-math.degrees(span) * 16) % 5760)
    mid = a1 + diff / 2.0
    lx = V.x() + (r + 15) * math.cos(mid)
    ly = V.y() + (r + 15) * math.sin(mid)
    p.setFont(_font(11, 800))
    p.setPen(QPen(_c_pt(), 1))
    p.drawText(QRectF(lx - 24, ly - 9, 48, 18), Qt.AlignCenter, text)


def _tri_canvas(w, h, pad, verts):
    """把 (name -> QPointF) 画成三角形，返回 (pm, p, V)。

    只负责底图：填充 + 描边 + 顶点圆点。标注交给调用方按 ``V`` 里的
    坐标去算 —— 这样"摆哪儿"全由几何决定，跟图幅尺寸解耦。
    """
    pm, p = _pm(w, h)
    order = ["A", "B", "C"]
    poly = QPolygonF([verts[k] for k in order])
    p.setPen(Qt.NoPen)
    p.setBrush(_c_fill())
    p.drawPolygon(poly)
    _pen(p, _c_line(), 2.2)
    p.drawPolygon(poly)
    for k in order:
        p.setPen(Qt.NoPen)
        p.setBrush(_c_line())
        p.drawEllipse(verts[k], 3.0, 3.0)
    return pm, p, verts


def fig_tri_special(kind):
    """四种特殊三角形示意图：kind ∈ 45 / 60 / 3060 / 30120120

    每种都明确标出**三边比例**和**三个角**。老版本的毛病：
    图幅只有 190×168、标注位置硬编码，`30°` 角标和 `√3` 边长标注挤在
    同一小块地方，两个字块各宽 52px、中心只差 16px —— 必然重叠成一团。
    这里把图幅放到 236×206，标注全部按几何自动排布。

    ``pad`` 必须 ≥ 边标注的外偏距离 + 字块半宽（11 + 20 = 31），否则
    外侧标注会顶出画布（实测 pad=30 时 45° 图的左边「1」跑到 x=-6）。
    取 38 留一点余量，同时图形本身仍有 160×130 的可用跨度。
    """
    w, h = 236, 206
    pad = 38          # 留白要够：角标和边长标注都画在图形外侧

    if kind == "45":
        # 直角在左下，两直角边相等
        verts = {"A": QPointF(pad, h - pad),        # 直角顶点
                 "B": QPointF(w - pad, h - pad),    # 45°
                 "C": QPointF(pad, pad)}            # 45°
        inner = QPointF(w * 0.42, h * 0.42)
        pm, p, V = _tri_canvas(w, h, pad, verts)
        _vertex_angle(p, V["A"], "90°", V["B"], V["C"], r=20)
        _vertex_angle(p, V["B"], "45°", V["A"], V["C"], r=26)
        _vertex_angle(p, V["C"], "45°", V["A"], V["B"], r=26)
        _edge_label(p, V["A"], V["B"], "1", out=14, inward=inner)
        _edge_label(p, V["A"], V["C"], "1", out=14, inward=inner)
        _edge_label(p, V["B"], V["C"], "√2", out=14, inward=inner)

    elif kind == "60":
        verts = {"A": QPointF(w / 2.0, pad * 0.72),
                 "B": QPointF(w - pad, h - pad),
                 "C": QPointF(pad, h - pad)}
        inner = QPointF(w / 2.0, h * 0.62)
        pm, p, V = _tri_canvas(w, h, pad, verts)
        _vertex_angle(p, V["A"], "60°", V["B"], V["C"], r=24)
        _vertex_angle(p, V["B"], "60°", V["A"], V["C"], r=24)
        _vertex_angle(p, V["C"], "60°", V["A"], V["B"], r=24)
        _edge_label(p, V["A"], V["B"], "1", out=14, inward=inner)
        _edge_label(p, V["A"], V["C"], "1", out=14, inward=inner)
        _edge_label(p, V["B"], V["C"], "1", out=14, inward=inner)

    elif kind == "3060":
        # 30° 在左上，60° 在右下，直角在右上（题面常见画法）
        verts = {"A": QPointF(pad, pad),            # 30°
                 "B": QPointF(w - pad, h - pad),    # 60°
                 "C": QPointF(w - pad, pad)}        # 90°
        inner = QPointF(w * 0.55, h * 0.45)
        pm, p, V = _tri_canvas(w, h, pad, verts)
        _vertex_angle(p, V["A"], "30°", V["B"], V["C"], r=26)
        _vertex_angle(p, V["B"], "60°", V["A"], V["C"], r=26)
        _vertex_angle(p, V["C"], "90°", V["A"], V["B"], r=20)
        # 斜边 AB = 2（对直角），短直角边 AC = 1，长直角边 BC = √3
        _edge_label(p, V["A"], V["C"], "1", inward=inner)
        _edge_label(p, V["B"], V["C"], "√3", inward=inner)
        _edge_label(p, V["A"], V["B"], "2", inward=inner)

    else:
        # 30/30/120：顶角 120° 在上，两腰相等
        verts = {"A": QPointF(w / 2.0, pad * 0.72),   # 120°
                 "B": QPointF(pad, h - pad),          # 30°
                 "C": QPointF(w - pad, h - pad)}      # 30°
        inner = QPointF(w / 2.0, h * 0.60)
        pm, p, V = _tri_canvas(w, h, pad, verts)
        _vertex_angle(p, V["A"], "120°", V["B"], V["C"], r=26)
        _vertex_angle(p, V["B"], "30°", V["A"], V["C"], r=24)
        _vertex_angle(p, V["C"], "30°", V["A"], V["B"], r=24)
        _edge_label(p, V["A"], V["B"], "1", inward=inner)
        _edge_label(p, V["A"], V["C"], "1", inward=inner)
        _edge_label(p, V["B"], V["C"], "√3", inward=inner)

    p.end()
    return _finish(pm)


# ================================================================ 页面
class ResponsiveFigureLabel(QLabel):
    """按图形原比例填充教学图表区域。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_pixmap = QPixmap()
        self._keep_aspect = True

    def set_keep_aspect(self, enabled):
        self._keep_aspect = bool(enabled)
        self.setScaledContents(not self._keep_aspect)
        self._refresh_pixmap()

    def setPixmap(self, pixmap):
        self._source_pixmap = QPixmap(pixmap)
        self._refresh_pixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self):
        source = self._source_pixmap
        if source.isNull():
            return
        if not self._keep_aspect:
            QLabel.setPixmap(self, source)
            return
        available = self.contentsRect().size()
        if available.width() <= 0 or available.height() <= 0:
            return
        ratio = source.devicePixelRatioF()
        target = QSize(round(available.width() * ratio),
                       round(available.height() * ratio))
        fitted = source.scaled(target, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
        fitted.setDevicePixelRatio(ratio)
        QLabel.setPixmap(self, fitted)


class AnnaDiagramMotifLayer(QWidget):
    """知识图形画布边角的轻量点阵与色块。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)
        self.show()
        self.raise_()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        if T.app_theme != "anna":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        blue = QColor("#2788F4")
        blue.setAlpha(100 if T.mode == "light" else 130)
        painter.setPen(Qt.NoPen)
        for origin_x, origin_y, columns, rows in (
                (10, 10, 8, 5), (self.width() - 63, self.height() - 49, 7, 5)):
            for row in range(rows):
                for column in range(columns):
                    dot = QColor(blue)
                    dot.setAlpha(max(25, blue.alpha() - row * 13 - column * 7))
                    painter.setBrush(dot)
                    painter.drawEllipse(QRectF(origin_x + column * 7,
                                               origin_y + row * 7, 2.1, 2.1))
        yellow = QColor("#FFE84C")
        pink = QColor("#F64188")
        for x, y, size, color in (
                (52, 48, 22, yellow),
                (self.width() - 36, self.height() - 34, 16, pink)):
            mark = QPainterPath()
            mark.moveTo(x, y - size / 2)
            mark.lineTo(x + size / 2, y + size / 2)
            mark.lineTo(x - size / 2, y + size / 2)
            mark.closeSubpath()
            painter.setBrush(color)
            painter.drawPath(mark)
        painter.end()


@motion_page
class KnowledgePage(QWidget):
    """地面站基础知识：滚动长页 + 图文卡片。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards = []
        self._figures = []
        self._figure_cells = []
        self._chips = []
        self._handles_theme_stylesheet = True
        self._scaffold()
        self._fill()
        self._cache_alternate_diagrams()
        self._install_teaching_workspace()

    def _install_teaching_workspace(self):
        """三类入口先引导选择，再进入原有课程与工具工作台。"""
        from teaching_studio import TeachingStudio
        from teaching_hub import TeachingHub
        from PySide6.QtWidgets import QStackedWidget
        outer = self.layout()
        self.legacy_teaching = QWidget(self)
        legacy_layout = QVBoxLayout(self.legacy_teaching)
        legacy_layout.setContentsMargins(0, 0, 0, 0)
        while outer.count():
            item = outer.takeAt(0)
            if item.widget():
                legacy_layout.addWidget(item.widget())
            elif item.layout():
                legacy_layout.addLayout(item.layout())
        self.teaching_workspace = TeachingStudio(self)
        self.teaching_stack = QStackedWidget(self)
        self.teaching_stack.addWidget(self.teaching_workspace)
        self.teaching_stack.addWidget(self.legacy_teaching)
        self.teaching_hub = TeachingHub(self, self)
        self.teaching_stack.addWidget(self.teaching_hub)
        self.teaching_hub.chapterSelected.connect(self._open_hub_chapter)
        self.teaching_hub.toolSelected.connect(self._open_hub_tool)
        self.teaching_workspace.embedded_mode = True
        self.detail_navigation = QWidget(self)
        tabs = QHBoxLayout(self.detail_navigation)
        tabs.setContentsMargins(30, 14, 30, 0)
        from ui_kit import IconButton
        self.teaching_back = IconButton('', 'arrow_left', parent=self)
        self.teaching_back.setFixedSize(44,44)
        self.teaching_back.setToolTip('返回教学');self.teaching_back.setAccessibleName('返回教学')
        self.teaching_back.clicked.connect(lambda: self._select_teaching_view(2))
        self.teaching_breadcrumb = QLabel('', self)
        tabs.addWidget(self.teaching_back)
        tabs.addWidget(self.teaching_breadcrumb)
        tabs.addStretch(1)
        self.teaching_workspace.source_button.hide()
        self.teaching_v2_button = QPushButton('互动课程与工具', self)
        self.teaching_legacy_button = QPushButton('基础资料与原课程', self)
        self.teaching_v2_button.hide()
        self.teaching_legacy_button.hide()
        outer.addWidget(self.detail_navigation)
        outer.addWidget(self.teaching_stack, 1)
        self._select_teaching_view(2)

    def _open_hub_chapter(self, key):
        self.teaching_workspace.embedded_section = 'courses'
        self.teaching_workspace.select_chapter(key)
        self.teaching_breadcrumb.setText('课程 / ' + dict(__import__('teaching_scene_catalog').CHAPTERS)[key])
        self._select_teaching_view(0)
        self.teaching_workspace.replay()

    def _open_hub_tool(self, key):
        self.teaching_workspace.embedded_section = 'tools'
        self.teaching_workspace.select_topic(key)
        self.teaching_breadcrumb.setText('工具 / ' + {'heading':'航向','wind':'风向','avoidance':'避让点','angle':'夹角'}[key])
        self._select_teaching_view(0)
        self.teaching_workspace.replay()

    def _select_teaching_view(self, index, animate=True, play_intro=True):
        from workbench_ui import reveal_layout
        changed = self.teaching_stack.currentIndex() != index
        transition = None
        if changed:
            if index == 2 and play_intro:
                self.teaching_hub.prepare_intro()
            def change_view():
                self.detail_navigation.setVisible(index == 0)
                self.teaching_stack.setCurrentIndex(index)
            if animate and self.isVisible():
                transition = reveal_layout(self, change_view, background=T.P['shell'])
            else:
                change_view()
        self.teaching_workspace.player.set_visible(index == 0 and self.isVisible())
        self.detail_navigation.setVisible(index == 0)
        self.teaching_workspace._set_section_widgets('courses' if self.teaching_workspace._topic == 'courses' else 'tools')
        for i, button in enumerate((self.teaching_v2_button, self.teaching_legacy_button)):
            button.setStyleSheet(
                f'QPushButton{{background:{T.C_BLUE if i == index else T.P["panel_bg"]};'
                f'color:{"white" if i == index else T.C_INK};'
                f'border:1px solid {T.P["input_border"]};border-radius:10px;padding:6px 16px;}}')
        if index == 1:
            self.on_show()
        elif index == 2 and changed and play_intro and self.isVisible():
            self._play_teaching_intro_after(transition)

    def warm_up_teaching_menu(self):
        """在启动加载阶段预热教学菜单卡片快照。"""
        return self.teaching_hub.warm_up_menu()

    def prepare_teaching_menu(self):
        '''页面隐藏时只准备菜单状态，等页面显示后由主切页入口播放一次动画。'''
        self._select_teaching_view(2, animate=False, play_intro=False)
        self.teaching_hub.prepare_menu_entry()

    def show_teaching_menu(self):
        '''在当前教学页内返回入口菜单，先完成页面过渡再播放卡片入场。'''
        if (self.teaching_stack.currentIndex() == 2 and
                self.teaching_hub.stack.currentIndex() == 3):
            return
        from workbench_ui import reveal_layout
        self.cancel_intro()
        transition = reveal_layout(
            self, self.prepare_teaching_menu, background=T.P['shell'])
        self._play_teaching_intro_after(transition)

    def _play_teaching_intro_after(self, transition):
        if not self.isVisible():
            return
        if transition is None:
            QTimer.singleShot(0, self.teaching_hub.play_intro)
        else:
            transition.animation.finished.connect(self.teaching_hub.play_intro)

    def _cache_alternate_diagrams(self):
        """在窗口启动准备阶段预绘另一主题的示意图，换肤时只换缓存图。"""
        original = T.mode
        alternate = "dark" if original == "light" else "light"
        T.apply(alternate)
        try:
            for label, draw in self._figures:
                cache = label._knowledge_figure_cache
                cache[alternate] = draw()
        finally:
            T.apply(original)

    # -------------------------------------------------- 结构
    def _scaffold(self):
        """搭外层骨架：只建一次滚动容器。

        之前 theme_changed 直接重调 _build()，等于往布局里再塞一个
        QScrollArea —— 新旧两层内容叠在一起，深色模式下页面直接糊穿
        （实测 grab 出来整页灰板）。架子搭一次，换主题只换内容。
        """
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea>QWidget>QWidget{background:transparent;}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(scroll)
        self.scroll = scroll
        self.shape_nav = QWidget(self)
        self.shape_nav.setObjectName("KnowledgeShapeNav")
        self.shape_nav_layout = QHBoxLayout(self.shape_nav)
        self.shape_nav_layout.setContentsMargins(20, 0, 20, 10)
        self.shape_nav_layout.setSpacing(6)
        outer.addWidget(self.shape_nav)
        self.industrial_footer = IndustrialPageFooter(self)
        self.industrial_footer.setVisible(T.app_theme == "industrial")
        outer.addWidget(self.industrial_footer)

    def theme_changed(self, pref=None):
        """原位更新教学长页的颜色与示意图，保留滚动位置和卡片实例。"""
        if hasattr(self, 'teaching_workspace'):
            self.teaching_workspace.theme_changed()
            self._select_teaching_view(self.teaching_stack.currentIndex())
        industrial = T.app_theme == "industrial"
        anna = T.app_theme == "anna"
        self.industrial_header.setVisible(industrial)
        self.industrial_header.update()
        self.industrial_footer.setVisible(industrial)
        self.industrial_footer.update()
        self.chiyo_art_slot.setStyleSheet(
            "QFrame#KnowledgeChiyoArtFrame{background:transparent;border:0px;}")
        self.chiyo_header_note.setVisible(T.app_theme == "chiyo")
        self.chiyo_header_note.update()
        self.anna_header_art.setVisible(anna)
        self.anna_header_art.refresh_art()
        self.page_title.setVisible(not anna)
        self.page_subtitle.setVisible(not anna)
        self._lesson_root_layout.setContentsMargins(
            28 if anna else 30, 12 if anna else 22,
            28 if anna else 30, 18)
        self.chiyo_dialogue.setVisible(T.app_theme == "chiyo")
        self.chiyo_dialogue.update()
        self.chiyo_dialogue.raise_()
        self.chiyo_figure_note.setVisible(T.app_theme == "chiyo")
        self.chiyo_figure_note.update()
        self.chiyo_figure_note.raise_()
        for card in self._cards:
            refresh_glass(card)
        for cell in self._figure_cells:
            self._style_figure_cell(cell)
        text_colors = {"heading": "ink", "page_title": "ink",
                       "page_subtitle": "ink2", "card_subtitle": "ink2",
                       "figure_title": "ink", "figure_description": "ink3"}
        for label in self.findChildren(QLabel):
            role = getattr(label, "_knowledge_role", None)
            color_key = (label._knowledge_style[0] if role == "para"
                         else text_colors.get(role))
            if color_key is not None:
                if role in ("para", "card_subtitle"):
                    self._apply_rich_label_color(label, T.P[color_key])
                palette = label.palette()
                palette.setColor(QPalette.ColorRole.WindowText,
                                 QColor(T.P[color_key]))
                label.setPalette(palette)
                if role == "heading":
                    label.setStyleSheet(
                        f"font-size:20px;font-weight:700;color:{T.P['ink']};"
                        "background:transparent;")
                elif role == "page_title":
                    label.setStyleSheet(
                        f"font-size:{30 if industrial else 27}px;"
                        f"font-weight:800;color:{T.P['ink']};"
                        f"letter-spacing:0px;background:transparent;"
                        f"border-left:{'6px' if industrial else '0px'} solid {T.P['accent_fg']};"
                        f"padding-left:{'12px' if industrial else '0px'};")
        for label in self._chips:
            label.setStyleSheet(
                f"font-size:12px; font-weight:700; color:{label._chip_color};"
                f"background:{T.P['panel_bg']};"
                f"border:1px solid {T.P['glass_soft_edge']};"
                "border-radius:12px; padding:7px 14px;")
        for box in self._lesson_boxes:
            self._style_lesson_box(box)
        for label, draw in self._figures:
            image = label._knowledge_figure_cache.get(T.mode)
            if image is None:
                image = draw()
                label._knowledge_figure_cache[T.mode] = image
            label.setPixmap(image)
        for button, (_name, _desc, draw) in zip(self._shape_nav_buttons, SHAPES):
            button.setIcon(QIcon(draw()))
        self._style_feature()
        self._style_lesson_text()
        self._style_shape_nav()
        self.select_shape(getattr(self, "_shape_index", 0), animate=False)
        from motion_features import refresh_feature_controls
        refresh_feature_controls(self)
        self.scroll.verticalScrollBar().setStyleSheet(
            f"QScrollBar:vertical{{width:8px;background:transparent;margin:4px 2px;}}"
            f"QScrollBar::handle:vertical{{background:{T.P['scroll']};"
            "border-radius:4px;min-height:30px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}")
        self.update()
        if hasattr(self, 'teaching_hub'):
            self.teaching_hub.refresh_theme()
            self.teaching_back.refresh()
            self.teaching_breadcrumb.setStyleSheet(f'color:{T.P["ink"]};background:transparent;font-size:26px;font-weight:800;')

    def _intro_cards(self):
        viewport = self.scroll.viewport()
        visible = [card for card in self._cards
                   if not card.isHidden() and
                   QRect(card.mapTo(viewport, QPoint(0, 0)), card.size())
                   .intersects(viewport.rect())]
        return visible[:5]

    def prepare_intro(self):
        if hasattr(self, 'teaching_hub') and self.teaching_stack.currentIndex() == 2:
            self.teaching_hub.prepare_intro()
            return
        self._intro_prepared_cards = self._intro_cards()
        prepare_card_in(self._intro_prepared_cards)

    def play_intro(self):
        """每次进入教学页，让当前视口内的知识卡片依次浮入。"""
        if hasattr(self, 'teaching_hub') and self.teaching_stack.currentIndex() == 2:
            self.teaching_hub.play_intro()
            return
        cards = getattr(self, "_intro_prepared_cards", self._intro_cards())
        cards = cards or self._intro_cards()
        card_in(cards, step=70, ms=460, prepared=True)
        self._intro_prepared_cards = None

    def cancel_intro(self):
        if hasattr(self, 'teaching_hub'):
            self.teaching_hub.cancel_intro()
        cancel_card_in(self._intro_cards())
        self._intro_prepared_cards = None

    def _fill(self):
        self._figures = []
        self._figure_cells = []
        self._cards = []
        self._shape_nav_buttons = []
        self._lesson_boxes = []
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        self.scroll.setWidget(inner)
        page_layout = QVBoxLayout(inner)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        self.anna_header_art = AnnaHeaderArt(
            "地面站教学", "先理解图形，再掌握航线", self,
            page_key="teaching")
        self.anna_header_art.setVisible(T.app_theme == "anna")
        page_layout.addWidget(self.anna_header_art)
        content = QWidget(inner)
        content.setStyleSheet("background:transparent;")
        page_layout.addWidget(content, 1)
        root = QVBoxLayout(content)
        self._lesson_root_layout = root
        root.setContentsMargins(30, 22, 30, 18)
        root.setSpacing(12)

        heading = QLabel("地面站教学")
        heading.setObjectName("H1")
        self.page_title = heading
        heading._knowledge_role = "page_title"
        subtitle = QLabel("先理解图形，再掌握航线。")
        subtitle.setObjectName("Dim")
        self.page_subtitle = subtitle
        subtitle._knowledge_role = "page_subtitle"
        heading_box = QVBoxLayout()
        heading_box.setSpacing(3)
        heading_box.addWidget(heading)
        heading_box.addWidget(subtitle)
        heading_row = QHBoxLayout()
        heading_row.setSpacing(16)
        heading_row.addLayout(heading_box)
        heading_row.addStretch(1)
        self.industrial_header = IndustrialPageMeta(
            "GEOMETRY", "FOCUS", self)
        self.industrial_header.setVisible(T.app_theme == "industrial")
        heading_row.addWidget(self.industrial_header, 0, Qt.AlignVCenter)
        from chiyo_art import ChiyoMarginNote
        self.chiyo_header_note = ChiyoMarginNote(
            "多练一点，\n就多一分底气！", width=218, height=80)
        self.chiyo_header_note.setVisible(T.app_theme == "chiyo")
        heading_row.addWidget(self.chiyo_header_note, 0, Qt.AlignVCenter)
        root.addLayout(heading_row)

        index_row = QHBoxLayout()
        self.shape_kicker = QLabel()
        self.shape_kicker._knowledge_role = "lesson_meta"
        index_row.addWidget(self.shape_kicker)
        index_row.addStretch(1)
        self.shape_count = QLabel()
        self.shape_count._knowledge_role = "lesson_meta"
        index_row.addWidget(self.shape_count)
        index_row.addSpacing(10)
        self.shape_prev = QPushButton("‹")
        self.shape_next = QPushButton("›")
        self.shape_prev.setText("")
        self.shape_next.setText("")
        self.shape_prev.setIcon(QIcon(_chevron_icon("left")))
        self.shape_next.setIcon(QIcon(_chevron_icon("right")))
        self.shape_prev.setIconSize(QSize(14, 14))
        self.shape_next.setIconSize(QSize(14, 14))
        for button in (self.shape_prev, self.shape_next):
            button.setFixedSize(34, 34)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName("上一图形" if button is self.shape_prev else "下一图形")
        index_row.addWidget(self.shape_prev)
        index_row.addWidget(self.shape_next)
        root.addLayout(index_row)

        self.feature = QFrame()
        self.feature.setObjectName("KnowledgeFeature")
        self.feature.setMinimumHeight(420)
        self._cards.append(self.feature)
        feature_outer = QVBoxLayout(self.feature)
        feature_outer.setContentsMargins(0, 0, 0, 0)
        self.content_motion_layer = QWidget(self.feature)
        feature_row = QHBoxLayout(self.content_motion_layer)
        self.feature_row = feature_row
        feature_row.setContentsMargins(18, 16, 18, 16)
        feature_row.setSpacing(20)
        feature_outer.addWidget(self.content_motion_layer)

        self.diagram_surface = QFrame()
        self.diagram_surface.setMinimumWidth(300)
        diagram_layout = QVBoxLayout(self.diagram_surface)
        diagram_layout.setContentsMargins(10, 8, 10, 8)
        self.hero_figure = ResponsiveFigureLabel()
        self.hero_figure.setAlignment(Qt.AlignCenter)
        self.hero_figure.setMinimumSize(280, 290)
        self.figure_stack = QStackedLayout()
        self.figure_stack.addWidget(self.hero_figure)
        self.route_demo = FlightRouteCanvas()
        self.figure_stack.addWidget(self.route_demo)
        self.concept_demo = LessonConceptCanvas()
        self.figure_stack.addWidget(self.concept_demo)
        diagram_layout.addLayout(self.figure_stack, 1)
        self.figure_motifs = AnnaDiagramMotifLayer(self.diagram_surface)
        feature_row.addWidget(
            self.diagram_surface, 7 if T.app_theme == "industrial" else 3)

        detail = QWidget()
        detail.setObjectName("KnowledgeFeatureDetails")
        self.feature_detail = detail
        detail.setMinimumWidth(260)
        detail_layout = QVBoxLayout(detail)
        self.feature_detail_layout = detail_layout
        detail_layout.setContentsMargins(8, 4, 6, 4)
        detail_layout.setSpacing(11)
        self.hero_title = QLabel()
        self.hero_title._knowledge_role = "lesson_heading"
        self.hero_meta = QLabel()
        self.hero_meta._knowledge_role = "lesson_meta"
        self.hero_desc = QLabel()
        self.hero_desc.setWordWrap(True)
        self.hero_desc._knowledge_role = "lesson_body"
        self.hero_focus = QLabel("观察重点")
        self.hero_focus._knowledge_role = "lesson_focus"
        self.hero_steps = QLabel("先核对图中的顶点编号，再按题目要求确认连接顺序。\n"
            "闭合图形应回到起始点；扫描航线按图示折返。")
        self.hero_steps.setWordWrap(True)
        self.hero_steps._knowledge_role = "lesson_body"
        from chiyo_art import ChiyoComicCell, ChiyoSpeechBubble
        self.chiyo_art_slot = ChiyoComicCell(
            detail, "KnowledgeChiyoArtFrame")
        self.chiyo_art_slot.setObjectName("KnowledgeChiyoArtFrame")
        self.chiyo_art_slot.setMinimumHeight(260)
        self.chiyo_art_slot.setMaximumHeight(300)
        self.chiyo_art_slot.setStyleSheet(
            "QFrame#KnowledgeChiyoArtFrame{background:transparent;border:0px;}")
        self.chiyo_dialogue = ChiyoSpeechBubble(
            "先认顶点，\n再连航线！", self.chiyo_art_slot,
            width=150, height=82)
        self.chiyo_dialogue.setVisible(T.app_theme == "chiyo")
        self.chiyo_figure_note = ChiyoMarginNote(
            "图形看清楚，\n航线不出错！", self.chiyo_art_slot,
            width=145, height=94, anchor="bottom-left")
        self.chiyo_figure_note.setVisible(T.app_theme == "chiyo")
        self.lesson_toggle = QPushButton("查看完整基础知识　⌄")
        self.lesson_toggle.setCursor(Qt.PointingHandCursor)
        self.lesson_toggle.setFocusPolicy(Qt.StrongFocus)
        detail_layout.addWidget(self.hero_title)
        detail_layout.addWidget(self.hero_meta)
        detail_layout.addWidget(self.hero_desc)
        detail_layout.addSpacing(8)
        detail_layout.addWidget(self.hero_focus)
        detail_layout.addWidget(self.hero_steps)
        demo_actions = QHBoxLayout()
        self.demo_toggle = QPushButton("航线演示")
        self.demo_replay = QPushButton("重播")
        for button in (self.demo_toggle, self.demo_replay):
            button.setMinimumHeight(38)
            button.setCursor(Qt.PointingHandCursor)
        self.demo_replay.setVisible(False)
        demo_actions.addWidget(self.demo_toggle)
        demo_actions.addWidget(self.demo_replay)
        demo_actions.addStretch(1)
        detail_layout.addLayout(demo_actions)
        self.demo_zone = QCheckBox("显示演示空域（非题目条件）")
        self.demo_zone.setVisible(False)
        detail_layout.addWidget(self.demo_zone)
        self.demo_caption = QLabel("点击航线演示，查看连接顺序和罗盘航向。")
        self.demo_caption.setWordWrap(True)
        self.demo_caption.setMinimumHeight(36)
        detail_layout.addWidget(self.demo_caption)
        self.route_demo.segment_changed.connect(self.update_demo_caption)
        self.demo_toggle.clicked.connect(self._toggle_route_demo)
        self.demo_replay.clicked.connect(self._replay_demo)
        self.demo_zone.toggled.connect(self.route_demo.set_zone)
        self.demo_mode = Combo()
        for text, key in (("航线顺序与罗盘", "route"), ("坐标定位", "coordinate"),
                          ("高度变化", "altitude"), ("侧风修正", "wind"),
                          ("过渡点与绕行", "avoidance")):
            self.demo_mode.addItem(text, key)
        self.demo_mode.setVisible(False)
        self.demo_mode.changed.connect(self._switch_demo_mode)
        detail_layout.addWidget(self.demo_mode)
        self.concept_demo.caption_changed.connect(self.update_demo_caption)
        detail_layout.addWidget(self.chiyo_art_slot, 1)
        detail_layout.addStretch(1)
        detail_layout.addWidget(self.lesson_toggle)
        feature_row.addWidget(
            detail, 3 if T.app_theme == "industrial" else 2)
        root.addWidget(self.feature, 1)

        self._feature_effect = QGraphicsOpacityEffect(self.content_motion_layer)
        self._feature_effect.setOpacity(1.0)
        self.content_motion_layer.setGraphicsEffect(self._feature_effect)
        self._shape_motion = QVariantAnimation(self)
        self._shape_motion.setDuration(520)
        self._shape_motion.setStartValue(0.0)
        self._shape_motion.setEndValue(1.0)
        self._shape_motion.setEasingCurve(QEasingCurve.InOutCubic)
        self._shape_motion.valueChanged.connect(self._update_shape_transition)
        self._shape_motion.finished.connect(self._finish_shape_transition)
        self._shape_transition_index = 0
        self._shape_transition_mode = "crossfade"
        self._shape_transition_start_opacity = 1.0
        self._shape_transition_swapped = False

        self.details_panel = QWidget()
        self.details_panel.setVisible(False)
        details_layout = QVBoxLayout(self.details_panel)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(12)
        for card in (self._card_intro(), self._card_compass(), self._card_angles(),
                     self._card_tri(), self._card_hdg(), self._card_takeoff(),
                     self._card_params(), self._card_next()):
            details_layout.addWidget(card)
        root.addWidget(self.details_panel)
        root.addStretch(1)

        self._shape_group = QButtonGroup(self)
        self._shape_group.setExclusive(True)
        for index, (name, _desc, draw) in enumerate(SHAPES):
            button = QPushButton(name)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.StrongFocus)
            button.setAccessibleName(name)
            button.setIcon(QIcon(draw()))
            button.setIconSize(QSize(56, 38))
            button.setMinimumHeight(78)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.clicked.connect(lambda _=False, i=index: self.select_shape(i))
            self._shape_group.addButton(button, index)
            self.shape_nav_layout.addWidget(button, 1)
            self._shape_nav_buttons.append(button)

        self.shape_prev.clicked.connect(
            lambda: self.select_shape((self._shape_index - 1) % len(SHAPES)))
        self.shape_next.clicked.connect(
            lambda: self.select_shape((self._shape_index + 1) % len(SHAPES)))
        self.lesson_toggle.clicked.connect(self._toggle_details)
        self.select_shape(0, animate=False)
        self._style_feature()
        self._style_shape_nav()

        from motion_features import install_teaching_controls
        install_teaching_controls(self)
        self._toggle_route_demo()

    def on_show(self):
        """进入教学页时默认演示，保留主动选择的静态模式。"""
        if self.route_demo.isVisible() and self.figure_stack.currentWidget() is self.route_demo:
            if not self.route_demo.paused and self.route_demo.motion.state() != QVariantAnimation.Running:
                self.route_demo.replay()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.on_show)

    def update_demo_caption(self, text):
        self.demo_caption.setText(text)

    def pause_demo(self):
        from motion_features import pause_demo
        pause_demo(self)

    def step_demo(self):
        from motion_features import step_demo
        step_demo(self)

    def set_demo_speed(self):
        from motion_features import set_demo_speed
        set_demo_speed(self)

    def reset_demo(self):
        from motion_features import reset_demo
        reset_demo(self)

    def complete_lesson(self):
        from motion_features import complete_lesson
        complete_lesson(self)

    def open_lesson_card(self):
        from motion_features import open_lesson_card
        open_lesson_card(self)

    def _style_feature(self):
        for button in (self.demo_toggle, self.demo_replay):
            button.setStyleSheet(
                f"QPushButton{{background:{T.P['panel_bg']};color:{T.C_BLUE};"
                "border:none;border-radius:12px;"
                "padding:6px 14px;font-size:13px;font-weight:600;}"
                f"QPushButton:hover{{background:{T.P['panel_hover']};}}")
        self.demo_caption.setStyleSheet(
            f"color:{T.P['ink2']};font-size:12px;background:transparent;")
        self.demo_zone.setStyleSheet(
            f"color:{T.P['ink2']};font-size:12px;background:transparent;")
        self.route_demo.update()
        self.concept_demo.update()
        self.demo_mode.refresh()
        if hasattr(self, "_lesson_root_layout"):
            tail_index = self._lesson_root_layout.count() - 1
            self._lesson_root_layout.setStretch(
                tail_index, 1 if T.app_theme == "industrial" else 0)
        self.feature_row.setStretch(
            0, 7 if T.app_theme == "industrial" else
            4 if T.app_theme == "anna" else 3)
        self.feature_row.setStretch(
            1, 3 if T.app_theme == "industrial" else
            3 if T.app_theme == "anna" else 2)
        self.feature_detail_layout.setContentsMargins(
            (20 if T.app_theme == "industrial" else 8), 4, 6, 4)
        self.feature_detail.setStyleSheet(
            f"QWidget#KnowledgeFeatureDetails{{background:transparent;"
            f"border-left:{'1px solid ' + T.P['glass_soft_edge'] if T.app_theme == 'industrial' else '0px'};}}")
        self.feature.setMinimumHeight(
            478 if T.app_theme == "anna" else
            450 if T.app_theme == "industrial" else 0)
        self.feature.setStyleSheet(
            f"QFrame#KnowledgeFeature{{background:{T.P['panel_bg']};"
            f"border:{1 if T.app_theme == 'industrial' else 2}px solid {T.P['glass_edge']};"
            f"border-radius:{4 if T.app_theme == 'industrial' else 8}px;}}")
        self.diagram_surface.setStyleSheet(
            f"background:{T.P['panel_soft']};border:1px solid {T.P['glass_soft_edge']};"
            "border-radius:5px;")
        self.figure_motifs.update()
        self.hero_figure.set_keep_aspect(T.app_theme != "industrial")
        self.shape_prev.setStyleSheet(self._nav_arrow_style())
        self.shape_next.setStyleSheet(self._nav_arrow_style())
        self.shape_prev.setIcon(QIcon(_chevron_icon("left")))
        self.shape_next.setIcon(QIcon(_chevron_icon("right")))
        self.lesson_toggle.setStyleSheet(
            f"QPushButton{{color:{T.C_BLUE};background:{T.P['panel_soft'] if T.app_theme == 'anna' else 'transparent'};"
            f"border:0;border-radius:8px;text-align:left;font-size:{20 if T.app_theme == 'anna' else 14}px;"
            "font-weight:700;padding:14px 18px;}"
            f"QPushButton:hover{{color:{T.C_BLUE_HI};}}"
            f"QPushButton:focus{{border-bottom:2px solid {T.C_BLUE};}}")

    @staticmethod
    def _nav_arrow_style():
        return (f"QPushButton{{color:{T.C_BLUE};background:{T.P['panel_bg']};"
                f"border:1px solid {T.P['input_border']};border-radius:10px;"
                "font-size:21px;font-weight:600;}"
                f"QPushButton:hover{{background:{T.P['panel_hover']};}}"
                f"QPushButton:pressed{{background:{T.P['panel_soft']};}}"
                f"QPushButton:focus{{border:2px solid {T.C_BLUE};}}")

    def _style_shape_nav(self):
        self.shape_nav.setStyleSheet("QWidget#KnowledgeShapeNav{background:transparent;}")
        default_theme = T.app_theme == "default"
        if default_theme:
            self.shape_nav_layout.setContentsMargins(8, 0, 8, 8)
            self.shape_nav_layout.setSpacing(4)
        else:
            self.shape_nav_layout.setContentsMargins(20, 0, 20, 10)
            self.shape_nav_layout.setSpacing(6)
        for index, button in enumerate(self._shape_nav_buttons):
            selected = index == getattr(self, "_shape_index", 0)
            background = (T.P["panel_bg"] if T.app_theme == "industrial" else
                          T.P["panel_soft"]) if selected else T.P["panel_bg"]
            border = T.C_BLUE if selected else T.P["glass_soft_edge"]
            color = T.C_BLUE if selected else T.P["ink2"]
            button.setIconSize(QSize(64, 46) if default_theme else QSize(56, 38))
            button.setStyleSheet(
                f"QPushButton{{min-height:{68 if default_theme else 78 if T.app_theme == 'industrial' else 32}px;"
                f"background:{background};color:{color};"
                f"border:none;border-radius:{4 if T.app_theme == 'industrial' else 10}px;padding:4px {1 if default_theme else 2}px;"
                f"font-size:{13 if default_theme else 12}px;font-weight:700;}}"
                f"QPushButton:hover{{border:none;"
                f"background:{T.P['panel_hover']};}}"
                f"QPushButton:pressed{{background:{T.P['panel_soft']};}}"
                f"QPushButton:focus{{border:none;background:{T.P['accent_soft']};}}")

    def _large_figure(self, draw):
        image = draw()
        if T.app_theme == "industrial":
            width, height = 620, 430
            canvas, painter = _pm(width, height)
            painter.fillRect(QRectF(0, 0, width, height),
                             QColor(T.P["panel_soft"]))
            line = QColor(T.P["glass_soft_edge"])
            dot = QColor(T.P["ink3"])
            dot.setAlpha(105)
            grid_line = QColor(T.P["glass_soft_edge"])
            grid_line.setAlpha(48)
            painter.setPen(QPen(grid_line, .7, Qt.DotLine))
            for value in range(0, 181, 30):
                x = 42 + (width - 42 - 14) * value / 180
                painter.drawLine(QPointF(x, 54), QPointF(x, height - 58))
            for value in range(0, 121, 30):
                y = height - 58 - (height - 112) * value / 120
                painter.drawLine(QPointF(42, y), QPointF(width - 14, y))
            painter.setPen(Qt.NoPen)
            painter.setBrush(dot)
            for x in range(16, width - 10, 18):
                for y in range(42, height - 48, 18):
                    painter.drawEllipse(QPointF(x, y), 1.0, 1.0)

            name = SHAPES[getattr(self, "_shape_index", 0)][0].upper()
            micro = QFont("Consolas")
            micro.setPixelSize(8)
            micro.setLetterSpacing(QFont.AbsoluteSpacing, 1.1)
            painter.setFont(micro)
            painter.setPen(QColor(T.P["ink3"]))
            painter.drawText(QRectF(12, 9, 220, 18),
                             Qt.AlignLeft | Qt.AlignVCenter,
                             f"GEOMETRY  /  {name}")
            painter.drawText(QRectF(width - 220, 9, 208, 18),
                             Qt.AlignRight | Qt.AlignVCenter,
                             "AZIMUTH  ·  CAAC  ·  TRAINING")

            axis_x, top_y, bottom_y = 42, 54, height - 58
            painter.setPen(QPen(line, .75))
            for value in range(0, 181, 6):
                x = axis_x + (width - axis_x - 14) * value / 180
                major = value % 30 == 0
                length = 14 if major else 6 if value % 12 == 0 else 3
                painter.drawLine(QPointF(x, bottom_y + 4),
                                 QPointF(x, bottom_y + 4 + length))
            painter.drawLine(QPointF(axis_x, bottom_y + 4),
                             QPointF(width - 14, bottom_y + 4))
            for value, label in ((0, "0"), (30, "30"), (60, "60"),
                                 (90, "90"), (120, "120"), (150, "150"),
                                 (180, "180")):
                x = axis_x + (width - axis_x - 14) * value / 180
                painter.setPen(QColor(T.P["ink3"]))
                painter.drawText(QRectF(x - 14, height - 26, 28, 14),
                                 Qt.AlignHCenter | Qt.AlignVCenter, label)

            for value in range(0, 121, 6):
                y = bottom_y - (bottom_y - top_y) * value / 120
                major = value % 30 == 0
                length = 10 if major else 5 if value % 12 == 0 else 2
                painter.setPen(QPen(line, 1.0 if major else .65))
                painter.drawLine(QPointF(axis_x - length, y),
                                 QPointF(axis_x, y))

            for value, label in ((120, "120°"), (90, "90°"), (60, "60°"),
                                 (30, "30°"), (0, "0°")):
                y = bottom_y - (bottom_y - top_y) * value / 120
                painter.setPen(QColor(T.P["ink3"]))
                painter.drawText(QRectF(2, y - 7, 34, 14),
                                 Qt.AlignRight | Qt.AlignVCenter, label)

            diagram = image.scaled(QSize(510 * SS, 330 * SS),
                                   Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
            diagram.setDevicePixelRatio(SS)
            painter.drawPixmap(QPointF(53, 47), diagram)
            current_angle = (60, 90, 45, 90, 30, 60, 0, 180)[
                getattr(self, "_shape_index", 0)]
            marker_x = axis_x + (width - axis_x - 14) * current_angle / 180
            painter.setPen(QPen(QColor(T.P["accent_fg"]), 1.6))
            painter.drawLine(QPointF(marker_x, bottom_y - 3),
                             QPointF(marker_x, bottom_y + 23))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(T.P["accent_fg"]))
            painter.drawEllipse(QPointF(marker_x, bottom_y - 5), 2.8, 2.8)
            painter.end()
            return _finish(canvas)
        size = QSize(440 * SS, 330 * SS)
        image = image.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        image.setDevicePixelRatio(SS)
        return image

    def _toggle_details(self):
        controller = getattr(self.details_panel, "_polish_fold", None)
        visible = not controller.opened if controller else self.details_panel.isHidden()
        fold(self.details_panel, visible)
        self.lesson_toggle.setText("收起完整基础知识　⌃" if visible
                                   else "查看完整基础知识　⌄")

    def _toggle_route_demo(self):
        if hasattr(self, 'teaching_hub'):
            self.figure_stack.setCurrentWidget(self.route_demo)
            self._replay_demo()
            return
        enabled = self.figure_stack.currentWidget() is self.hero_figure
        self.figure_stack.setCurrentWidget(self.route_demo if enabled else self.hero_figure)
        self.demo_toggle.setText("查看静态图" if enabled else "航线演示")
        self.demo_replay.setVisible(enabled)
        self.demo_mode.setVisible(enabled)
        self.demo_zone.setVisible(enabled)
        if enabled:
            self._switch_demo_mode()
        else:
            self.demo_caption.setText("点击航线演示，查看连接顺序和罗盘航向。")

    def _switch_demo_mode(self, *_):
        if self.figure_stack.currentWidget() is self.hero_figure:
            return
        mode = self.demo_mode.currentData()
        if hasattr(self, 'demo_wind_row'):
            self.demo_wind_row.setVisible(mode == 'wind')
        if hasattr(self, 'demo_pause'):
            self.demo_pause.setText('暂停')
        route = mode == "route"
        self.figure_stack.setCurrentWidget(self.route_demo if route else self.concept_demo)
        self.demo_zone.setVisible(route)
        if route:
            self.route_demo.set_zone(self.demo_zone.isChecked())
            self.route_demo.replay()
        else:
            self.concept_demo.set_mode(mode)

    def _replay_demo(self):
        canvas = self.figure_stack.currentWidget()
        if canvas is not self.hero_figure:
            canvas.replay()
            self.demo_pause.setText('暂停')

    def select_shape(self, index, animate=True):
        index %= len(SHAPES)
        if animate and index == getattr(self, "_shape_index", -1):
            if hasattr(self, 'teaching_hub'):
                self._replay_demo()
            return
        self._shape_index = index
        for i, button in enumerate(self._shape_nav_buttons):
            button.setChecked(i == index)
        self._style_shape_nav()
        self._style_lesson_text()
        if not animate or not animations_enabled():
            self._shape_motion.stop()
            self._apply_shape_content(index)
            self._feature_effect.setOpacity(1.0)
            return

        current = self._feature_effect.opacity()
        self._shape_motion.stop()
        self._shape_transition_index = index
        self._shape_transition_swapped = False
        if current >= 0.98:
            self._shape_transition_mode = "crossfade"
        else:
            # 中途改选时沿用当前透明度，立即更新到最后选择，再顺滑显现。
            self._shape_transition_mode = "fade_in"
            self._shape_transition_start_opacity = current
            self._apply_shape_content(index)
        self._shape_motion.setStartValue(0.0)
        self._shape_motion.setEndValue(1.0)
        self._shape_motion.start()

    def _apply_shape_content(self, index):
        name, desc, draw = SHAPES[index]
        self.shape_kicker.setText(f"图形基础　/　{name}")
        self.shape_count.setText(f"{index + 1} / {len(SHAPES)}")
        self.hero_title.setText(name)
        self.hero_desc.setText(desc)
        self.hero_meta.setText(self._shape_summary(index))
        self.hero_figure.setPixmap(self._large_figure(draw))
        self.route_demo.set_shape(index)
        if hasattr(self, 'course_progress'):
            from motion_features import refresh_course_progress
            refresh_course_progress(self)
        if self.figure_stack.currentWidget() is self.route_demo and self.isVisible():
            self.route_demo.replay()

    def _update_shape_transition(self, progress):
        progress = max(0.0, min(1.0, float(progress)))
        if self._shape_transition_mode == "fade_in":
            opacity = (self._shape_transition_start_opacity +
                       (1.0 - self._shape_transition_start_opacity) * progress)
        elif progress < 0.5:
            opacity = 1.0 - 0.65 * (progress * 2.0)
        else:
            if not self._shape_transition_swapped:
                self._apply_shape_content(self._shape_transition_index)
                self._shape_transition_swapped = True
            opacity = 0.35 + 0.65 * ((progress - 0.5) * 2.0)
        self._feature_effect.setOpacity(opacity)

    def _finish_shape_transition(self):
        if not self._shape_transition_swapped:
            self._apply_shape_content(self._shape_transition_index)
            self._shape_transition_swapped = True
        self._feature_effect.setOpacity(1.0)

    def _style_lesson_text(self):
        anna = T.app_theme == "anna"
        self.shape_kicker.setStyleSheet(
            f"font-size:{18 if anna else 13}px;color:{T.P['ink2']};background:transparent;")
        self.shape_count.setStyleSheet(
            f"font-size:{18 if anna else 13}px;font-weight:700;color:{T.P['ink2']};background:transparent;")
        self.hero_title.setStyleSheet(
            f"font-size:{42 if anna else 30}px;font-weight:800;color:{T.P['ink']};background:transparent;")
        self.hero_meta.setStyleSheet(
            f"font-size:{21 if anna else 15}px;font-weight:650;color:{T.P['ink2']};background:transparent;")
        self.hero_desc.setStyleSheet(
            f"font-size:{19 if anna else 14}px;color:{T.P['ink2']};background:transparent;")
        self.hero_steps.setStyleSheet(
            f"font-size:{18 if anna else 14}px;color:{T.P['ink']};background:transparent;")
        self.hero_focus.setStyleSheet(
            f"font-size:{20 if anna else 15}px;font-weight:750;color:{T.C_BLUE};background:transparent;")

    @staticmethod
    def _shape_summary(index):
        values = ("3 个顶点　·　3 条边", "3 个顶点　·　含一个直角",
                  "4 个顶点　·　一组对边平行", "5 个顶点　·　内角和 540°",
                  "4 个航点　·　连线交叉", "6 个顶点　·　内角和 720°",
                  "往返折线　·　按图示折返", "圆周航点　·　沿圆弧连接")
        return values[index]

    # -------------------------------------------------- 通用卡片
    def _card(self, title, sub=None):
        c = glass("Glass")
        l = QVBoxLayout(c)
        l.setContentsMargins(22, 18, 22, 20)
        l.setSpacing(9)
        t = QLabel(title)
        t.setObjectName("H2")
        t._knowledge_role = "heading"
        l.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setWordWrap(True)
            s.setTextFormat(Qt.RichText)
            s._knowledge_role = "card_subtitle"
            s._knowledge_markup = sub
            s.setStyleSheet(
                f"font-size:13px; color:{T.P['ink2']}; line-height:170%;"
                "background:transparent;")
            self._apply_rich_label_color(s, T.P["ink2"])
            l.addWidget(s)
        attach_shadow(c, blur=30, dy=8, alpha=40)
        self._cards.append(c)
        return c, l

    def _para(self, text, color_key="ink2", size=13, weight=500):
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setTextFormat(Qt.RichText)
        lb._knowledge_role = "para"
        lb._knowledge_style = (color_key, size, weight)
        lb._knowledge_markup = text
        lb.setStyleSheet(
            f"font-size:{size}px; font-weight:{weight};"
            f"color:{T.P[color_key]}; line-height:175%; background:transparent;")
        self._apply_rich_label_color(lb, T.P[color_key])
        return lb

    @staticmethod
    def _apply_rich_label_color(label, color):
        """给富文本根节点设主题色，避免 HTML 默认黑色覆盖控件调色板。"""
        markup = getattr(label, "_knowledge_markup", label.text())
        label.setText(f'<div style="color:{color};">{markup}</div>')

    @staticmethod
    def _style_lesson_box(box):
        box.setStyleSheet(
            f"background:{T.P['panel_soft']};"
            f"border:1px solid {T.P['glass_soft_edge']};"
            "border-radius:12px;")

    def _chip(self, text, color):
        lb = QLabel(text)
        lb.setAlignment(Qt.AlignCenter)
        lb._knowledge_role = "chip"
        lb._chip_color = color
        self._chips.append(lb)
        lb.setStyleSheet(
            f"font-size:12px; font-weight:700; color:{color};"
            f"background:{T.P['panel_bg']};"
            f"border:1px solid {T.P['glass_soft_edge']};"
            f"border-radius:12px; padding:7px 14px;")
        return lb

    def _fig_label(self, pm, draw=None):
        lb = QLabel()
        lb.setPixmap(pm)
        lb.setAlignment(Qt.AlignCenter)
        lb.setStyleSheet("background:transparent;")
        lb.setFixedSize(int(pm.width() / SS) + 8, int(pm.height() / SS) + 8)
        if draw is not None:
            lb._knowledge_figure_cache = {T.mode: pm}
            self._figures.append((lb, draw))
        return lb

    def _style_figure_cell(self, cell):
        if cell not in self._figure_cells:
            self._figure_cells.append(cell)
        cell.setStyleSheet(
            f"background:{T.P['panel_soft']};"
            f"border:1px solid {T.P['glass_soft_edge']};"
            "border-radius:14px;")

    # -------------------------------------------------- 卡片内容
    def _card_intro(self):
        c, l = self._card("什么是地面站？")
        l.addWidget(self._para(
            "地面站就是无人机的<b>地面指挥控制中心</b> —— 你在地面上用它"
            "规划航线、下发指令、监控飞机状态。考试用的地面站软件里，"
            "输入<b>方位角 + 距离</b>就能精准画出一条航线，多条航线组合"
            "成一个图形。"))
        l.addWidget(self._para(
            "<b>考试怎么考：</b>给一张题卡图形和一组条件，要求在 "
            "<b>6 分钟内</b>在地面站软件里画出航线、填好全部参数。", "ink"))
        row = QHBoxLayout()
        row.setSpacing(9)
        for txt, col in (("看懂图形", T.C_BLUE), ("算准角度", T.C_GREEN),
                         ("算准边长", T.C_AMBER), ("填对参数", T.C_RED)):
            row.addWidget(self._chip(txt, col))
        l.addLayout(row)
        return c

    def _card_shapes(self):
        c, l = self._card(
            "认识八大图形",
            "题卡图形基本都从这 8 种里出。认识它们，是拿到题的第一步。")
        grid = QGridLayout()
        self.shape_grid = grid
        self.shape_cells = []
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        for i, (name, desc, fn) in enumerate(SHAPES):
            cell = QFrame()
            self._style_figure_cell(cell)
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(10, 10, 10, 10)
            cl.setSpacing(4)
            fig = self._fig_label(fn(), fn)
            fig.setAlignment(Qt.AlignCenter)
            cl.addWidget(fig)
            nm = QLabel(name)
            nm._knowledge_role = "figure_title"
            nm.setAlignment(Qt.AlignCenter)
            nm.setStyleSheet(
                "font-size:13px; font-weight:800; color:palette(window-text);"
                "background:transparent;")
            ds = QLabel(desc)
            ds._knowledge_role = "figure_description"
            ds.setAlignment(Qt.AlignCenter)
            ds.setWordWrap(True)
            ds.setStyleSheet(
                "font-size:11px; color:palette(window-text);"
                "background:transparent;")
            cl.addWidget(nm)
            cl.addWidget(ds)
            grid.addWidget(cell, i // 4, i % 4)
            self.shape_cells.append(cell)
        l.addLayout(grid)
        self._reflow_shapes()
        return c

    def _reflow_shapes(self):
        """依据阅读区域宽度排列图形，避免小屏幕出现横向滚动。"""
        if not hasattr(self, "shape_grid"):
            return
        cols = max(1, min(4, (self.width() - 110) // 250))
        for index, cell in enumerate(self.shape_cells):
            self.shape_grid.addWidget(cell, index // cols, index % cols)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_shapes()

    def _card_compass(self):
        c, l = self._card(
            "方位角 —— 航线的语言",
            "方位角以<b>正北为 0°，顺时针</b>转一整圈 0~360°。"
            "题目里所有方向说法，都要先换算成方位角再往软件里填。")
        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(self._fig_label(fig_compass(), fig_compass))
        col = QVBoxLayout()
        col.setSpacing(8)
        col.addWidget(self._para(
            "<b>记住四个基准：</b>东 90°　·　南 180°　·　"
            "西 270°　·　北 0°"))
        col.addWidget(self._para(
            "<b>常见说法换算：</b><br>"
            "· 南偏西 30° = 180° + 30° = <b>210°</b><br>"
            "· 北偏东 45° = <b>45°</b><br>"
            "· 正南方向 = <b>180°</b>　·　正西方向 = <b>270°</b>"))
        col.addWidget(self._para(
            "<b>小技巧：</b>拿到题先在草稿纸上画个十字方位，把题目里的"
            "“偏”标出来再换算，比心算稳得多 —— 方向错了整道题全错。",
            "ink"))
        col.addStretch()
        body.addLayout(col, 1)
        l.addLayout(body)
        return c

    def _card_angles(self):
        c, l = self._card(
            "角度怎么算 —— 内角与外角",
            "图形的夹角是推算航线方位角的钥匙。")
        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(self._fig_label(fig_angles(), fig_angles))
        col = QVBoxLayout()
        col.setSpacing(8)
        col.addWidget(self._para(
            "<b>内角：</b>图形内部的夹角。<b>外角：</b>一条边延长后与"
            "相邻边形成的角，<b>外角 = 180° − 内角</b>。"))
        col.addWidget(self._para(
            "<b>内角和公式：</b>(n − 2) × 180°<br>"
            "· 三角形 180°　·　四边形 360°<br>"
            "· 五边形 540°　·　六边形 720°"))
        col.addWidget(self._para(
            "<b>怎么用：</b>等边三角形每个内角 60°；知道两个角，第三个"
            "角 = 180° − 两角之和。把每条边对应的方位角一个一个推出来，"
            "再按顺序输入软件。", "ink"))
        col.addStretch()
        body.addLayout(col, 1)
        l.addLayout(body)
        return c

    def _card_tri(self):
        c, l = self._card(
            "三角形边长 —— 学好它，任何图形都不怕",
            "这四种特殊三角形的三边比例要背熟，考试直接用。"
            "图上蓝字是角度、灰字是边长，边长都以短边为 1。")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        specs = [
            ("45° / 45° / 90°", "等腰直角：两直角边 1，斜边 √2 ≈ 1.414", "45"),
            ("60° / 60° / 60° 等边", "三边相等，都是 1（也被称等角三角形）", "60"),
            ("30° / 60° / 90°", "短直角边 1，长直角边 √3，斜边 2", "3060"),
            ("30° / 30° / 120°", "两腰 1，底边 √3 ≈ 1.732", "30120120"),
        ]
        for i, (name, desc, kind) in enumerate(specs):
            cell = QFrame()
            self._style_figure_cell(cell)
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(10, 10, 10, 10)
            cl.setSpacing(6)
            fig = self._fig_label(
                fig_tri_special(kind), lambda shape=kind: fig_tri_special(shape))
            fig.setAlignment(Qt.AlignCenter)
            cl.addWidget(fig)
            nm = QLabel(name)
            nm._knowledge_role = "figure_title"
            nm.setAlignment(Qt.AlignCenter)
            nm.setWordWrap(True)
            nm.setStyleSheet(
                "font-size:13px; font-weight:800; color:palette(window-text);"
                "background:transparent;")
            ds = QLabel(desc)
            ds._knowledge_role = "figure_description"
            ds.setAlignment(Qt.AlignCenter)
            ds.setWordWrap(True)
            ds.setStyleSheet(
                "font-size:11px; color:palette(window-text);"
                "background:transparent; line-height:150%;")
            cl.addWidget(nm)
            cl.addWidget(ds)
            grid.addWidget(cell, i // 2, i % 2)
            grid.setColumnStretch(i % 2, 1)
        l.addLayout(grid)
        l.addWidget(self._para(
            "<b>两个常数记牢：</b>√2 ≈ 1.414　·　√3 ≈ 1.732　"
            "—— 边长换算全靠它们。", "ink"))
        l.addWidget(self._para(
            "<b>反过来怎么用：</b>题目给了一条边的实际长度（比如「边长 "
            "300 米」），先把图上比例换成实际值再算。举例：30°/60°/90° 的"
            "斜边是短直角边的 <b>2 倍</b>，短边 300 米 → 斜边 600 米、"
            "长直角边 300×1.732 ≈ 519.6 米。<br>"
            "边长算出小数是正常的，<b>别急着以为算错了</b> —— 一般保留一位"
            "小数，或者看题目要求。", "warn_fg"))
        return c

    def _card_hdg(self):
        c, l = self._card(
            "航向角 —— 从这一点飞向那一点")
        l.addWidget(self._para(
            "<b>航向角</b>就是从当前点飞向下一点时，这条航线与正北的"
            "夹角（顺时针 0~360°）。地面上输入每条航线的航向角 + 距离，"
            "飞机就会按顺序飞完整个图形。"))
        l.addWidget(self._para(
            "<b>常见题型：</b>题目给“起飞点在图形正前方 20 米”“"
            "0 点到①点航向 100 度、距离 60”这类条件，要你先定起飞点到"
            "第一个航点的航向，再用图形的内角 / 外角逐段推算后面每条边"
            "的航向角。", "ink"))
        l.addWidget(self._para(
            "<b>解题口诀：</b>先定起点航向 → 沿图形逐点推内外角 → "
            "算一条填一条，别等全算完再一起填，容易乱。"))
        return c

    def _card_takeoff(self):
        c, l = self._card(
            "起飞点怎么定 —— 三种题型",
            "做题流程是先找起飞点，再按航点顺序画图形。起飞点有三种"
            "给法，看清楚题目用的是哪一种。")
        items = [
            ("① 前方规划型",
             "“机头正前方 20 米处规划任务航线” —— 起飞点就在图形旁边，"
             "直接按题卡图形输入航点即可。"),
            ("② 相对位置型（注意“位于 / 在 / 到”的区别）",
             "· “起飞点<b>在</b> 1 号点的正南方向 50 米处” —— 站在 "
             "1 号点看起飞点，正南 50 米<br>"
             "· “起飞点<b>位于</b> 1 号点 180° 方向 50 米” —— 同上，"
             "180° 就是正南<br>"
             "· “起飞点<b>到</b> 1 号点为 180° 50 米” —— 方向反过来："
             "站在起飞点看 1 号点"),
            ("③ 航点坐标位置型",
             "给经纬度，按公式换算成十进制度：<br>"
             "<b>DD = 度 + 分 ÷ 60 + 秒 ÷ 3600</b><br>"
             "例：25°5′35.6″ = 25 + 5/60 + 35.6/3600 ≈ "
             "<b>25.0932°</b>"),
        ]
        for title, txt in items:
            box = QFrame()
            self._lesson_boxes.append(box)
            self._style_lesson_box(box)
            bl = QVBoxLayout(box)
            bl.setContentsMargins(14, 10, 14, 12)
            bl.setSpacing(4)
            t = QLabel(title)
            t.setWordWrap(True)
            t.setStyleSheet(
                f"font-size:13px; font-weight:800; color:{T.C_BLUE};"
                f"background:transparent;")
            b = self._para(txt)
            bl.addWidget(t)
            bl.addWidget(b)
            l.addWidget(box)
        return c

    def _card_params(self):
        c, l = self._card(
            "题目条件与参数设置",
            "图形画完只是半道题，参数填错照样挂。逐项对号入座。")
        rows = [
            ("航线高度", "任务、避让与返航高度分别核对。障碍高度与航点高度的参照"
                         "必须一致；课件山高100m与60/80m航点存在条件疑问，须复核，不能仅凭两点数值判断净空。"),
            ("水平速度", "注意单位换算：10.8 km/h ÷ 3.6 = 3 m/s。"),
            ("垂直速度", "爬升 / 下降速度，通常比水平速度小。"),
            ("转弯模式", "常考“停止转弯”：到航点先停住再转向下一段。"),
            ("停留与朝向", "各点停留几秒、机头朝向（如“停留 60 秒，"
                           "机头朝 W”）。"),
            ("执行方式", "闭合航线常考“循环执行”；“航线不循环、①点开始”"
                         "就是飞一遍就停。"),
        ]
        for name, desc in rows:
            row = QHBoxLayout()
            row.setSpacing(12)
            tag = self._chip(name, T.C_BLUE)
            tag.setMinimumWidth(96)
            tag.setMaximumWidth(96)
            row.addWidget(tag)
            row.addWidget(self._para(desc), 1)
            l.addLayout(row)
        l.addWidget(self._para(
            "<b>易错点：</b>① 速度单位 km/h → m/s 要除以 3.6；"
            "② 题目给风向时看“顺 / 逆风”决定航点顺序方向，"
            "如“4-1 为逆风飞行”说明 4 飞向 1 是逆风，方向别画反。", "ink"))
        return c

    def _card_next(self):
        c, l = self._card("学完基础，去哪练？")
        l.addWidget(self._para(
            "去<b>「题库题卡」</b>随机抽一道题：先自己心算方位角和边长，"
            "再打开<b>题卡工作台</b>照着画一遍，最后对照“建议参数”检查。"
            "在题目页停留满 1 分钟会自动计入练习进度。", "ink"))
        return c
