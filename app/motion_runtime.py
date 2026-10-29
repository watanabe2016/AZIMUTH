"""动效调度与可测量的控件反馈：一处属性只有一个驱动者。"""
from __future__ import annotations

import functools
import math
import time
import weakref
from collections import Counter, deque
from shiboken6 import isValid

from PySide6.QtCore import QObject, QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget
import theme as T
from ui_kit import animations_enabled


def cubic(t):
    """展开与关闭共用的对称三次曲线。"""
    return 4 * t * t * t if t < .5 else 1 - (-2 * t + 2) ** 3 / 2


def follow(value, target, tightness, dt):
    """时间步长决定响应，30、60、120 帧使用相同手感。"""
    return value + (target - value) * (1 - math.exp(-tightness * max(0, dt)))


class MotionClock(QObject):
    """所有新原生反馈共享精确时钟，空闲、最小化及隐藏后停止。"""
    def __init__(self, app):
        super().__init__(app)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        screen = app.primaryScreen()
        hz = screen.refreshRate() if screen else 60
        self.hz = max(60, min(144, round(hz or 60)))
        self.timer.setInterval(max(6, round(1000 / self.hz)))
        self.timer.timeout.connect(self.advance)
        self.jobs = {}
        self.intervals = deque(maxlen=4096)
        self.costs = deque(maxlen=4096)
        self.paint_costs = deque(maxlen=4096)
        self.last = None
        self.seen = Counter()
        self.peaks = 0
        self._sampled_allowed = True
        self._preference_at = 0.0

    def allowed(self):
        now = time.monotonic()
        if now >= self._preference_at:
            self._sampled_allowed = animations_enabled()
            self._preference_at = now + .1
        return self._sampled_allowed

    def add(self, layer, duration, motion_id):
        self.remove(layer)
        self.jobs[id(layer)] = (weakref.ref(layer), time.monotonic(), max(.001, duration / 1000))
        self.seen[motion_id] += 1
        self.peaks = max(self.peaks, len(self.jobs))
        if not self.timer.isActive():
            self.last = None
            self.timer.start()

    def remove(self, layer):
        self.jobs.pop(id(layer), None)
        if not self.jobs:
            self.timer.stop()
            self.last = None

    def advance(self):
        started = time.monotonic()
        if self.last is not None:
            self.intervals.append((started - self.last) * 1000)
        self.last = started
        allowed = self.allowed()
        for key, (ref, origin, duration) in tuple(self.jobs.items()):
            layer = ref()
            try:
                if layer is None or not allowed or not layer.anchor.isVisible() or layer.anchor.window().isMinimized():
                    if layer is not None:
                        layer.finish()
                    self.jobs.pop(key, None)
                    continue
                progress = min(1.0, (started - origin) / duration)
                layer.progress = progress
                layer.update()
                if progress >= 1:
                    layer.finish()
                    self.jobs.pop(key, None)
            except RuntimeError:
                self.jobs.pop(key, None)
        self.costs.append((time.monotonic() - started) * 1000)
        if not self.jobs:
            self.timer.stop()
            self.last = None

    def probe(self):
        def percentile(values, fraction):
            ordered = sorted(values)
            return round(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))], 3) if ordered else 0
        return {"active": len(self.jobs), "peak_active": self.peaks, "refresh_hz": self.hz,
                "sample_count": len(self.intervals), "interval_p95_ms": percentile(self.intervals, .95),
                 "dispatch_p95_ms": percentile(self.costs, .95),
                 "paint_p95_ms": percentile(self.paint_costs, .95), "seen": dict(self.seen)}


def clock():
    app = QApplication.instance()
    if not hasattr(app, "_azimuth_motion_clock"):
        app._azimuth_motion_clock = MotionClock(app)
    return app._azimuth_motion_clock


