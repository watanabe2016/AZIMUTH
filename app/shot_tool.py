# -*- coding: utf-8 -*-
"""截图小工具 —— 全屏框选截图 + 大图查看

为什么要冻结画面再框选：
  直接在当前窗口上拉框，会把「半透明遮罩」也框进去，框到的内容跟看到的不一样。
  标准做法是先把屏幕抓成一张图铺满全屏，用户是在这张"照片"上框选，
  松手时从这张照片上裁 —— 所见即所得。

多屏处理：grabWindow(0) 在不同平台/版本上可能返回「整块虚拟桌面」，
  也可能只返回主屏。这里按实测尺寸自动判断，避免把图拉伸错位。
"""
from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QGuiApplication, QFont
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel

import theme as T
from logger import get_logger

log = get_logger()


class ShotOverlay(QWidget):
    """全屏框选：拖拽出一块区域即为截图结果"""

    picked = Signal(QPixmap)

    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self._from = None
        self._to = None
        self._hint = "拖拽框选要记录的区域　·　回车截取全屏　·　ESC 取消"
        self._setup()

    # ------------------------------------------------------------ 抓屏
    def _setup(self):
        screens = QGuiApplication.screens()
        if not screens:
            self._full = QPixmap()
            self._dim = QPixmap()
            self.resize(200, 200)
            return
        virt = screens[0].geometry()
        for s in screens[1:]:
            virt = virt.united(s.geometry())
        sc = QGuiApplication.primaryScreen()
        dpr = float(sc.devicePixelRatio() or 1.0)
        pm = sc.grabWindow(0)
        if pm.isNull():
            # 抓不到就退化成"截当前窗口"，至少不崩
            log.warning("全屏抓取失败，退化为窗口抓取")
            w = self.window() or sc
            pm = QPixmap()
        # 判断抓到的是整块虚拟桌面还是只有主屏
        if not pm.isNull() and pm.width() / max(dpr, 0.01) >= virt.width() * 0.98:
            self._geo = virt
        else:
            self._geo = sc.geometry()
        pm.setDevicePixelRatio(1.0)
        self._full = pm
        self._dim = self._dim_copy(pm)
        self.setGeometry(self._geo)

    @staticmethod
    def _dim_copy(pm):
        """原图压暗一份，作为框选时的背景"""
        if pm.isNull():
            return pm
        d = QPixmap(pm.size())
        d.fill(Qt.transparent)
        p = QPainter(d)
        p.drawPixmap(0, 0, pm)
        p.fillRect(d.rect(), QColor(8, 14, 26, 120))
        p.end()
        d.setDevicePixelRatio(1.0)
        return d

    # ------------------------------------------------------------ 交互
    def _ratio(self):
        """控件逻辑像素 → 抓图的设备像素"""
        sx = self._full.width() / float(max(1, self.width()))
        sy = self._full.height() / float(max(1, self.height()))
        return sx, sy

    def _sel(self):
        if self._from is None or self._to is None:
            return QRect()
        return QRect(self._from, self._to).normalized()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.RightButton:
            self.close()
            return
        self._from = ev.position().toPoint()
        self._to = self._from
        self.update()

    def mouseMoveEvent(self, ev):
        if self._from is not None:
            self._to = ev.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, ev):
        if self._from is None:
            return
        self._to = ev.position().toPoint()
        r = self._sel()
        if r.width() < 8 or r.height() < 8:
            # 误点一下不算数，把选框清掉让用户重来
            self._from = self._to = None
            self.update()
            return
        self._emit(r)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.close()
        elif ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._emit(self.rect())

    def _emit(self, rect):
        sx, sy = self._ratio()
        src = QRect(int(rect.x() * sx), int(rect.y() * sy),
                    max(1, int(rect.width() * sx)),
                    max(1, int(rect.height() * sy)))
        out = self._full.copy(src)
        out.setDevicePixelRatio(1.0)
        self.close()
        if not out.isNull():
            self.picked.emit(out)

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, ev):
        p = QPainter(self)
        if not self._dim.isNull():
            p.drawPixmap(self.rect(), self._dim)
        sel = self._sel()
        if sel.width() > 0 and sel.height() > 0:
            sx, sy = self._ratio()
            src = QRect(int(sel.x() * sx), int(sel.y() * sy),
                        max(1, int(sel.width() * sx)),
                        max(1, int(sel.height() * sy)))
            p.drawPixmap(sel, self._full, src)
            p.setPen(QPen(QColor(T.C_BLUE_HI), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(sel.adjusted(0, 0, -1, -1))
            # 尺寸标签
            lab = "%d × %d" % (src.width(), src.height())
            f = QFont()
            f.setPointSize(9)
            f.setBold(True)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(lab) + 14
            ty = sel.top() - 24 if sel.top() > 28 else sel.bottom() + 6
            p.fillRect(QRect(sel.left(), ty, tw, 20), QColor(16, 26, 46, 225))
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRect(sel.left(), ty, tw, 20),
                       Qt.AlignCenter, lab)
        # 顶部提示条
        p.setPen(Qt.NoPen)
        p.fillRect(QRect(0, 0, self.width(), 34), QColor(12, 20, 36, 205))
        p.setPen(QColor(235, 242, 255))
        f = QFont()
        f.setPointSize(10)
        p.setFont(f)
        p.drawText(QRect(16, 0, self.width() - 32, 34),
                   Qt.AlignVCenter | Qt.AlignLeft, self._hint)
        p.end()


class ShotViewer(QFrame):
    """点开备忘录里的截图看大图"""

    def __init__(self, path, title="", scale=1.0):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._drag = None
        self._path = path

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        card = QFrame()
        card.setObjectName("ShotCard")
        card.setStyleSheet(
            f"QFrame#ShotCard{{background:{T.P['shell']};"
            f"border-radius:14px;border:1px solid {T.P['glass_soft_edge']};}}")
        outer.addWidget(card)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 12)
        cl.setSpacing(8)

        bar = QHBoxLayout()
        cap = QLabel(title or "备忘录截图")
        cap.setStyleSheet(f"font-size:13px;font-weight:700;color:{T.P['ink']};"
                          f"background:transparent;")
        bar.addWidget(cap)
        bar.addStretch()
        tip = QLabel("ESC 关闭")
        tip.setStyleSheet(f"font-size:11px;color:{T.P['ink3']};"
                          f"background:transparent;")
        bar.addWidget(tip)
        cl.addLayout(bar)

        self.lb = QLabel()
        self.lb.setAlignment(Qt.AlignCenter)
        self.lb.setStyleSheet("background:transparent;")
        cl.addWidget(self.lb, 1)

        pm = QPixmap(path)
        if pm.isNull():
            self.lb.setText("截图读取失败")
        else:
            scr = QGuiApplication.primaryScreen()
            aw, ah = (scr.availableGeometry().width(),
                      scr.availableGeometry().height()) if scr else (1280, 800)
            mw, mh = int(aw * 0.82), int(ah * 0.82)
            w, h = pm.width(), pm.height()
            if w > mw or h > mh:
                pm = pm.scaled(mw, mh, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
                w, h = pm.width(), pm.height()
            self.lb.setPixmap(pm)
            self.resize(min(w + 64, aw - 60), min(h + 96, ah - 60))
        # 标题栏可拖动
        for wdg in (card, cap):
            wdg.mousePressEvent = self._press
            wdg.mouseMoveEvent = self._move
            wdg.mouseReleaseEvent = self._release
        self._center()

    def _center(self):
        scr = QGuiApplication.primaryScreen()
        if not scr:
            return
        a = scr.availableGeometry()
        self.move(int(a.x() + (a.width() - self.width()) / 2),
                  int(a.y() + (a.height() - self.height()) / 2))

    def _press(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _move(self, ev):
        if self._drag is not None:
            self.move(ev.globalPosition().toPoint() - self._drag)

    def _release(self, ev):
        self._drag = None

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.close()

    def mouseDoubleClickEvent(self, ev):
        self.close()
