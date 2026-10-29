"""参考 InteractiveHoverButton 的原生实现，保持业务按钮与键盘语义。"""
import math
from PySide6.QtCore import Qt,QEvent,QObject,QVariantAnimation,QEasingCurve,QRectF,QByteArray
from PySide6.QtGui import QPainter,QPainterPath,QColor,QPen
from PySide6.QtWidgets import QStyleOptionButton,QStyle
from PySide6.QtSvg import QSvgRenderer
import theme as T
import re


def color(value):
    """兼容主题中的 CSS rgba 颜色，避免 Qt 把无效颜色绘成黑色。"""
    if isinstance(value,str) and value.startswith('rgba'):
        parts=re.findall(r'[\d.]+',value)
        r,g,b=map(int,parts[:3]);a=float(parts[3])
        return QColor(r,g,b,round(a*255) if a<=1 else round(a))
    return QColor(value)

# 图标来自 better-icons 检索的 lucide:arrow-right（ISC）。
ARROW = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="none" stroke="#ffffff" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14m-7-7l7 7l-7 7"/></svg>'


class InteractiveButtonMotion(QObject):
    """只绘制按钮内部；连续进出从当前进度反向，空闲时无定时器。"""
    def __init__(self,button):
        super().__init__(button)
        self.button=button;self.progress=0.0
        self.arrow=QSvgRenderer(QByteArray(ARROW),self)
        self.motion=QVariantAnimation(self)
        self.motion.setEasingCurve(QEasingCurve.OutQuint)
        self.motion.valueChanged.connect(self._advance)
        button.installEventFilter(self)

    def _advance(self,value):
        if value is None:return
        self.progress=float(value);self.button.update()

    def animate(self,target):
        from ui_kit import animations_enabled
        self.motion.stop()
        if not animations_enabled():
            self._advance(0.0);return
        current=float(self.progress)
        # 重设端点时屏蔽旧时间位置触发的插值，再从当前画面反向。
        self.motion.blockSignals(True)
        self.motion.setDuration(max(100,round(480*abs(target-current))))
        self.motion.setStartValue(current);self.motion.setEndValue(float(target));self.motion.setCurrentTime(0)
        self.motion.blockSignals(False);self.motion.start()

    def eventFilter(self,button,event):
        kind=event.type()
        if kind==QEvent.Enter and button.isEnabled():self.animate(1.0)
        elif kind==QEvent.FocusIn and button.isEnabled():self.animate(1.0)
        elif kind in (QEvent.Leave,QEvent.FocusOut):self.animate(0.0)
        elif kind in (QEvent.Hide,QEvent.EnabledChange):
            self.motion.stop();self._advance(0.0)
        return False

    def paint(self):
        b=self.button
        if not b.isEnabled():return False
        p=QPainter(b);p.setRenderHint(QPainter.Antialiasing)
        rect=QRectF(b.rect()).adjusted(.5,.5,-.5,-.5)
        radius=min(10,rect.height()/2)
        path=QPainterPath();path.addRoundedRect(rect,radius,radius)
        primary=getattr(b,'_kind','default') in ('primary','danger') or b.isChecked() or getattr(b,'_checked_style',False)
        accent=QColor(T.P['err_fg'] if getattr(b,'_kind','')=='danger' else '#f04464' if T.app_theme=='chiyo' else T.C_BLUE)
        base=accent if primary else color(T.P['input_bg'])
        p.setPen(QPen(color(T.P['accent_fg'] if b.hasFocus() else T.P['input_border']),2 if b.hasFocus() else 1))
        p.setBrush(base);p.drawPath(path);p.setClipPath(path)
        t=self.progress
        p.setPen(Qt.NoPen);p.setBrush(accent.darker(112) if b.isDown() else accent.lighter(108) if primary else accent)
        # 蒙版从中心展开；静止状态不绘制装饰圆点，文字与图标不移动。
        reach=math.hypot(rect.width()/2,rect.height()/2)
        r=reach*t
        if t>0:p.drawEllipse(rect.center(),r,r)
        text=b.text().strip().replace('&','')
        fm=b.fontMetrics();tw=fm.horizontalAdvance(text)
        normal_icon=0 if b.icon().isNull() else b.iconSize().width()
        total=tw+(normal_icon+(8 if text else 0) if normal_icon else 0)
        start=(b.width()-total)/2
        p.setPen(QColor('#ffffff' if primary else T.P['ink']))
        if normal_icon:
            b.icon().paint(p,round(start),round((b.height()-normal_icon)/2),normal_icon,normal_icon)
            start+=normal_icon+8
        p.drawText(QRectF(start,0,tw+2,b.height()),Qt.AlignVCenter|Qt.AlignLeft,text)
        # 覆盖区内重绘白色内容，让渐进蒙版始终保持清晰对比。
        if t>0:
            mask=QPainterPath();mask.addEllipse(rect.center(),r,r)
            p.setClipPath(mask,Qt.IntersectClip);p.setPen(QColor('#ffffff'))
            if normal_icon:
                icon=b.icon().pixmap(normal_icon,normal_icon)
                tint=QPainter(icon);tint.setCompositionMode(QPainter.CompositionMode_SourceIn)
                tint.fillRect(icon.rect(),QColor('#ffffff'));tint.end()
                p.drawPixmap(round((b.width()-total)/2),round((b.height()-normal_icon)/2),icon)
            p.drawText(QRectF(start,0,tw+2,b.height()),Qt.AlignVCenter|Qt.AlignLeft,text)
        p.end();return True


def attach_interactive(button):
    if not hasattr(button,'_interactive_hover'):
        button._interactive_hover=InteractiveButtonMotion(button)
    return button._interactive_hover
