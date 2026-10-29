"""数学画布：投影和拾取不改变米制模型，教学帧不重新计算布局或公式。"""
import math, time
from collections import deque
from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QPainter,QPen,QColor,QFont,QPainterPath,QPixmap
from PySide6.QtWidgets import QWidget
import theme as T
from teaching_math import Point,normalizeBearing
from motion_runtime import cubic


class TeachingCanvas(QWidget):
    bearingDragged=Signal(float)

    def __init__(self,player,parent=None):
        super().__init__(parent)
        self.player=player
        self.setMinimumSize(280,310)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.zoom=1.0;self.pan=QPointF();self.drag=None;self.drag_start=None
        self.bounds=(-100,-100,100,100)
        self.paint_costs=deque(maxlen=8192)
        self.frame_count=0
        self.original=QPixmap()
        self.player.changed.connect(self.rebuild)
        self.player.frame.connect(self.update)
        self.setAccessibleName('可缩放教学画布；滚轮缩放，拖动平移；罗盘可拖动指针')

    def rebuild(self):
        if not self.player.lesson:return
        points=[]
        for s in self.player.lesson.steps:
            for o in s.objects:
                points.extend(o.points)
                if o.kind=='circle':points.extend((Point(-o.value,-o.value),Point(o.value,o.value)))
        if points:
            self.bounds=(min(p.x for p in points),min(p.y for p in points),max(p.x for p in points),max(p.y for p in points))
        else:self.bounds=(-100,-100,100,100)
        image=next((o for o in self.player.current.objects if o.kind=='image'),None)
        if image:
            self.original=QPixmap(T.asset_path('teaching','cases',f'page-{int(image.value)}.png'))
        self.update()

    def reset_view(self):
        self.zoom=1;self.pan=QPointF();self.player.session.viewport={};self.update()

    def projection(self):
        x0,y0,x1,y1=self.bounds
        span=max(x1-x0,y1-y0,1)
        scale=max(1,min(self.width()-130,self.height()-130))/span*.85*self.zoom
        center=QPointF(self.width()/2,self.height()/2)+self.pan
        return scale,center,(x0+x1)/2,(y0+y1)/2

    def project(self,point):
        scale,center,x,y=self.projection()
        return center+QPointF((point.x-x)*scale,-(point.y-y)*scale)

    def paintEvent(self,event):
        start=time.perf_counter()
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        bg=QColor(T.P['glass']);painter.fillRect(self.rect(),bg)
        ink=QColor(T.C_INK);muted=QColor(T.C_INK_3);accent=QColor(('#ff839a' if T.app_theme=='chiyo' else '#8ab5ff') if T.mode=='dark' else '#f04464' if T.app_theme=='chiyo' else T.C_BLUE)
        grid=QColor(muted);grid.setAlpha(24)
        painter.setPen(QPen(grid,1))
        for x in range(20,self.width(),32):
            for y in range(20,self.height(),32):painter.drawPoint(x,y)
        painter.setFont(QFont(T.pick_font(),10))
        painter.setPen(muted)
        painter.drawText(QRectF(18,12,self.width()-36,28),Qt.AlignLeft,'东 / 北米制坐标 · 滚轮缩放 · 拖动平移')
        s=self.player.current
        if not s:painter.end();return
        progress=self.player.session.progress
        e=cubic(progress)
        palette={'task':accent,'muted':QColor(T.C_INK_3),'helper':QColor('#eeab2d'),'avoidance':QColor('#f04464'),'takeoff':QColor('#20b69b'),'wind':QColor('#20b69b')}
        compass=next((o for o in s.objects if o.kind=='compass'),None)
        if compass:self._compass(painter,compass,e,accent,ink,muted)
        altitudes=[o for o in s.objects if o.kind=='altitude']
        if altitudes:self._altitude(painter,altitudes,e,accent,ink)
        painter.setFont(QFont(T.pick_font(),10))
        for o in s.objects:
            if o.kind in ('compass','altitude'):continue
            color=palette.get(o.role,accent)
            painter.setPen(QPen(color,2 if o.role=='muted' else 3,Qt.DashLine if o.role=='helper' else Qt.SolidLine,Qt.RoundCap))
            pts=[self.project(pt) for pt in o.points]
            if o.kind=='image' and not self.original.isNull():
                target=QRectF(16,48,self.width()-32,self.height()-92)
                size=self.original.size();size.scale(target.size().toSize(),Qt.KeepAspectRatio)
                painter.drawPixmap(QRectF(target.center().x()-size.width()/2,target.center().y()-size.height()/2,size.width(),size.height()),self.original,QRectF(self.original.rect()))
            elif o.kind=='line' and len(pts)==2:
                a,b=pts
                t=e if o.id in s.highlights else 1
                tip=a+(b-a)*t
                painter.drawLine(a,tip)
                if o.role!='muted':
                    angle=math.atan2(b.y()-a.y(),b.x()-a.x())
                    for sign in (-1,1):painter.drawLine(tip,tip-QPointF(math.cos(angle+sign*.45)*11,math.sin(angle+sign*.45)*11))
                if o.label and t>.7:
                    painter.setPen(color)
                    painter.drawText(QRectF((a.x()+b.x())/2-90,(a.y()+b.y())/2-30,180,26),Qt.AlignCenter,o.label)
                if o.id in s.highlights and s.motion=='line':
                    # 解释性连线使用缓动，标明不是按真实速度仿真。
                    painter.setBrush(color);painter.drawEllipse(tip,5,5);painter.setBrush(Qt.NoBrush)
            elif o.kind=='plane':
                painter.save();painter.translate(pts[0]);painter.rotate(o.value)
                path=QPainterPath();path.moveTo(0,-18);path.lineTo(13,10);path.lineTo(0,5);path.lineTo(-13,10);path.closeSubpath()
                painter.setBrush(accent);painter.setPen(QPen(bg,2));painter.drawPath(path);painter.restore()
            elif o.kind=='point':
                point=pts[0]
                painter.setBrush(bg);painter.drawEllipse(point,7,7);painter.setBrush(Qt.NoBrush)
                painter.setPen(ink);painter.drawText(QRectF(point.x()-32,point.y()+9,64,28),Qt.AlignCenter,o.label)
            elif o.kind=='circle':
                scale,*_=self.projection();r=o.value*scale;center=self.project(o.points[0])
                rect=QRectF(center.x()-r,center.y()-r,r*2,r*2)
                painter.drawEllipse(rect)
                if s.motion=='circle':
                    # 圆周匀速轨迹采用线性相位，不套缓动曲线。
                    a=progress*2*math.pi
                    tip=center+QPointF(r*math.sin(a),-r*math.cos(a))
                    painter.setBrush(accent);painter.drawEllipse(tip,7,7);painter.setBrush(Qt.NoBrush)
                painter.setPen(ink);painter.drawText(rect,Qt.AlignCenter,o.label)
        if not s.objects:
            painter.setPen(accent);painter.setFont(QFont(T.pick_font(),20,QFont.Bold))
            painter.drawText(QRectF(35,80,self.width()-70,self.height()-130),Qt.AlignCenter|Qt.TextWordWrap,s.result or s.title)
        painter.setPen(muted);painter.setFont(QFont(T.pick_font(),9))
        painter.drawText(QRectF(18,self.height()-36,self.width()-36,24),Qt.AlignLeft,f'步骤 {self.player.session.step+1}/{len(self.player.lesson.steps)} · '+('连接顺序 / 推导演示' if s.motion!='circle' else '匀速圆周相位示意'))
        painter.end()
        self.frame_count+=1;self.paint_costs.append((time.perf_counter()-start)*1000)

    def _altitude(self,p,objects,e,accent,ink):
        baseline=self.height()-90;maximum=max(1,max(o.value for o in objects))
        p.setPen(QPen(QColor(T.C_INK_3),1));p.drawLine(50,baseline,self.width()-50,baseline)
        p.setFont(QFont(T.pick_font(),11));p.setPen(ink)
        p.drawText(QRectF(18,40,self.width()-36,28),Qt.AlignCenter,'高度变化示意 · 须使用一致的高度参照')
        for i,o in enumerate(objects):
            x=self.width()*(i+1)/(len(objects)+1)
            height=(self.height()-190)*o.value/maximum
            p.setBrush(accent);p.setPen(Qt.NoPen);p.drawRoundedRect(QRectF(x-22,baseline-height,44,height),8,8)
            p.setPen(ink);p.setBrush(Qt.NoBrush)
            p.drawText(QRectF(x-80,baseline-height-35,160,30),Qt.AlignCenter,f'{o.value:g} m')
            p.drawText(QRectF(x-80,baseline+12,160,30),Qt.AlignCenter,o.label)
        a,b=objects[0].value,objects[-1].value
        shown=a+(b-a)*e
        x=self.width()/2;y=baseline-(self.height()-190)*shown/maximum
        p.setPen(QPen(QColor('#20b69b'),2));p.setBrush(QColor('#20b69b'));p.drawEllipse(QPointF(x,y),6,6)

    def _compass(self,p,o,e,accent,ink,muted):
        center=QPointF(self.width()/2,self.height()/2+8)
        r=min(self.width(),self.height())*.32
        p.setPen(QPen(muted,1.5));p.drawEllipse(center,r,r)
        for deg in range(0,360,5):
            a=math.radians(deg)
            length=13 if deg%30==0 else 5
            p.drawLine(center+QPointF(math.sin(a)*(r-length),-math.cos(a)*(r-length)),center+QPointF(math.sin(a)*r,-math.cos(a)*r))
        p.setFont(QFont(T.pick_font(),11,QFont.Bold));p.setPen(ink)
        for degree,label in ((0,'北 N'),(90,'东 E'),(180,'南 S'),(270,'西 W')):
            a=math.radians(degree);pos=center+QPointF(math.sin(a)*(r+28),-math.cos(a)*(r+28))
            p.drawText(QRectF(pos.x()-38,pos.y()-14,76,28),Qt.AlignCenter,label)
        previous=o.value
        if self.player.session.step>0:
            old=self.player.lesson.steps[self.player.session.step-1]
            prev=next((x.value for x in old.objects if x.kind=='compass'),o.value)
            # 偏角遵从课程指定方向，反向步骤明确 +180°。
            delta=180 if self.player.current.id=='reverse' else (o.value-prev+180)%360-180
            previous=prev+delta*e
        angle=math.radians(previous)
        p.setPen(QPen(accent,4,Qt.SolidLine,Qt.RoundCap))
        p.drawLine(center,center+QPointF(math.sin(angle)*(r-20),-math.cos(angle)*(r-20)))
        arc=QRectF(center.x()-r*.62,center.y()-r*.62,r*1.24,r*1.24)
        p.setPen(QPen(accent,2));p.drawArc(arc,90*16,-round(normalizeBearing(previous)*16))
        p.setBrush(accent);p.drawEllipse(center,6,6);p.setBrush(Qt.NoBrush)
        p.setFont(QFont('Alibaba PuHuiTi 2.0',22,QFont.Bold));p.setPen(ink)
        p.drawText(QRectF(center.x()-80,center.y()+r*.38,160,44),Qt.AlignCenter,f'{normalizeBearing(previous):.1f}°')

    def mousePressEvent(self,event):
        if event.button()!=Qt.LeftButton:return
        self.player.pause();self.drag_start=event.position()
        self.drag='bearing' if self.player.lesson and self.player.lesson.id=='bearing' else 'pan'
        self.setCursor(Qt.ClosedHandCursor)
        if self.drag=='bearing':self._drag_bearing(event.position())

    def mouseMoveEvent(self,event):
        if not self.drag:return
        if self.drag=='bearing':self._drag_bearing(event.position())
        else:
            self.pan+=event.position()-self.drag_start;self.drag_start=event.position();self.update()

    def _drag_bearing(self,position):
        delta=position-QPointF(self.width()/2,self.height()/2+8)
        if abs(delta.x())+abs(delta.y())<5:return
        angle=normalizeBearing(math.degrees(math.atan2(delta.x(),-delta.y())))
        self.bearingDragged.emit(angle)

    def mouseReleaseEvent(self,event):
        self.drag=None;self.unsetCursor()
        self.player.session.viewport={'zoom':self.zoom,'pan':[self.pan.x(),self.pan.y()]}

    def wheelEvent(self,event):
        self.zoom=max(.4,min(5,self.zoom*math.exp(event.angleDelta().y()/1200)))
        self.player.session.viewport={'zoom':self.zoom,'pan':[self.pan.x(),self.pan.y()]}
        self.update();event.accept()
