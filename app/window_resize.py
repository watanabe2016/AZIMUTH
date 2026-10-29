"""无边框窗口的系统级四角缩放热区。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtWidgets import QWidget


class CornerResizeHandle(QWidget):
    """优先交给窗口系统缩放；最大化时拖动则先恢复再从角点接续。"""

    def __init__(self, window, edges, cursor, size=16):
        super().__init__(window)
        self.window = window
        self.edges = edges
        self._drag_geometry = None
        self._drag_global = None
        self._restore_on_move = False
        self._native_resize = False
        self.setFixedSize(size, size)
        self.setCursor(cursor)
        self.setToolTip("拖动窗口角落调整大小")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        self._drag_global = event.globalPosition().toPoint()
        self._drag_geometry = None
        self._native_resize = False
        self._restore_on_move = (
            self.window.isMaximized() or self.window.isFullScreen())

        # 普通窗口优先使用系统缩放，保留 Windows 的贴靠和边缘吸附行为。
        if not self._restore_on_move:
            native_window = self.window.windowHandle()
            if (native_window is not None and
                    native_window.startSystemResize(self.edges)):
                self._native_resize = True
                event.accept()
                return

        if not self._restore_on_move:
            self._drag_geometry = QRect(self.window.geometry())
        event.accept()

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return

        point = event.globalPosition().toPoint()
        if self._restore_on_move:
            self._restore_at_drag_corner(point)
            event.accept()
            return
        if self._native_resize or self._drag_geometry is None:
            event.accept()
            return

        self._resize_from_delta(point - self._drag_global)
        event.accept()

    def _restore_at_drag_corner(self, point):
        """从最大化状态恢复时，让原先拖动的窗口角继续贴着鼠标。"""
        self._restore_on_move = False
        normal = QRect(self.window.normalGeometry())
        min_width = self.window.minimumWidth()
        min_height = self.window.minimumHeight()
        if (not normal.isValid() or normal.width() < min_width or
                normal.height() < min_height):
            screen = self.window.screen()
            available = (screen.availableGeometry() if screen is not None
                         else QRect(0, 0, max(960, min_width),
                                    max(720, min_height)))
            width = min(available.width(), max(min_width,
                         round(available.width() * .78)))
            height = min(available.height(), max(min_height,
                          round(available.height() * .78)))
            normal = QRect(available.x() + (available.width() - width) // 2,
                           available.y() + (available.height() - height) // 2,
                           width, height)

        if self.edges & Qt.Edge.LeftEdge:
            normal.moveLeft(point.x())
        elif self.edges & Qt.Edge.RightEdge:
            normal.moveRight(point.x())
        if self.edges & Qt.Edge.TopEdge:
            normal.moveTop(point.y())
        elif self.edges & Qt.Edge.BottomEdge:
            normal.moveBottom(point.y())

        self.window.showNormal()
        self.window.setGeometry(normal)
        self._drag_geometry = QRect(self.window.geometry())
        self._drag_global = point

    def _resize_from_delta(self, delta):
        start = self._drag_geometry
        dx, dy = delta.x(), delta.y()
        left, top = start.left(), start.top()
        width, height = start.width(), start.height()
        min_width = self.window.minimumWidth()
        min_height = self.window.minimumHeight()

        if self.edges & Qt.Edge.LeftEdge:
            width = max(min_width, width - dx)
            left = start.right() - width + 1
        elif self.edges & Qt.Edge.RightEdge:
            width = max(min_width, width + dx)
        if self.edges & Qt.Edge.TopEdge:
            height = max(min_height, height - dy)
            top = start.bottom() - height + 1
        elif self.edges & Qt.Edge.BottomEdge:
            height = max(min_height, height + dy)

        self.window.setGeometry(left, top, width, height)
        callback = getattr(self.window, "_on_corner_resize", None)
        if callable(callback):
            callback(QRect(self.window.geometry()))

    def mouseReleaseEvent(self, event):
        self._drag_geometry = None
        self._drag_global = None
        self._restore_on_move = False
        self._native_resize = False
        event.accept()


def create_corner_resize_handles(window, size=24):
    corners = (
        (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.CursorShape.SizeFDiagCursor),
        (Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.CursorShape.SizeBDiagCursor),
        (Qt.Edge.LeftEdge | Qt.Edge.BottomEdge, Qt.CursorShape.SizeBDiagCursor),
        (Qt.Edge.RightEdge | Qt.Edge.BottomEdge, Qt.CursorShape.SizeFDiagCursor),
    )
    return tuple(CornerResizeHandle(window, edges, cursor, size)
                 for edges, cursor in corners)


def sync_corner_resize_handles(window, handles, raise_handles=True):
    size = handles[0].width() if handles else 0
    positions = ((0, 0), (window.width() - size, 0),
                 (0, window.height() - size),
                 (window.width() - size, window.height() - size))
    for handle, (x, y) in zip(handles, positions):
        if handle.pos() != QPoint(x, y):
            handle.move(x, y)
        if raise_handles:
            handle.raise_()
