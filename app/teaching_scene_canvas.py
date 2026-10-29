"""教学场景矢量画布：单一时钟、缓存底图、角度与画面同源。"""
import math,time,re
from PySide6.QtCore import Qt,QPointF,QRectF,Signal
from PySide6.QtGui import QPainter,QPen,QColor,QFont,QPainterPath,QPixmap,QPolygonF
import theme as T
from teaching_canvas import TeachingCanvas
from teaching_math import Point,normalizeBearing,azimuthBetweenPoints,reverseBearing


def smooth(t):
    """对称四次曲线只作用一次，教学步之间保留阅读停顿。"""
    return 8*t**4 if t<.5 else 1-((-2*t+2)**4)/2

def painted_color(value):
    """Qt 画笔不解析样式表 rgba 字符串，显式转换，避免画成黑块。"""
    color=QColor(value)
    if not color.isValid():
        parts=re.fullmatch(r'rgba\((\d+),(\d+),(\d+),([.\d]+)\)',str(value).replace(' ',''))
        if parts:
            r,g,b,a=parts.groups();color=QColor(int(r),int(g),int(b));color.setAlphaF(float(a))
        else:color=QColor('#243247' if T.mode=='dark' else '#ffffff')
    return color


class SceneCanvas(TeachingCanvas):
    parameterDragged=Signal(object)
    def __init__(self,player,parent=None):
        super().__init__(player,parent)
        self.background=QPixmap();self.background_key=None
        self.starts={};self.drawn_values={};self.last_objects=[]
        self._drag_projection=None;self._handles={};self._node_layout=False
        self.setAccessibleName('教学矢量图；航向工具可拖动指针；滚轮缩放，拖动画布平移')

    def _uses_node_layout(self):
        return self._node_layout

    def node_card_size(self):
        # 窄画布收紧卡片宽度，避免三列流程节点互相遮挡。
        return max(76.0,min(130.0,(self.width()-48)/3)),max(54.0,min(62.0,(self.height()-100)/3))

    def project(self,point):
        if not self._uses_node_layout():return super().project(point)
        x0,y0,x1,y1=self.bounds
        margin_x=min(100.0,max(24.0,self.width()*.14))
        margin_y=min(84.0,max(48.0,self.height()*.24))
        span_x=max(1.0,x1-x0);span_y=max(1.0,y1-y0)
        x=margin_x+(point.x-x0)*(self.width()-2*margin_x)/span_x
        y=self.height()-margin_y-(point.y-y0)*(self.height()-2*margin_y)/span_y
        center=QPointF(self.width()/2,self.height()/2)
        return center+(QPointF(x,y)-center)*self.zoom+self.pan

    def rebuild(self):
        super().rebuild()
        lesson=self.player.lesson
        self._node_layout=bool(lesson and any(o.kind=='node' for step in lesson.steps for o in step.objects))
        if self.player.lesson:
            values=self.player.lesson.parameters
            if self.player.current.motion=='rotate':
                span=150;self.bounds=(-span,-span,span,span)
            if self.player.lesson.id=='scene:30':
                scale=max(1,values['side']/100);self.bounds=(-120*scale,-120*scale,120*scale,120*scale)
        self.update()

    def _background(self):
        key=(self.width(),self.height(),T.mode,T.app_theme,T.P['glass'],T.C_INK_3)
        if key!=self.background_key:
            self.background_key=key;self.background=QPixmap(self.size());self.background.fill(painted_color(T.P['glass']))
            p=QPainter(self.background);color=QColor(T.C_INK_3);color.setAlpha(20);p.setPen(QPen(color,1))
            for x in range(24,self.width(),32):
                for y in range(24,self.height(),32):p.drawPoint(x,y)
            p.end()
        return self.background

    @staticmethod
    def _arrow(p,a,b,color,width=3):
        p.setPen(QPen(color,width,Qt.SolidLine,Qt.RoundCap));p.drawLine(a,b)
        if (b-a).manhattanLength()<5:return
        angle=math.atan2(b.y()-a.y(),b.x()-a.x())
        for sign in (-1,1):p.drawLine(b,b-QPointF(math.cos(angle+sign*.43)*10,math.sin(angle+sign*.43)*10))

    def _angular_value(self,obj,e):
        previous=self.starts.get(obj.id)
        from_interruption=previous is not None
        if previous is None and self.player.session.step>0:
            prev=self.player.lesson.steps[self.player.session.step-1]
            previous=next((o.value for o in prev.objects if o.id==obj.id and o.kind==obj.kind),obj.value)
        if previous is None:previous=obj.value
        if obj.kind=='windcompass' and self.player.session.step==1 and obj.id not in self.starts:
            previous=reverseBearing(self.player.lesson.parameters['heading'])
        delta=float(obj.label) if obj.kind=='compass' and obj.label and not from_interruption else (obj.value-previous+180)%360-180
        value=previous+delta*e
        self.drawn_values[obj.id]=normalizeBearing(value)
        return value

    def paintEvent(self,event):
        started=time.perf_counter();p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        clip=QPainterPath();clip.addRoundedRect(QRectF(self.rect()),14,14);p.setClipPath(clip)
        p.drawPixmap(0,0,self._background())
        current=self.player.current
        if current is None:p.end();return
        progress=self.player.session.progress;e=smooth(progress);self._handles={}
        colors={'task':QColor('#65a3ff' if T.mode=='dark' else '#1769dc'),
                'wind':QColor('#4fd4ba' if T.mode=='dark' else '#008d76'),
                'takeoff':QColor('#4fd4ba' if T.mode=='dark' else '#008d76'),
                'avoidance':QColor('#f18c6c' if T.mode=='dark' else '#ba5b16'),
                'helper':QColor('#f2bd58' if T.mode=='dark' else '#a06700'),
                'muted':QColor(T.C_INK_3)}
        ink=QColor(T.C_INK);muted=QColor(T.C_INK_3)
        p.setFont(QFont(T.pick_font(),10));p.setPen(muted)
        compass_objects=[o for o in current.objects if o.kind in ('compass','windcompass')]
        p.drawText(QRectF(18,12,self.width()-36,30),Qt.AlignLeft,
                   '正北 0° · 顺时针航向' if compass_objects else '北 ↑ · 东 → · 数学坐标等比显示')
        if compass_objects:self._instrument(p,compass_objects,e,colors,ink,muted)
        old=set()
        if self.player.session.step>0:old={o.id for o in self.player.lesson.steps[self.player.session.step-1].objects}
        altitudes=[o for o in current.objects if o.kind=='altitude']
        if altitudes:self._altitude(p,altitudes,e,colors['task'],ink)
        scale,*_=self.projection()
        self.last_objects=[]
        for o in current.objects:
            if o.kind in ('compass','windcompass','altitude'):continue
            color=colors.get(o.role,colors['task']);model_points=list(o.points);rotating_ray=False
            if o.id=='direction' and current.motion=='direction':
                center,end=o.points;length=math.hypot(end.x-center.x,end.y-center.y)
                heading=self.player.lesson.parameters['heading'];a=math.radians(heading*e)
                model_points=[center,Point(center.x+math.sin(a)*length,center.y+math.cos(a)*length)]
                rotating_ray=True
            if o.id=='map' and 'compass' in self.drawn_values:
                center,end=o.points;length=math.hypot(end.x-center.x,end.y-center.y)
                a=math.radians(self.drawn_values['compass'])
                model_points=[center,Point(center.x+math.sin(a)*length,center.y+math.cos(a)*length)]
                rotating_ray=True
            # 推导时未知射线随夹角转动；航点、底边和角弧共享相同中间状态。
            for angle_object in current.objects:
                if angle_object.kind!='arc' or not angle_object.id.startswith('arc') or angle_object.id not in current.highlights:continue
                branch_id='branch'+angle_object.id[3:]
                ray=next((v for v in current.objects if v.id==branch_id),None)
                if ray is None:continue
                center,end=ray.points;distance=math.hypot(end.x-center.x,end.y-center.y)
                heading=azimuthBetweenPoints(*angle_object.points)+angle_object.value*e;a=math.radians(heading)
                moving=Point(center.x+math.sin(a)*distance,center.y+math.cos(a)*distance)
                if o.kind!='arc':model_points=[moving if v==end else v for v in model_points]
                if o.id==branch_id:rotating_ray=True
            pts=[self.project(v) for v in model_points]
            if o.id=='map':
                # 罗盘旁保留独立航线小图，避免地图线覆盖中央仪表。
                center=QPointF(100,self.height()-125);a=math.radians(self.drawn_values.get('compass',0))
                pts=[center,center+QPointF(math.sin(a)*58,-math.cos(a)*58)]
                p.setPen(QPen(muted,1));p.drawLine(center,center-QPointF(0,70))
                p.drawText(QRectF(30,self.height()-210,140,24),Qt.AlignCenter,'航线同步 · 北 ↑')
            animated=o.id in current.highlights or o.id not in old
            t=e if animated and not rotating_ray else 1
            p.save()
            if o.kind!='line':p.setOpacity(t if o.id not in old else 1)
            p.setPen(QPen(color,2.5,Qt.DashLine if o.role in ('helper','muted') else Qt.SolidLine,Qt.RoundCap))
            p.setFont(QFont(T.pick_font(),10))
            if o.kind=='line':
                self.last_objects.append({'kind':'line','id':o.id,'points':tuple(model_points)})
                a,b=pts
                if o.id.startswith(('flow','info')):
                    # 通信线和流程连接止于卡片边界，不穿过标题文字。
                    delta=b-a
                    card_width,card_height=self.node_card_size();half_width=card_width/2;half_height=card_height/2
                    candidates=[half_width/abs(delta.x()) if abs(delta.x())>.001 else math.inf,
                                half_height/abs(delta.y()) if abs(delta.y())>.001 else math.inf]
                    start_cut=min(candidates);end_cut=min(candidates)
                    a,b=a+delta*start_cut,b-delta*end_cut
                tip=a+(b-a)*t
                if o.role=='muted':p.drawLine(a,tip)
                else:self._arrow(p,a,tip,color)
                if o.label and t>.72 and o.id!='map':
                    # 文字移至线段法向，给箭头与角弧留出空间。
                    delta=b-a;length=max(1,math.hypot(delta.x(),delta.y()))
                    offset=-24 if o.id=='known' and any(v.kind=='arc' for v in current.objects) else 22
                    mid=(a+b)/2+QPointF(-delta.y()/length*offset,delta.x()/length*offset)
                    p.drawText(QRectF(mid.x()-95,mid.y()-18,190,36),Qt.AlignCenter|Qt.TextWordWrap,o.label)
            elif o.kind=='point':
                pos=pts[0];p.setPen(QPen(color,2));p.setBrush(painted_color(T.P['panel_bg']));p.drawEllipse(pos,7,7)
                if self.player.lesson.id=='scene:41' and o.role=='avoidance':self._handles['avoidance']=pos
                p.setPen(ink);p.drawText(QRectF(pos.x()-68,pos.y()+11,136,34),Qt.AlignCenter|Qt.TextWordWrap,o.label)
            elif o.kind=='arc':
                center=pts[0];r=math.hypot(pts[1].x()-center.x(),pts[1].y()-center.y())
                start=azimuthBetweenPoints(o.points[0],o.points[1]);sweep=o.value*t
                rect=QRectF(center.x()-r,center.y()-r,2*r,2*r)
                p.setPen(QPen(color,3,Qt.SolidLine,Qt.RoundCap));p.drawArc(rect,round((90-start)*16),round(-sweep*16))
                a=math.radians(start+sweep/2);pos=center+QPointF(math.sin(a)*(r+19),-math.cos(a)*(r+19))
                p.drawText(QRectF(pos.x()-40,pos.y()-16,80,32),Qt.AlignCenter,f'{abs(sweep):.1f}°')
                self.last_objects.append({'kind':'arc','center':o.points[0],'start':start,'sweep':sweep,'target':o.value})
                if self.player.lesson.id in ('scene:49','scene:50') and o.id in ('given','arc0'):
                    angle=math.radians(start+sweep);self._handles['angle']=center+QPointF(math.sin(angle)*r,-math.cos(angle)*r)
            elif o.kind in ('square','polygon'):
                if o.kind=='polygon':
                    if current.motion=='rotate':
                        angle=math.radians(o.value*e);cs=math.cos(angle);sn=math.sin(angle)
                        pts=[self.project(Point(v.x*cs+v.y*sn,-v.x*sn+v.y*cs)) for v in o.points]
                    elif current.motion=='resize_triangle':
                        ratio=1+(o.value-1)*e;pts=[self.project(Point(v.x*ratio,v.y*ratio)) for v in o.points]
                fill=QColor(color);fill.setAlpha(35);p.setBrush(fill);p.setPen(QPen(color,2.5));p.drawPolygon(QPolygonF(pts))
                mid=sum(pts,QPointF())/len(pts);p.setPen(ink)
                p.drawText(QRectF(mid.x()-90,mid.y()-24,180,48),Qt.AlignCenter|Qt.TextWordWrap,o.label)
            elif o.kind in ('node','token'):
                pos=pts[0]
                card_width,card_height=self.node_card_size() if o.kind=='node' else (130.0,62.0)
                rect=QRectF(pos.x()-card_width/2,pos.y()-card_height/2,card_width,card_height);fill=painted_color(T.P['panel_bg'])
                p.setBrush(fill);p.setPen(QPen(color,1.5));p.drawRoundedRect(rect,12,12)
                p.setPen(ink);p.drawText(rect.adjusted(8,5,-8,-5),Qt.AlignCenter|Qt.TextWordWrap,o.label)
            elif o.kind=='text':
                pos=pts[0];p.drawText(QRectF(pos.x()-100,pos.y()-25,200,50),Qt.AlignCenter|Qt.TextWordWrap,o.label)
            elif o.kind=='circle':
                r=o.value*scale;c=pts[0];rect=QRectF(c.x()-r,c.y()-r,2*r,2*r)
                p.setPen(QPen(color,3,Qt.SolidLine,Qt.RoundCap));p.drawArc(rect,90*16,-round(360*progress*16))
                a=math.tau*progress;tip=c+QPointF(math.sin(a)*r,-math.cos(a)*r)
                p.setBrush(color);p.drawEllipse(tip,5,5);p.setPen(ink);p.drawText(rect,Qt.AlignCenter,o.label)
            p.restore()
        self._moving_markers(p,current,progress,colors)
        for key,pos in self._handles.items():
            color=colors['avoidance' if key=='avoidance' else 'wind' if key=='wind' else 'helper']
            fill=QColor(color);fill.setAlpha(24);p.setBrush(fill);p.setPen(QPen(color,1));p.drawEllipse(pos,16,16)
        p.setFont(QFont(T.pick_font(),9));p.setPen(muted)
        p.drawText(QRectF(18,self.height()-34,self.width()-36,22),Qt.AlignLeft,
                   {'scene:47':'拖动指针端点调整航向 · 空白处平移','scene:48':'拖动绿色来风点 · 吸附四种风向关系','scene:41':'拖动橙色 1 点调整避让位置','scene:49':'拖动角弧端点调整夹角','scene:50':'拖动角弧端点调整夹角'}.get(self.player.lesson.id,'可逐步观看 · 滚轮缩放 · 拖动平移'))
        p.end();self.frame_count+=1;self.paint_costs.append((time.perf_counter()-started)*1000)

    def _instrument(self,p,objects,e,colors,ink,muted):
        center=QPointF(self.width()/2,self.height()/2);r=min(self.width()-150,self.height()-145)*.43
        p.setPen(QPen(muted,1));p.setBrush(Qt.NoBrush);p.drawEllipse(center,r,r)
        for deg in range(0,360,5):
            a=math.radians(deg);size=12 if deg%45==0 else 5
            p.drawLine(center+QPointF(math.sin(a)*(r-size),-math.cos(a)*(r-size)),center+QPointF(math.sin(a)*r,-math.cos(a)*r))
        p.setFont(QFont(T.pick_font(),10,QFont.Bold))
        for deg,name in zip(range(0,360,45),('N 北','NE','E 东','SE','S 南','SW','W 西','NW')):
            a=math.radians(deg);loc=center+QPointF(math.sin(a)*(r+25),-math.cos(a)*(r+25));p.setPen(ink)
            p.drawText(QRectF(loc.x()-34,loc.y()-12,68,24),Qt.AlignCenter,name)
        for o in objects:
            p.save()
            if o.kind=='windcompass' and self.player.session.step==1 and o.id not in self.starts:p.setOpacity(e)
            angle=self._angular_value(o,e);a=math.radians(angle);color=colors[o.role]
            tip=center+QPointF(math.sin(a)*(r-20),-math.cos(a)*(r-20))
            if self.player.lesson.id=='scene:47' and o.kind=='compass':self._handles['bearing']=tip
            if self.player.lesson.id=='scene:48' and o.kind=='windcompass':self._handles['wind']=tip
            if o.kind=='compass':
                self._arrow(p,center,tip,color,4)
                if len(objects)==1:
                    p.setPen(QPen(colors['helper'],2));rr=r*.52
                    p.drawArc(QRectF(center.x()-rr,center.y()-rr,2*rr,2*rr),90*16,-round((angle if self.player.lesson.id=='scene:18' else normalizeBearing(angle))*16))
                    p.setFont(QFont(T.pick_font(),24,QFont.Bold));p.setPen(ink)
                    p.drawText(QRectF(center.x()-85,center.y()+r*.4,170,46),Qt.AlignCenter,f'{normalizeBearing(angle):.1f}°')
            else:
                # 标记在来向，运动箭头从来向穿过飞机指向吹去方向。
                self._arrow(p,tip,center-(tip-center)*.8,color,3)
                p.setBrush(color);p.drawEllipse(tip,6,6);p.setBrush(Qt.NoBrush);p.setPen(color);p.setFont(QFont(T.pick_font(),10))
                p.drawText(QRectF(center.x()-115,center.y()+r*.53,230,36),Qt.AlignCenter,f'来向 {normalizeBearing(angle):.1f}° · 吹向 {reverseBearing(angle):.1f}°')
            p.restore()

    def _moving_markers(self,p,s,t,colors):
        paths=[o for o in s.objects if o.kind=='line' and o.role!='muted']
        if s.motion=='informationflow':
            for o in paths:
                if o.id not in s.highlights:continue
                a,b=map(self.project,o.points);pos=a+(b-a)*t
                p.setBrush(colors['wind']);p.setPen(Qt.NoPen);p.drawEllipse(pos,4,4)
        elif s.motion=='speed_compare':
            for i,o in enumerate(paths):
                a,b=map(self.project,o.points);pos=a+(b-a)*min(1,t*(2 if i==0 else 1))
                p.setBrush(colors[o.role]);p.setPen(Qt.NoPen);p.drawEllipse(pos,7,7)
        elif s.motion in ('closed_once','loop_route','through','dwell') and paths:
            if s.motion=='loop_route':t=(t*self.player.lesson.parameters['loops'])%1 if t<1 else 1
            if s.motion=='dwell':phase=t*3;index=0 if phase<=2 else 1;local=min(1,phase) if index==0 else phase-2
            else:phase=t*len(paths);index=min(len(paths)-1,int(phase));local=phase-index
            a,b=map(self.project,paths[index].points);pos=a+(b-a)*min(1,local)
            p.setBrush(colors['wind']);p.setPen(Qt.NoPen);p.drawEllipse(pos,7,7)
            if s.motion=='dwell' and 1<phase<2:
                p.setPen(colors['helper']);p.drawText(QRectF(pos.x()-70,pos.y()-45,140,30),Qt.AlignCenter,f'停留 {self.player.lesson.parameters["dwell"]*(phase-1):.1f} s')

    def mousePressEvent(self,event):
        if event.button()!=Qt.LeftButton:return
        self.player.pause();self.drag_start=event.position()
        self.drag=next((key for key,pos in self._handles.items() if math.hypot((pos-event.position()).x(),(pos-event.position()).y())<=28),'pan')
        self._drag_projection=self.projection()
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self,event):
        if not self.drag:
            near=any(math.hypot((pos-event.position()).x(),(pos-event.position()).y())<=28 for pos in self._handles.values())
            self.setCursor(Qt.OpenHandCursor if near else Qt.ArrowCursor);return
        if self.drag=='pan':super().mouseMoveEvent(event);return
        if self.drag=='bearing':self._drag_bearing(event.position());return
        params=self.player.lesson.parameters
        if self.drag=='wind':
            delta=event.position()-QPointF(self.width()/2,self.height()/2)
            value=normalizeBearing(math.degrees(math.atan2(delta.x(),-delta.y())))
            from teaching_tool_math import WIND_OFFSETS
            mode=min(WIND_OFFSETS,key=lambda key:abs((value-params['heading']-WIND_OFFSETS[key]+180)%360-180))
            if mode!=params['wind_mode']:self.parameterDragged.emit({'wind_mode':mode})
            return
        scale,center,x,y=self._drag_projection;delta=event.position()-center
        point=Point(x+delta.x()/scale,y-delta.y()/scale)
        bearing=normalizeBearing(math.degrees(math.atan2(point.x,point.y)))
        if self.drag=='avoidance':
            self.parameterDragged.emit({'avoid_heading':bearing,'avoid_distance':max(1,math.hypot(point.x,point.y))})
        elif self.drag=='angle':
            reference=normalizeBearing(params['heading']+(180 if params['known_index']>=2 else 0))
            delta=(bearing-reference+180)%360-180
            self.parameterDragged.emit({'angle':max(.001,min(179.999,abs(delta))),'turn_side':'顺时针' if delta>=0 else '逆时针'})

    def mouseReleaseEvent(self,event):
        self._drag_projection=None;super().mouseReleaseEvent(event)

    def _drag_bearing(self,position):
        delta=position-QPointF(self.width()/2,self.height()/2)
        if delta.manhattanLength()<8:return
        self.bearingDragged.emit(normalizeBearing(math.degrees(math.atan2(delta.x(),-delta.y()))))