class ResponseLayer(QWidget):
    """局部缓存反馈层，不抢鼠标，不替换阴影，不逐帧修改样式。"""
    def __init__(self, anchor):
        super().__init__(anchor)
        self.anchor = anchor
        self.recipe = "line"
        self.progress = 1.0
        self.previous = None
        self.current = None
        self.tracks = {}
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()

    def play(self, recipe, motion_id, previous=None, duration=620):
        if not self.anchor.isVisible() or not animations_enabled():
            return
        self.recipe = recipe
        self.previous = previous
        self.progress = 0.0
        if previous is not None:
            self.hide()
            self.current = self.anchor.grab()
        now = time.monotonic()
        self.tracks = {key:value for key,value in self.tracks.items()
                       if now - value[1] < value[2] and (previous is None or value[3] is None)}
        self.tracks[motion_id] = (recipe, now, duration / 1000, previous, self.current if previous is not None else None)
        # 同一控件共用绘制层和时钟，各反馈保留独立进度，避免后一项覆盖前一项。
        while len(self.tracks) > 8:
            self.tracks.pop(next(iter(self.tracks)))
        self.setGeometry(self.anchor.rect())
        self.show()
        self.raise_()
        remaining = max(value[2] - (now - value[1]) for value in self.tracks.values())
        clock().add(self, remaining * 1000, motion_id)

    def finish(self):
        clock().remove(self)
        self.progress = 1.0
        self.previous = self.current = None
        self.tracks.clear()
        self.hide()

    def hideEvent(self, event):
        clock().remove(self)
        super().hideEvent(event)

    def paintEvent(self, event):
        now = time.monotonic()
        for recipe, origin, duration, previous, current in tuple(self.tracks.values()):
            progress = min(1.0, (now - origin) / duration)
            if progress < 1:
                self._paint_track(recipe, progress, previous, current)

    def _paint_track(self, recipe, t, previous, current):
        started = time.monotonic()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        e = cubic(t)
        fade = min(1.0, t * 6) * min(1.0, (1 - t) * 4)
        color = QColor(T.P["err_fg"] if recipe == "error" else
                       T.P["ok_fg"] if recipe in ("check", "stamp") else T.C_BLUE)
        color.setAlphaF(.68 * max(0, fade))
        p.setPen(QPen(color, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        w, h = rect.width(), rect.height()
        if previous is not None and current is not None:
            p.setOpacity(1)
            p.drawPixmap(self.rect(), previous)
            if recipe == 'wipe':
                p.setClipRect(QRectF(0, 0, self.width() * e, self.height()))
                p.drawPixmap(self.rect(), current)
            elif recipe == 'flip':
                height = self.height() * (.88 + .12 * e)
                p.setOpacity(e)
                p.drawPixmap(QRectF(0, (self.height() - height) / 2, self.width(), height),
                             current, QRectF(current.rect()))
            else:
                p.setOpacity(e)
                p.drawPixmap(self.rect(), current)
            p.end()
            clock().paint_costs.append((time.monotonic() - started) * 1000)
            return
        if recipe in ("focus", "outline", "hover", "corner", "chips"):
            path = QPainterPath()
            path.addRoundedRect(rect, min(11, h / 3), min(11, h / 3))
            p.drawPath(path)
            if recipe == "chips":
                p.setBrush(color)
                p.drawEllipse(QPointF(rect.right() - 8, rect.top() + 8), 2 + 2 * fade, 2 + 2 * fade)
        elif recipe in ("press", "halo", "spark"):
            clip = QPainterPath()
            clip.addRoundedRect(rect, min(14, h / 3), min(14, h / 3))
            p.setClipPath(clip)
            center = rect.center()
            radius = (max(w, h) * .48) * e
            p.drawEllipse(center, radius, radius)
            if recipe == "spark":
                for index in range(6):
                    angle = index * math.tau / 6
                    a = center + QPointF(math.cos(angle), math.sin(angle)) * (radius + 4)
                    b = center + QPointF(math.cos(angle), math.sin(angle)) * (radius + 8)
                    p.drawLine(a, b)
        elif recipe in ("check", "stamp"):
            cx, cy = rect.right() - min(18, w / 4), rect.bottom() - min(14, h / 3)
            path = QPainterPath(QPointF(cx - 7, cy))
            path.lineTo(cx - 2, cy + 4)
            path.lineTo(cx + 8, cy - 6)
            p.setClipRect(QRectF(cx - 10, cy - 10, 22 * min(1, e * 2), 22))
            p.drawPath(path)
        elif recipe == "wave":
            for i in range(9):
                amplitude = min(12, h / 4) * abs(math.sin(i * .8 + t * math.tau)) * fade
                x = rect.center().x() + (i - 4) * 4
                p.drawLine(QPointF(x, rect.bottom() - 3), QPointF(x, rect.bottom() - 3 - amplitude))
        elif recipe in ("arc", "progress"):
            radius = max(4, min(22, w / 3, h / 3))
            box = QRectF(rect.right() - radius * 2, rect.bottom() - radius * 2, radius * 2, radius * 2)
            p.drawArc(box, 90 * 16, round(-360 * e * 16))
        elif recipe == "arrow":
            x = rect.right() - 12 + 3 * math.sin(t * math.pi)
            y = rect.center().y()
            p.drawLine(QPointF(x - 5, y - 4), QPointF(x, y))
            p.drawLine(QPointF(x, y), QPointF(x - 5, y + 4))
        elif recipe in ("error", "nudge"):
            p.drawLine(QPointF(rect.left() + 5, rect.bottom()), QPointF(rect.left() + w * e, rect.bottom()))
            p.drawLine(QPointF(rect.left() + 2, rect.top() + h * (1 - e)), QPointF(rect.left() + 2, rect.bottom()))
        elif recipe == "dot":
            p.setBrush(color)
            p.drawEllipse(QPointF(rect.right() - 8, rect.bottom() - 8), 3 * fade, 3 * fade)
        else:
            x = rect.left() + w * e
            length = w * .25 if recipe == "scan" else w * e
            p.drawLine(QPointF(max(rect.left(), x - length), rect.bottom()), QPointF(x, rect.bottom()))
        p.end()
        clock().paint_costs.append((time.monotonic() - started) * 1000)


def response(anchor, recipe, motion_id, previous=None, duration=620):
    if anchor is None or not isinstance(anchor, QWidget):
        return False
    layer = getattr(anchor, "_motion_response", None)
    if layer is None:
        layer = ResponseLayer(anchor)
        anchor._motion_response = layer
    layer.play(recipe, motion_id, previous, duration)
    return True


EVENTS = {"show": QEvent.Show, "focus": QEvent.FocusIn, "enter": QEvent.Enter,
          "press": QEvent.MouseButtonPress, "release": QEvent.MouseButtonRelease,
          "resize": QEvent.Resize, "key": QEvent.KeyPress, "leave": QEvent.Leave}


def resolve(owner, path):
    value = owner
    if path == "self":
        return value
    for part in path.split("."):
        if part.isdigit():
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            value = getattr(value, part)
    return value


class MotionScene(QObject):
    """把验收项绑定到实际事件，测试可以读取绑定与执行记录。"""
    def __init__(self, owner, entries):
        super().__init__(owner)
        self.owner = owner
        self.entries = entries
        self.bound = {}
        self.missing = {}
        self._events = {}
        self._last = {}
        self._revision = 0
        self.bind()
        owner.installEventFilter(self)

    def bind(self):
        for entry in self.entries:
            try:
                target = resolve(self.owner, entry["target"])
                old = self.bound.get(entry['id'])
                if old is target and isValid(target):
                    continue
                if old is not None:
                    self._events[old] = [value for value in self._events.get(old, ()) if value['id'] != entry['id']]
                if not isinstance(target, QWidget) or not isValid(target):
                    raise TypeError("目标不是控件")
                trigger = entry["trigger"]
                if trigger.startswith("method:"):
                    if not callable(getattr(self.owner, trigger[7:], None)):
                        raise AttributeError(trigger)
                elif trigger.startswith("signal:"):
                    getattr(target, trigger[7:]).connect(
                        lambda *args, current=entry: self.fire(current))
                else:
                    self._events.setdefault(target, []).append(entry)
                    target.installEventFilter(self)
                self.bound[entry["id"]] = target
                self.missing.pop(entry["id"], None)
            except (AttributeError, KeyError, IndexError, TypeError) as exc:
                self.missing[entry["id"]] = str(exc)

    def capture(self, method):
        captured = {}
        for entry in self.entries:
            if entry["trigger"] == "method:" + method and entry["recipe"] in ("change", "wipe", "flip"):
                target = self.bound.get(entry["id"])
                if target is not None and isValid(target) and target.isVisible() and target.width() * target.height() < 1_000_000:
                    layer = getattr(target, "_motion_response", None)
                    captured[entry["id"]] = target.grab()
                    if layer is not None:
                        layer.finish()
        return captured

    def method(self, name, before):
        if self.missing or name in ('_rebuild_body', '_reload_custom_themes', '_reload_clock_skins'):
            self.bind()
        for entry in self.entries:
            if entry["trigger"] == "method:" + name:
                self.fire(entry, before.get(entry["id"]))

    def fire(self, entry, previous=None):
        target = self.bound.get(entry["id"])
        if target is None or not isValid(target) or not target.isVisible():
            return
        now = time.monotonic()
        if now - self._last.get(entry["id"], -100) < .09:
            return
        self._last[entry["id"]] = now
        if entry["recipe"] == "native":
            clock().seen[entry["id"]] += 1
            return
        revision = self._revision
        def play():
            if revision == self._revision and target.isVisible():
                response(target, entry["recipe"], entry["id"], previous,
                         240 if entry['trigger'] == 'press' else
                         380 if entry['trigger'] in ('enter', 'focus') else 620)
        QTimer.singleShot(0, target, play)

    def stop(self):
        self._revision += 1
        for target in set(self.bound.values()):
            if not isValid(target):
                continue
            layer = getattr(target, "_motion_response", None)
            if layer is not None:
                layer.finish()

    def eventFilter(self, watched, event):
        kind = event.type()
        try:
            owner = self.owner
            events = self._events
        except AttributeError:
            return False
        if watched is owner and kind == QEvent.Hide:
            self.stop()
        for entry in self._events.get(watched, ()):
            if kind == EVENTS.get(entry["trigger"]):
                self.fire(entry)
        if kind == QEvent.Hide:
            layer = getattr(watched, "_motion_response", None)
            if layer is not None:
                layer.finish()
        return False


def motion_page(cls):
    """装饰器在业务函数前后采集视觉状态，业务参数与返回值原样传递。"""
    from motion_catalog import NATIVE
    entries = [entry for entry in NATIVE if entry["owner"] == cls.__name__]
    original_init = cls.__init__
    @functools.wraps(original_init)
    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.motion_scene = MotionScene(self, entries)
    cls.__init__ = initialize
    methods = {entry["trigger"][7:] for entry in entries if entry["trigger"].startswith("method:")}
    for name in methods:
        original = getattr(cls, name, None)
        if original is None:
            continue
        def wrap(function, method_name):
            @functools.wraps(function)
            def wrapped(self, *args, **kwargs):
                scene = getattr(self, "motion_scene", None)
                before = scene.capture(method_name) if scene is not None else {}
                result = function(self, *args, **kwargs)
                if scene is not None:
                    scene.method(method_name, before)
                return result
            return wrapped
        setattr(cls, name, wrap(original, name))
    return cls
