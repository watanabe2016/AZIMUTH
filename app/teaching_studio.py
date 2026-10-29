"""六章课程与三个独立工具；大画布、分步讲解和统一播放控制。"""
import copy
from PySide6.QtCore import Qt,QEvent,QTimer,Signal,Property,QEasingCurve,QPropertyAnimation,QRectF
from PySide6.QtGui import QPixmap,QKeySequence,QShortcut,QPainter,QColor,QPen,QLinearGradient,QFont
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QFrame,QLabel,
    QScrollArea,QComboBox,QDoubleSpinBox,QFormLayout,QListWidget,QDialog,QPushButton,QSlider,QApplication,QLineEdit,QAbstractSpinBox,QLayout,QSizePolicy)
import theme as T
import config as cfg
from teaching_controls import label,button
from teaching_player import TeachingPlayer
from teaching_scene_canvas import SceneCanvas
from teaching_scene_catalog import CHAPTERS,SCENES,defaults,build_scene,scene_by_number
from teaching_tool_math import parse_angle
from teaching_math import normalizeBearing,reverseBearing
from ui_kit import animations_enabled, IconButton, Combo
from teaching_tool_math import WIND_OFFSETS
from workbench_ui import SegmentRail


class StudioCombo(Combo):
    """教学下拉框保留 Qt 的程序选中信号语义。"""
    def setCurrentIndex(self,index,emit=True):
        if index!=self.currentIndex():super().setCurrentIndex(index,emit=emit)
        else:super().setCurrentIndex(index,emit=False)


class CourseChapterCard(QPushButton):
    """独立的大课程卡片；用轻柔底色和描边反馈选中状态。"""
    _descriptions={
        'basics':'认识地面站、任务航线与航点',
        'shapes':'从图形结构建立航线判断基础',
        'headings':'理解方位角、航向角与罗盘方向',
        'lengths':'结合边长和夹角推导航线关系',
        'takeoff':'规划起飞点、避让点与飞行路径',
        'settings':'整理题目条件，完成航线复核',
    }

    def __init__(self,index,key,title,count,parent=None):
        super().__init__(parent)
        self.index=index;self.key=key;self.title=title;self.count=count
        self._selection_progress=1.0 if index==0 else 0.0;self._hover_progress=0.0
        self.setObjectName('CourseChapterCard');self.setCheckable(True);self.setChecked(index==0)
        self.setAccessibleName(f'第{index+1}章，{title}，共{count}节课程')
        self.setAccessibleDescription(self._descriptions.get(key,''))
        self.setCursor(Qt.PointingHandCursor);self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(0,100);self.setFixedHeight(104);self.setMouseTracking(True)
        self.selection_animation=QPropertyAnimation(self,b'selectionProgress',self)
        self.selection_animation.setDuration(420);self.selection_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.hover_animation=QPropertyAnimation(self,b'hoverProgress',self)
        self.hover_animation.setDuration(210);self.hover_animation.setEasingCurve(QEasingCurve.OutCubic)

    def _get_selection_progress(self):return self._selection_progress
    def _set_selection_progress(self,value):self._selection_progress=float(value);self.update()
    selectionProgress=Property(float,_get_selection_progress,_set_selection_progress)

    def _get_hover_progress(self):return self._hover_progress
    def _set_hover_progress(self,value):self._hover_progress=float(value);self.update()
    hoverProgress=Property(float,_get_hover_progress,_set_hover_progress)

    def set_selected(self,selected,animate=True):
        self.setChecked(selected)
        target=1.0 if selected else 0.0
        self.selection_animation.stop()
        if animate and animations_enabled() and self.isVisible() and abs(target-self._selection_progress)>.001:
            self.selection_animation.setStartValue(self._selection_progress)
            self.selection_animation.setEndValue(target);self.selection_animation.start()
        else:self._set_selection_progress(target)

    def enterEvent(self,event):
        self._animate_hover(1.0);super().enterEvent(event)

    def leaveEvent(self,event):
        self._animate_hover(0.0);super().leaveEvent(event)

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Space,Qt.Key_Return,Qt.Key_Enter) and not event.isAutoRepeat():
            self.click();event.accept();return
        super().keyPressEvent(event)

    def _animate_hover(self,target):
        self.hover_animation.stop()
        if animations_enabled() and self.isVisible():
            self.hover_animation.setStartValue(self._hover_progress)
            self.hover_animation.setEndValue(target);self.hover_animation.start()
        else:self._set_hover_progress(target)

    @staticmethod
    def _mix(first,second,amount):
        return QColor(round(first.red()*(1-amount)+second.red()*amount),
                      round(first.green()*(1-amount)+second.green()*amount),
                      round(first.blue()*(1-amount)+second.blue()*amount),
                      round(first.alpha()*(1-amount)+second.alpha()*amount))

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        rect=QRectF(self.rect()).adjusted(1,1,-1,-1)
        selected=self._selection_progress;hover=self._hover_progress
        accent=QColor(T.C_RED if T.app_theme=='chiyo' else T.C_BLUE)
        base=QColor(T.P['panel_soft']);active=QColor(T.P['panel_bg'])
        fill=self._mix(base,active,.34)
        fill=self._mix(fill,accent,.035*selected+.018*hover)
        shadow=QColor(accent);shadow.setAlpha(round(13+17*selected+10*hover))
        p.setPen(Qt.NoPen);p.setBrush(shadow)
        p.drawRoundedRect(rect.translated(0,3),15,15)
        p.setPen(Qt.NoPen);p.setBrush(fill)
        p.drawRoundedRect(rect,15,15)
        bar_alpha=round(235*selected+110*hover)
        p.setPen(Qt.NoPen);p.setBrush(QColor(accent.red(),accent.green(),accent.blue(),bar_alpha))
        p.drawRoundedRect(QRectF(rect.left()+10,rect.top()+15,3,rect.height()-30),1.5,1.5)
        p.save();p.setClipRect(rect.adjusted(20,4,-14,-4))
        ink=QColor(T.P['ink']);muted=QColor(T.P['ink2'])
        p.setPen(self._mix(muted,accent,.65*selected))
        p.setFont(QFont(T.pick_font(),9,QFont.DemiBold))
        p.drawText(QRectF(rect.left()+20,rect.top()+9,rect.width()*.58,18),Qt.AlignLeft|Qt.AlignVCenter,
                   f'{self.index+1:02d}  /  {len(CHAPTERS):02d}')
        p.setPen(muted);p.setFont(QFont(T.pick_font(),9))
        p.drawText(QRectF(rect.right()-102,rect.top()+9,82,18),Qt.AlignRight|Qt.AlignVCenter,f'{self.count} 节课')
        p.setPen(ink);p.setFont(QFont(T.pick_font(),max(13,min(17,int(rect.height()*.17))),QFont.DemiBold))
        p.drawText(QRectF(rect.left()+20,rect.top()+29,rect.width()*.76,29),Qt.AlignLeft|Qt.AlignVCenter,self.title)
        p.setPen(muted);p.setFont(QFont(T.pick_font(),9))
        p.drawText(QRectF(rect.left()+20,rect.bottom()-28,rect.width()*.78,18),Qt.AlignLeft|Qt.AlignVCenter,
                   self._descriptions.get(self.key,''))
        watermark=QColor(accent);watermark.setAlpha(round(14+16*selected+8*hover));p.setPen(watermark)
        p.setFont(QFont(T.pick_font(),max(24,int(rect.height()*.48)),QFont.Bold))
        p.drawText(QRectF(rect.right()-78,rect.top()+6,58,rect.height()-12),Qt.AlignRight|Qt.AlignVCenter,
                   f'{self.index+1:02d}')
        p.restore()
        if self.hasFocus():
            focus=QColor(accent);focus.setAlpha(50)
            p.setBrush(focus);p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect.adjusted(4,4,-4,-4),11,11)
        p.end()


class CourseResumeCard(QPushButton):
    """课程目录底部的继续学习卡片，直接回到最近打开的课时。"""
    def __init__(self,parent=None):
        super().__init__(parent);self._hover_progress=0.0;self._number=1
        self._chapter_index=1;self._lesson_index=1;self._lesson_count=1
        self._chapter_title='什么是地面站';self._lesson_title='认识地面站'
        self.setObjectName('CourseResumeCard');self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus);self.setAccessibleName('继续学习当前课程')
        self.setMinimumHeight(108);self.setFixedHeight(112);self.setMouseTracking(True)
        self.hover_animation=QPropertyAnimation(self,b'hoverProgress',self)
        self.hover_animation.setDuration(240);self.hover_animation.setEasingCurve(QEasingCurve.OutCubic)

    def _get_hover_progress(self):return self._hover_progress
    def _set_hover_progress(self,value):self._hover_progress=float(value);self.update()
    hoverProgress=Property(float,_get_hover_progress,_set_hover_progress)

    def set_lesson(self,number):
        scene=scene_by_number(number)
        if scene.number>=47:return
        lessons=[item for item in SCENES if item.chapter==scene.chapter]
        self._number=scene.number;self._chapter_title=dict(CHAPTERS).get(scene.chapter,'课程')
        self._lesson_title=scene.title;self._chapter_index=next(
            (index for index,(key,_) in enumerate(CHAPTERS,1) if key==scene.chapter),1)
        self._lesson_index=next((index for index,item in enumerate(lessons,1) if item.number==scene.number),1)
        self._lesson_count=max(1,len(lessons))
        self.setAccessibleName(f'继续学习：{self._chapter_title}，第{self._lesson_index}节，{self._lesson_title}')
        self.update()

    def enterEvent(self,event):
        self._animate_hover(1.0);super().enterEvent(event)

    def leaveEvent(self,event):
        self._animate_hover(0.0);super().leaveEvent(event)

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Space,Qt.Key_Return,Qt.Key_Enter) and not event.isAutoRepeat():
            self.click();event.accept();return
        super().keyPressEvent(event)

    def _animate_hover(self,target):
        self.hover_animation.stop()
        if animations_enabled() and self.isVisible():
            self.hover_animation.setStartValue(self._hover_progress)
            self.hover_animation.setEndValue(target);self.hover_animation.start()
        else:self._set_hover_progress(target)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        rect=QRectF(self.rect()).adjusted(1,1,-1,-1)
        accent=QColor(T.C_RED if T.app_theme=='chiyo' else T.C_BLUE)
        fill=CourseChapterCard._mix(QColor(T.P['panel_bg']),accent,.055+.02*self._hover_progress)
        edge=CourseChapterCard._mix(QColor(T.P['input_border']),accent,.4+.35*self._hover_progress)
        p.setPen(QPen(edge,1.1+.3*self._hover_progress));p.setBrush(fill);p.drawRoundedRect(rect,16,16)
        p.save();p.setClipRect(rect.adjusted(20,6,-16,-6))
        p.setPen(accent);p.setFont(QFont(T.pick_font(),10,QFont.DemiBold))
        p.drawText(QRectF(rect.left()+22,rect.top()+12,rect.width()*.64,18),Qt.AlignLeft|Qt.AlignVCenter,'继续上次学习')
        p.setPen(QColor(T.P['ink']));p.setFont(QFont(T.pick_font(),17,QFont.DemiBold))
        p.drawText(QRectF(rect.left()+22,rect.top()+31,rect.width()*.72,28),Qt.AlignLeft|Qt.AlignVCenter,
                   f'{self._chapter_title}  ·  {self._lesson_title}')
        p.setPen(QColor(T.P['ink2']));p.setFont(QFont(T.pick_font(),9))
        p.drawText(QRectF(rect.left()+22,rect.top()+61,rect.width()*.72,18),Qt.AlignLeft|Qt.AlignVCenter,
                   f'第 {self._lesson_index:02d} / {self._lesson_count:02d} 节  ·  点击卡片回到课堂')
        track=QRectF(rect.left()+22,rect.bottom()-18,rect.width()*.72,4)
        p.setPen(Qt.NoPen);p.setBrush(QColor(T.P['input_border']));p.drawRoundedRect(track,2,2)
        progress=0 if self._lesson_count<2 else (self._lesson_index-1)/(self._lesson_count-1)
        p.setBrush(accent);p.drawRoundedRect(QRectF(track.left(),track.top(),track.width()*progress,track.height()),2,2)
        ghost=QColor(accent);ghost.setAlpha(18);p.setPen(ghost)
        p.setFont(QFont(T.pick_font(),max(28,int(rect.height()*.52)),QFont.Bold))
        p.drawText(QRectF(rect.right()-126,rect.top()+2,70,rect.height()-4),Qt.AlignCenter,
                   f'{self._chapter_index:02d}')
        p.setPen(QPen(accent,1.6));p.setBrush(Qt.NoBrush)
        center=rect.right()-38;cy=rect.center().y();p.drawEllipse(QRectF(center-17,cy-17,34,34))
        p.drawLine(int(center-5),int(cy),int(center+5),int(cy))
        p.drawLine(int(center+1),int(cy-6),int(center+7),int(cy))
        p.drawLine(int(center+1),int(cy+6),int(center+7),int(cy))
        p.restore()
        if self.hasFocus():
            p.setBrush(Qt.NoBrush);p.setPen(QPen(accent,1.5,Qt.DashLine));p.drawRoundedRect(rect.adjusted(4,4,-4,-4),12,12)
        p.end()


class CourseCardGrid(QWidget):
    """以响应式网格呈现课程章节，卡片本身即可完成切换。"""
    chapterSelected=Signal(str)

    def __init__(self,parent=None):
        super().__init__(parent);self.setObjectName('CourseCardGrid')
        self.setMinimumWidth(0);self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        self._columns=0;self._current_key='basics';self.cards={}
        grid=QGridLayout(self);grid.setContentsMargins(0,0,0,0);grid.setHorizontalSpacing(10);grid.setVerticalSpacing(10)
        self.grid=grid
        for index,(key,title) in enumerate(CHAPTERS):
            count=sum(1 for scene in SCENES if scene.chapter==key)
            card=CourseChapterCard(index,key,title,count,self)
            card.clicked.connect(lambda checked=False,k=key:self.chapterSelected.emit(k))
            self.cards[key]=card
        self._reflow()

    @property
    def current_key(self):return self._current_key

    def set_current(self,key,animate=True):
        if key not in self.cards:return
        self._current_key=key
        for card_key,card in self.cards.items():card.set_selected(card_key==key,animate)

    def refresh_theme(self):
        for card in self.cards.values():card.update()

    def resizeEvent(self,event):
        super().resizeEvent(event);self._reflow()

    def _reflow(self):
        width=max(1,self.width())
        columns=3 if width>=720 else 2 if width>=500 else 1
        if columns==self._columns:return
        self._columns=columns
        while self.grid.count():
            item=self.grid.takeAt(0)
            if item.widget():item.widget().setParent(self)
        for index,(_,_) in enumerate(CHAPTERS):
            card=self.cards[CHAPTERS[index][0]]
            self.grid.addWidget(card,index//columns,index%columns)
            self.grid.setColumnStretch(index%columns,1)
        rows=(len(CHAPTERS)+columns-1)//columns
        self.setMinimumHeight(rows*104+(rows-1)*self.grid.verticalSpacing())
        self.updateGeometry()


class TeachingStudio(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.setObjectName('TeachingStudio')
        self.player=TeachingPlayer(self);self.number=1;self.chapter='basics';self.parameters=defaults(1);self._last_course_number=1
        self.editors={};self._loading=False;self._layout_narrow=None;self._autoplay_pending=False;self._course_overview=False
        raw=cfg.get('teaching_v3',{});self.sessions=copy.deepcopy(raw.get('sessions',{})) if isinstance(raw,dict) else {}
        self.save_timer=QTimer(self);self.save_timer.setSingleShot(True);self.save_timer.setInterval(700);self.save_timer.timeout.connect(self._save)
        self._build();self.player.changed.connect(self.refresh);self.player.frame.connect(self._frame_progress)
        self.canvas.bearingDragged.connect(self.drag_bearing)
        self.canvas.parameterDragged.connect(self.drag_parameters)
        self.select_scene(47,save=False);self.theme_changed()
        for key,callback in (('Space',self.toggle_play),('Left',self.previous),('Right',self.next)):
            shortcut=QShortcut(QKeySequence(key),self);shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda call=callback:self._keyboard_action(call))

    def _build(self):
        outer=QVBoxLayout(self);self.outer_layout=outer;outer.setContentsMargins(22,14,22,12);outer.setSpacing(8)
        heading_host=QWidget();heading_host.setFixedHeight(64);heading=QHBoxLayout(heading_host);heading.setContentsMargins(0,0,0,0);titles=QVBoxLayout()
        titles.addWidget(label('地面站教学',23,True));titles.addWidget(label('跟着课程看图，动手验证航向、风向、避让与夹角。',10))
        heading.addLayout(titles,1);self.source_button=button('查看原课件',self.open_source);self.source_button.hide();outer.addWidget(heading_host)
        self.group_tabs=QWidget();group_row=QHBoxLayout(self.group_tabs);group_row.setContentsMargins(0,0,0,0);group_row.setSpacing(8)
        self.group_buttons={}
        for key,title in (("tools","工具类"),("courses","完整课程")):
            b=button(title,lambda checked=False,k=key:self.select_group(k));b.setCheckable(True);b.setMinimumWidth(132)
            group_row.addWidget(b);self.group_buttons[key]=b
        group_row.addStretch(1);outer.addWidget(self.group_tabs)
        self.course_flow=QWidget();self.course_flow.setObjectName('CoursePath')
        course_box=QVBoxLayout(self.course_flow);course_box.setContentsMargins(0,4,0,4);course_box.setSpacing(10)
        course_head=QHBoxLayout();course_head.addWidget(label('完整课程',17,True));course_head.addStretch(1)
        course_head.addWidget(label('6 章  ·  共 50 节',10));course_box.addLayout(course_head)
        self.course_grid=CourseCardGrid(self.course_flow)
        self.course_grid.chapterSelected.connect(self.select_chapter);course_box.addWidget(self.course_grid)
        self.course_resume=CourseResumeCard(self.course_flow)
        self.course_resume.clicked.connect(self.resume_course);course_box.addWidget(self.course_resume)
        course_box.addStretch(1)
        self.course_nav=QWidget();course_nav_row=QHBoxLayout(self.course_nav)
        course_nav_row.setContentsMargins(0,0,0,0);course_nav_row.setSpacing(10)
        self.course_back_button=button('课程目录',self.show_course_overview);course_nav_row.addWidget(self.course_back_button)
        self.course_meta=label('',16,True);self.course_meta.setWordWrap(True);course_nav_row.addWidget(self.course_meta,1)
        self.course_lesson_selector=StudioCombo(height=40);self.course_lesson_selector.setMinimumWidth(220)
        self.course_lesson_selector.changed.connect(self._course_lesson_changed)
        self.chapter_selector=StudioCombo(height=40)
        self.chapter_selector.setMinimumWidth(150)
        for key,title in CHAPTERS:self.chapter_selector.addItem(title,key)
        self.chapter_selector.addItem('交互工具','tools')
        self.chapter_selector.changed.connect(lambda:self.select_chapter(self.chapter_selector.currentData()))
        self.topic_rail=SegmentRail((('heading','航向','compass'),('wind','风向','wind'),('avoidance','避让点','map-pin'),('angle','夹角','sliders-horizontal')))
        self.topic_rail.changed.connect(self.select_topic);outer.addWidget(self.topic_rail)
        self.topic_rail.setFixedHeight(48)
        self.course_toolbar=QWidget();toolbar=QHBoxLayout(self.course_toolbar);toolbar.setContentsMargins(0,0,0,0);toolbar.setSpacing(8);self.scene_selector=StudioCombo(height=40);self.scene_selector.setMinimumWidth(220)
        self.scene_selector.changed.connect(self._scene_changed);toolbar.addWidget(label('夹角推导方式',10));toolbar.addWidget(self.scene_selector,1)
        self.tool_buttons={}
        for key,num in (('航向工具',47),('风向工具',48),('夹角工具',49)):
            b=button(key,lambda checked=False,n=num:self.select_scene(n));b.setCheckable(True);self.tool_buttons[num]=b
        outer.addWidget(self.course_toolbar)
        outer.addWidget(self.course_nav)
        outer.addWidget(self.course_flow,1)
        self.overview=self.course_meta
        for b in self.tool_buttons.values():b.setParent(self);b.hide()
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.NoFrame)
        body=QWidget();self.main_grid=QGridLayout(body);self.main_grid.setContentsMargins(0,0,0,0);self.main_grid.setSpacing(16)
        self.canvas=SceneCanvas(self.player);self.canvas.setMinimumHeight(240)
        self.panel=QFrame();self.panel.setObjectName('StudioPanel');panel=QVBoxLayout(self.panel);panel.setContentsMargins(18,18,18,18);panel.setSpacing(10)
        self.step_title=label('',17,True);self.explanation=label('',11);self.known=label('',10)
        self.formula=label('',15,True);self.formula.setObjectName('StudioFormula')
        self.result=label('',17,True);self.result.setObjectName('StudioResult')
        self.warning=label('',10);self.warning.setObjectName('StudioWarning')
        for w in (self.step_title,self.explanation,self.known,self.formula,self.result,self.warning):panel.addWidget(w)
        self.conditions_toggle=button('调整条件 ▾',self.toggle_conditions);panel.addWidget(self.conditions_toggle)
        self.conditions=QWidget();self.form=QFormLayout(self.conditions);self.form.setContentsMargins(0,0,0,0)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow);self.form.setVerticalSpacing(8);panel.addWidget(self.conditions)
        self.form.setSizeConstraint(QLayout.SetMinimumSize)
        panel.setSizeConstraint(QLayout.SetMinimumSize)
        self.heading_shortcuts=QWidget();shortcuts=QGridLayout(self.heading_shortcuts);shortcuts.setContentsMargins(0,0,0,0);shortcuts.setSpacing(5)
        compass=StudioCombo(height=40)
        for name,value in zip(('N 北','NE 东北','E 东','SE 东南','S 南','SW 西南','W 西','NW 西北'),range(0,360,45)):compass.addItem(name,value)
        compass.changed.connect(lambda:self.set_heading(compass.currentData()))
        shortcuts.addWidget(label('快捷方位',10),0,0);shortcuts.addWidget(compass,0,1,1,3)
        for i,(name,callback) in enumerate((('− 角度',lambda:self.offset_heading(-1)),('+ 角度',lambda:self.offset_heading(1)),('反向',lambda:self.set_heading(reverseBearing(self.parameters['heading']))))):
            shortcuts.addWidget(button(name,callback),1,i,1,1 if i<2 else 2)
        panel.addWidget(self.heading_shortcuts)
        self.wind_shortcuts=QWidget();wind_layout=QVBoxLayout(self.wind_shortcuts);wind_layout.setContentsMargins(0,0,0,0);wind_layout.setSpacing(8)
        self.wind_choice_toggle=button('风向选择  ▾',self.toggle_wind_choices)
        self.wind_choice_toggle.setAccessibleName('展开风向选择，八种风向')
        wind_layout.addWidget(self.wind_choice_toggle)
        self.wind_choice_panel=QWidget();windbox=QGridLayout(self.wind_choice_panel);windbox.setContentsMargins(0,0,0,0);windbox.setSpacing(8)
        self.wind_buttons={}
        for i,name in enumerate(WIND_OFFSETS):
            b=button(name,lambda checked=False,v=name:self.set_wind(v));b.setCheckable(True);windbox.addWidget(b,i//2,i%2);self.wind_buttons[name]=b
        wind_layout.addWidget(self.wind_choice_panel);self.wind_choice_panel.hide()
        panel.addWidget(self.wind_shortcuts)
        self.error=label('',10);self.error.setObjectName('StudioWarning');panel.addWidget(self.error)
        self.step_list=StudioCombo(height=40)
        self.step_list.changed.connect(self.jump_step)
        links=QHBoxLayout();links.addWidget(button('恢复示例',self.reset_parameters));self.related_button=button('打开对应工具',self.open_related);links.addWidget(self.related_button);panel.addLayout(links)
        self.panel_scroll=QScrollArea();self.panel_scroll.setWidgetResizable(True);self.panel_scroll.setFrameShape(QFrame.NoFrame)
        self.panel_scroll.setMinimumWidth(270)
        panel_host=QWidget();panel_box=QVBoxLayout(panel_host);panel_box.setContentsMargins(0,0,0,0)
        panel_box.addWidget(self.panel,0,Qt.AlignTop)
        self.lesson_picker=QFrame();self.lesson_picker.setObjectName('StudioPanel')
        picker=QVBoxLayout(self.lesson_picker);picker.setContentsMargins(18,18,18,18);picker.setSpacing(12)
        self.lesson_picker_title=label('当前课时',16,True);picker.addWidget(self.lesson_picker_title)
        picker.addWidget(self.course_lesson_selector)
        picker.addWidget(label('演示步骤',13,True));picker.addWidget(self.step_list)
        self.step_list.setAccessibleName('选择演示步骤');self.course_lesson_selector.setAccessibleName('选择当前课时')
        panel_box.addWidget(self.lesson_picker);panel_box.addStretch(1)
        self.panel_scroll.setWidget(panel_host)
        self.main_grid.addWidget(self.canvas,0,0);self.main_grid.addWidget(self.panel_scroll,0,1);self.main_grid.setColumnStretch(0,7);self.main_grid.setColumnStretch(1,3)
        self.scroll.setWidget(body);outer.addWidget(self.scroll,1)
        self.footer=QFrame();self.footer.setObjectName('TeachingPlayerDock');footer=QVBoxLayout(self.footer);footer.setContentsMargins(12,6,12,6);footer.setSpacing(4)
        self.position=label('',11);timeline=QHBoxLayout();timeline.setContentsMargins(0,0,0,0);timeline.addWidget(self.position,1);footer.addLayout(timeline)
        self.track=QSlider(Qt.Horizontal);self.track.setObjectName('TeachingScrubber');self.track.setRange(0,1000);self.track.setSingleStep(1);self.track.setPageStep(50);self.track.setAccessibleName('课程播放进度，可拖动定位');self.track.sliderMoved.connect(self._seek_progress);footer.addWidget(self.track)
        def control(name,title,callback):
            b=IconButton('',name,icon_size=22);b.setFixedSize(44,44)
            b.setToolTip(title);b.setAccessibleName(title);b.clicked.connect(callback);return b
        controls=QHBoxLayout();self.previous_button=control('skip_previous','上一步（←）',self.previous);self.play_button=control('play','播放（空格）',self.toggle_play)
        self.next_button=control('skip_next','下一步（→）',self.next);self.replay_button=control('reset','重播',self.replay)
        self.play_button._kind='primary';self.play_button.refresh()
        controls.addStretch(1)
        for b in (self.previous_button,self.play_button,self.next_button):
            controls.addWidget(b)
        controls.addStretch(1);controls.addWidget(self.replay_button)
        self.speed=StudioCombo(height=40)
        self.speed.setAccessibleName('播放速度');self.speed.setToolTip('播放速度')
        for value in (.5,.75,1,1.5,2):self.speed.addItem(f'{value:g}×',value)
        self.speed.setCurrentIndex(2);self.speed.changed.connect(lambda:self.player.set_speed(self.speed.currentData()));controls.addWidget(self.speed)
        footer.addLayout(controls);outer.addWidget(self.footer)

    def _save(self):
        if not self.player.lesson:return
        self.sessions[str(self.number)]={'parameters':dict(self.parameters),'snapshot':self.player.snapshot()}
        cfg.set_('teaching_v3',{'schema_version':3,'sessions':self.sessions})

    def select_chapter(self,key,save=True):
        numbers=[s.number for s in SCENES if s.chapter==key]
        if not numbers:return
        if save:self._save()
        target=self.number if self.chapter==key and self.number in numbers else numbers[0]
        self.chapter=key;self._topic='courses';self._course_overview=False;self._set_section_widgets('courses')
        self.course_grid.set_current(key,animate=True)
        self._populate_selector(numbers);self.select_scene(target,save=False)

    def show_course_overview(self):
        self._save();self.player.pause()
        self.course_resume.set_lesson(self._last_course_number)
        self._course_overview=True;self._set_section_widgets('courses')

    def resume_course(self):
        self.select_chapter(self.course_grid.current_key)

    def select_group(self,key):
        if key=='courses':
            self._topic='courses';self._course_overview=False
            self.select_scene(getattr(self,'_last_course_number',1))
            self.show_course_overview()
            return
        topic=getattr(self,'_last_tool_topic','heading')
        if topic=='courses':topic='heading'
        self.select_topic(topic)

    def _set_section_widgets(self,section):
        section='courses' if section=='courses' else 'tools'
        for key,button_widget in self.group_buttons.items():
            button_widget.setChecked(key==section)
            button_widget.set_checked_style(key==section)
        self.topic_rail.setVisible(section=='tools')
        overview=section=='courses' and self._course_overview
        self.course_flow.setVisible(overview)
        self.course_nav.setVisible(section=='courses' and not overview)
        self.lesson_picker_title.setVisible(section=='courses')
        self.course_lesson_selector.setVisible(section=='courses')
        self.course_toolbar.setVisible(section=='tools' and getattr(self,'_topic',None)=='angle')
        self.overview.setVisible(section=='courses' and not overview)
        self.scroll.setVisible(not overview)
        self.footer.setVisible(not overview)
        self.outer_layout.setStretchFactor(self.course_flow,1 if overview else 0)
        self.outer_layout.setStretchFactor(self.scroll,0 if overview else 1)
        if getattr(self,'embedded_mode',False):
            self.outer_layout.itemAt(0).widget().hide()
            self.group_tabs.hide();self.topic_rail.hide();self.course_back_button.hide()

    def select_topic(self,key):
        numbers={'heading':47,'wind':48,'avoidance':41,'angle':49,'courses':getattr(self,'_last_course_number',1)}
        if key not in numbers:return
        self._topic=key
        if key=='courses':self._course_overview=False;self._set_section_widgets('courses')
        else:
            self._last_tool_topic=key;self._set_section_widgets('tools')
        self.select_scene(numbers[key])
        if key=='angle':self._populate_selector((49,50));self.scene_selector.setCurrentIndex(0)

    def _populate_selector(self,numbers):
        self.scene_selector.blockSignals(True);self.scene_selector.clear()
        for number in numbers:
            scene=scene_by_number(number);self.scene_selector.addItem(f'{number:02d} · {scene.title}',number)
        self.scene_selector.blockSignals(False)
        self.course_lesson_selector.blockSignals(True);self.course_lesson_selector.clear()
        for number in numbers:
            scene=scene_by_number(number);self.course_lesson_selector.addItem(f'{number:02d}  ·  {scene.title}',number)
        self.course_lesson_selector.blockSignals(False)

    def _course_lesson_changed(self):
        value=self.course_lesson_selector.currentData()
        if value is not None and value!=self.number:self.select_scene(value)

    def _scene_changed(self):
        value=self.scene_selector.currentData()
        if value is not None:self.select_scene(value)

    def select_scene(self,number,save=True):
        if save:self._save()
        self.save_timer.stop();self.player.pause();self.number=int(number);scene=scene_by_number(number);self.chapter=scene.chapter
        if self.scene_selector.findData(number)<0:
            self._populate_selector([s.number for s in SCENES if s.chapter==scene.chapter])
        self.scene_selector.blockSignals(True);self.scene_selector.setCurrentIndex(self.scene_selector.findData(number));self.scene_selector.blockSignals(False)
        if self.course_lesson_selector.findData(number)<0:
            self._populate_selector([item.number for item in SCENES if item.chapter==scene.chapter])
        self.course_lesson_selector.blockSignals(True)
        self.course_lesson_selector.setCurrentIndex(self.course_lesson_selector.findData(number))
        self.course_lesson_selector.blockSignals(False)
        saved=self.sessions.get(str(number),{});self.parameters=defaults(number)|saved.get('parameters',{})
        try:lesson=build_scene(number,self.parameters)
        except (ValueError,TypeError,KeyError,IndexError):self.parameters=defaults(number);lesson=build_scene(number,self.parameters);saved={}
        self.canvas.starts={};self.player.load(lesson,saved.get('snapshot'));self.canvas.reset_view()
        topic={47:'heading',48:'wind',41:'avoidance',49:'angle',50:'angle'}.get(number,'courses')
        # 同一个避让场景也属于课程，进入途径决定导航归属，不改变数学内容。
        if getattr(self,'embedded_mode',False) and getattr(self,'embedded_section',None)=='courses':
            topic='courses'
        self._topic=topic
        if topic=='courses':self._last_course_number=int(number)
        else:self._last_tool_topic=topic
        if topic=='courses':
            self._course_overview=False
            self.course_grid.set_current(scene.chapter,animate=self.isVisible())
        self.topic_rail.set_current(topic,animate=False)
        self._set_section_widgets('courses' if topic=='courses' else 'tools')
        self.chapter_selector.hide();self.scene_selector.setVisible(topic=='angle')
        self.speed.blockSignals(True);self.speed.setCurrentIndex(max(0,self.speed.findData(self.player.session.speed)));self.speed.blockSignals(False)
        self.chapter_selector.blockSignals(True);self.chapter_selector.setCurrentIndex(self.chapter_selector.findData(scene.chapter));self.chapter_selector.blockSignals(False)
        self._build_form();self._steps();self.refresh()
        self.conditions.setMaximumHeight(16777215)
        self.conditions.setVisible(number>=47 or number==41);self.conditions_toggle.setVisible(number<47 and number!=41 and bool(self.editors))
        # 工具参数区按表单实际最小尺寸占位，短窗口由外层滚动承担。
        self.form.invalidate();self.form.activate()
        self.conditions.setMinimumHeight(self.form.minimumSize().height() if number>=47 else 0)
        self.source_button.setEnabled(bool(scene.pages));self.conditions_toggle.setText('调整条件 ▾')
        for num,b in self.tool_buttons.items():b.setChecked(num==number or (num==49 and number==50))
        self.heading_shortcuts.setVisible(number==47);self.wind_shortcuts.setVisible(number==48)
        # 默认展示动态；在减少动效或隐藏状态下提供完整静态知识图。
        self._autoplay_pending=not saved and not self.isVisible()
        if not saved and self.isVisible() and animations_enabled():self.player.play()

    def _field_keys(self):
        k=scene_by_number(self.number).kind
        if self.number in (32,33,34,35,49,50):return ('angle_name','known_index','heading','angle','turn_side','target_reverse')
        if self.number==47:return ('heading','angle')
        if self.number==48:return ('heading',)
        if k=='avoidance':return ('avoid_heading','avoid_distance','exit_distance')
        if k=='dms':return ('degree','minute','second')
        if k in ('altitude','vertical'):return ('height','return_height','vertical')
        if k=='horizontal':return ('distance','horizontal')
        if k=='dwell':return ('dwell',)
        if k=='loops':return ('loops',)
        if k in ('relative','semantics'):return ('distance',)
        if k in ('destination','takeoff_destination'):return ('heading','distance')
        if k in ('triangle','isosceles','hexagon','circle','resize_triangle'):return ('side',)
        if k in ('north_east','east_north','west_south'):return ('angle',)
        if k in ('add','subtract','reverse','swap','compass_route','rotate'):return ('heading','angle')
        return ()

    def _build_form(self):
        self._loading=True
        while self.form.rowCount():self.form.removeRow(0)
        self.editors={}
        titles={'heading':'已知航向（°）','angle':'角度（°）','side':'边长 / 圆周长（m）','distance':'距离（m）','horizontal':'水平速度（m/s）','vertical':'垂直速度（m/s）','height':'任务高度（m）','return_height':'返航高度（m）','dwell':'停留（s）','degree':'度','minute':'分','second':'秒','loops':'循环次数','angle_name':'夹角名称','known_index':'已知航段','turn_side':'未知射线所在侧','target_reverse':'目标方向'}
        titles.update(avoid_heading='H→1 航向（°）',avoid_distance='H→1 距离（m）',exit_distance='1→2 反向距离（m）')
        rays=parse_angle(self.parameters['angle_name'])['rays']
        choices={'angle_name':[(v,'∠'+v) for v in ('H12','12H','21H','32H')],
                 'known_index':[(i,'→'.join(ray)) for i,ray in enumerate(rays)]+[(i+2,'→'.join(ray[::-1])) for i,ray in enumerate(rays)],
                 'turn_side':[(v,v) for v in ('顺时针','逆时针','未指定（两解）')],
                 'target_reverse':[(False,'顶点→未知点'),(True,'未知点→顶点（反向）')]}
        for key in self._field_keys():
            if key in choices:
                w=QComboBox() if key=='angle_name' else StudioCombo(height=40)
                for value,title in choices[key]:w.addItem(title,value)
                if key=='angle_name':
                    w.setEditable(True)
                    if w.findData(self.parameters[key])<0:w.addItem('∠'+self.parameters[key],self.parameters[key])
                    w.lineEdit().editingFinished.connect(self._inputs_changed)
                w.setCurrentIndex(max(0,w.findData(self.parameters[key])))
                (w.currentIndexChanged if isinstance(w,QComboBox) else w.changed).connect(self._inputs_changed)
            else:
                w=QDoubleSpinBox();w.setRange(-1000000,1000000);w.setDecimals(3);w.setValue(self.parameters[key]);w.setKeyboardTracking(False)
                if key in ('side','distance','horizontal','vertical','avoid_distance','exit_distance'):w.setRange(.001,1000000)
                if key=='angle':w.setRange(.001,179.999 if self.number in (32,33,34,35,49,50) else 90 if self.number in (20,21,22) else 359.999)
                if key in ('minute','second'):w.setRange(0,59.999)
                if key=='loops':w.setRange(1,10);w.setDecimals(0)
                w.valueChanged.connect(self._inputs_changed)
            w.setMinimumHeight(34);w.setAccessibleName(titles[key]);self.form.addRow(titles[key],w);self.editors[key]=w
        self._loading=False;self.conditions_toggle.setEnabled(bool(self.editors))
        self.form.invalidate();self.form.activate()
        if self.number>=47:self.conditions.setMinimumHeight(self.form.minimumSize().height())

    def _inputs_changed(self):
        if self._loading:return
        params=dict(self.parameters)
        for key,w in self.editors.items():params[key]=(w.currentText().replace('∠','').strip() if key=='angle_name' else w.currentData()) if isinstance(w,(QComboBox,Combo)) else w.value()
        rebuild=params['angle_name']!=self.parameters['angle_name'];self.recalculate(params)
        if rebuild and self.parameters['angle_name']==params['angle_name']:self._build_form()

    def recalculate(self,params,drag=False):
        try:lesson=build_scene(self.number,params)
        except (ValueError,TypeError,OverflowError,IndexError) as error:self.error.setText(str(error)+'；保留上次有效图示。');return
        old=dict(self.canvas.drawn_values);bounds=self.canvas.bounds;zoom=self.canvas.zoom;pan=self.canvas.pan
        self.player.pause();self.parameters=lesson.parameters;self.error.clear()
        index=min(self.player.session.step,len(lesson.steps)-1)
        if self.number==47:index=1
        if self.number==48:index=1
        if self.number in (49,50):
            index=next(i for i,s in enumerate(lesson.steps) if ('reverse0' if params['target_reverse'] else 'arc0') in s.highlights)
        self.player.load(lesson);self.canvas.starts=old if self.number in (47,48) and not drag else {}
        self.player.seek(index,1 if drag or not animations_enabled() else 0)
        if drag:self.canvas.bounds=bounds;self.canvas.zoom=zoom;self.canvas.pan=pan
        else:self.canvas.reset_view()
        self._steps()
        if not drag and animations_enabled() and self.isVisible():self.player.play(single=True)
        self.refresh();self.save_timer.start()

    def set_heading(self,value,drag=False):
        p=dict(self.parameters);p['heading']=normalizeBearing(value);self.recalculate(p,drag)
        if 'heading' in self.editors:
            self.editors['heading'].blockSignals(True);self.editors['heading'].setValue(p['heading']);self.editors['heading'].blockSignals(False)

    def offset_heading(self,sign):self.set_heading(self.parameters['heading']+sign*self.parameters['angle'])
    def drag_bearing(self,value):self.set_heading(value,True)
    def drag_parameters(self,values):
        self.recalculate(dict(self.parameters)|values,True)
        for key,value in values.items():
            w=self.editors.get(key)
            if w is not None:
                w.blockSignals(True)
                if isinstance(w,(QComboBox,Combo)):w.setCurrentIndex(max(0,w.findData(value)))
                else:w.setValue(value)
                w.blockSignals(False)
    def set_wind(self,mode):
        p=dict(self.parameters);p['wind_mode']=mode;self.recalculate(p)
        self.wind_choice_panel.hide()
        self.wind_choice_toggle.setText('风向选择  ·  '+mode+'  ▾')

    def toggle_wind_choices(self):
        expanded=not self.wind_choice_panel.isVisible()
        self.wind_choice_panel.setVisible(expanded)
        self.wind_choice_toggle.setText('风向选择  ▴' if expanded else '风向选择  ·  '+self.parameters['wind_mode']+'  ▾')
        self.wind_choice_toggle.setAccessibleName(('收起' if expanded else '展开')+'风向选择，当前'+self.parameters['wind_mode'])
        if expanded and animations_enabled():
            from ui_kit import card_in
            card_in((self.wind_choice_panel,),ms=260)

    def _steps(self):
        self.step_list.blockSignals(True);self.step_list.clear()
        for i,s in enumerate(self.player.lesson.steps):self.step_list.addItem(f'{i+1:02d} · {s.title}',i)
        self.step_list.setCurrentIndex(self.player.session.step);self.step_list.blockSignals(False)

    def refresh(self):
        if not self.player.lesson:return
        lesson=self.player.lesson;s=self.player.current
        self.course_resume.set_lesson(getattr(self,'_last_course_number',1))
        scene=scene_by_number(self.number)
        chapter_index=next((index for index,(key,_) in enumerate(CHAPTERS,1) if key==scene.chapter),0)
        chapter_title=dict(CHAPTERS).get(scene.chapter,'互动工具')
        chapter_total=sum(1 for item in SCENES if item.chapter==scene.chapter)
        self.overview.setText(f'第 {chapter_index:02d} 章  ·  {chapter_title}  /  第 {self.number:02d} 课  ·  {scene.title}')
        self.overview.setToolTip(f'{chapter_title} · {self.number:02d}/50 · {lesson.source}')
        self.step_title.setText(s.title);self.explanation.setText(s.explanation);self.formula.setText(s.formula);self.result.setText(s.result)
        self.known.setText('已知条件：'+self._summary());self.known.setVisible(bool(self._field_keys()) and self.number<47)
        self.formula.setVisible(bool(s.formula));self.result.setVisible(bool(s.result));self.warning.setText('注意：'+'；'.join(lesson.warnings));self.warning.setVisible(bool(lesson.warnings))
        self.position.setText(f'步骤 {self.player.session.step+1} / {len(lesson.steps)} · '+('播放中' if self.player.session.playing else '已暂停')+' · 空格播放，← → 切步')
        self.play_button._icon_name='pause' if self.player.session.playing else 'play';self.play_button.refresh()
        self.play_button.setToolTip('暂停（空格）' if self.player.session.playing else '播放（空格）')
        self.play_button.setAccessibleName(self.play_button.toolTip())
        self.previous_button.setEnabled(self.player.session.step>0);self.next_button.setEnabled(self.player.session.step<len(lesson.steps)-1)
        self.step_list.blockSignals(True);self.step_list.setCurrentIndex(self.player.session.step);self.step_list.blockSignals(False)
        for name,b in self.wind_buttons.items():b.setChecked(name==self.parameters['wind_mode'])
        if not self.wind_choice_panel.isVisible():self.wind_choice_toggle.setText('风向选择  ·  '+self.parameters['wind_mode']+'  ▾')
        self.related_button.setVisible(self.chapter in ('headings','lengths','takeoff') or self.number==49)
        self.related_button.setText('双向求解演示' if self.number==49 else '打开航向工具' if self.chapter=='headings' else '打开夹角工具')
        self._frame_progress()

    def _summary(self):
        return '；'.join(f'{self.form.labelForField(self.editors[k]).text()}：{self.editors[k].currentText() if isinstance(self.editors[k],(QComboBox,Combo)) else f"{self.parameters[k]:g}"}' for k in self._field_keys() if k in self.editors)

    def _frame_progress(self):
        if self.player.lesson and not self.track.isSliderDown():
            self.track.blockSignals(True)
            self.track.setValue(round(1000*(self.player.session.step+self.player.session.progress)/len(self.player.lesson.steps)))
            self.track.blockSignals(False)

    def _seek_progress(self,value):
        if not self.player.lesson:return
        count=len(self.player.lesson.steps)
        position=max(0,min(count,float(value)/1000*count))
        if position>=count:index,progress=count-1,1.0
        else:index,progress=int(position),position-int(position)
        self.canvas.starts={};self.player.seek(index,progress)

    def jump_step(self,index):
        if index>=0 and not self._loading:self.canvas.starts={};self.player.seek(index)
    def previous(self):self.canvas.starts={};self.player.previous()
    def next(self):self.canvas.starts={};self.player.next()
    def replay(self):self.canvas.starts={};self.player.replay()
    def toggle_play(self):
        if self.player.session.playing:self.player.pause()
        else:self.player.play()
    def _keyboard_action(self,callback):
        focused=QApplication.focusWidget()
        if isinstance(focused,(QAbstractSpinBox,QLineEdit,QComboBox,Combo)):return
        callback()
    def toggle_conditions(self):
        from motion_polish import fold
        expanded=self.conditions.isHidden() or self.conditions.maximumHeight()==0;fold(self.conditions,expanded)
        self.conditions_toggle.setText('收起条件 ▴' if expanded else '调整条件 ▾')
    def reset_parameters(self):self.parameters=defaults(self.number);self._build_form();self.recalculate(self.parameters)
    def open_related(self):self.select_scene(50 if self.number==49 else 47 if self.chapter=='headings' else 49)

    def open_source(self):
        pages=scene_by_number(self.number).pages
        if not pages:return
        dialog=QDialog(self);dialog.setWindowTitle('原课件 · 图示与条件');dialog.resize(1050,700)
        box=QVBoxLayout(dialog);selector=QComboBox()
        # 原课程全部22页可查看，默认定位当前教学章节的来源。
        for n in range(1,23):selector.addItem(f'PPT 第 {n} 页',n)
        box.addWidget(selector);scroll=QScrollArea();scroll.setWidgetResizable(True)
        content=QLabel();content.setAlignment(Qt.AlignCenter);scroll.setWidget(content);box.addWidget(scroll,1)
        def update():
            image=QPixmap(T.asset_path('teaching','ppt',f'slide-{selector.currentData()}.png'))
            content.setPixmap(image.scaled(980,552,Qt.KeepAspectRatio,Qt.SmoothTransformation));content.setMinimumSize(600,360)
        selector.currentIndexChanged.connect(update);selector.setCurrentIndex(pages[0]-1);update();dialog.exec()

    def theme_changed(self):
        accent='#f04464' if T.app_theme=='chiyo' else T.C_BLUE
        self.setStyleSheet(f'''QWidget#TeachingStudio{{background:transparent;color:{T.C_INK};}}
        QWidget#TeachingStudio QWidget{{background:transparent;}}
        QWidget#TeachingStudio QFrame#StudioPanel{{background:{T.P['panel_bg']};border:0;border-radius:14px;}}
        QWidget#TeachingStudio QFrame#TeachingPlayerDock{{background:{T.P['panel_bg']};border:0;border-radius:14px;}}
        QWidget#TeachingStudio QPushButton{{background:{T.P['panel_soft']};color:{T.C_INK};border:0;border-radius:10px;padding:6px 10px;font-weight:600;}}
        QWidget#TeachingStudio QPushButton:hover{{background:{T.P['panel_hover']};}}
        QWidget#TeachingStudio QPushButton:checked{{background:{accent};color:white;}}
        QWidget#TeachingStudio QPushButton#CourseResumeCard{{min-height:112px;max-height:112px;padding:0px;border:0px;}}
        QPushButton#CourseChapterCard{{background:{T.P['panel_soft']};border:0;border-radius:11px;padding:5px 10px;text-align:left;font-size:11px;font-weight:650;}}
        QPushButton#CourseChapterCard:checked{{background:{T.P['accent_soft']};border:0;color:{T.P['accent_fg']};}}
        QPushButton#CourseSceneCard{{background:{T.P['panel_soft']};border:0;border-radius:10px;padding:4px 10px;font-size:10px;}}
        QPushButton#CourseSceneCard:checked{{background:{T.P['accent_soft']};border:0;color:{T.P['accent_fg']};}}
        QFrame#CourseConnector{{background:{T.P['input_border']};border:0;border-radius:1px;}}
        QWidget#TeachingStudio QPushButton:disabled{{color:{T.C_INK_3};}}
        QWidget#TeachingStudio QComboBox,QWidget#TeachingStudio QDoubleSpinBox{{background:{T.P['input_bg']};border:0;border-radius:8px;padding:5px;color:{T.C_INK};}}
        QWidget#TeachingStudio QListWidget{{background:transparent;border:0;color:{T.C_INK};}}
        QWidget#TeachingStudio QListWidget::item{{padding:5px;border-radius:6px;}}
        QWidget#TeachingStudio QListWidget::item:selected{{background:{accent};color:white;}}
        QSlider#TeachingScrubber::groove:horizontal{{height:4px;background:{T.P['input_border']};border:0;border-radius:2px;}}
        QSlider#TeachingScrubber::sub-page:horizontal{{background:{accent};border-radius:2px;}}
        QSlider#TeachingScrubber::handle:horizontal{{width:14px;height:14px;margin:-5px 0;background:{accent};border:2px solid {T.P['panel_bg']};border-radius:7px;}}
        QSlider#TeachingScrubber::handle:horizontal:hover{{background:{T.P['accent_fg']};}}
        QLabel#StudioFormula{{color:{'#f2bd58' if T.mode=='dark' else '#926200'};}}
        QLabel#StudioResult{{color:{'#4fd4ba' if T.mode=='dark' else '#008470'};}}
        QLabel#StudioWarning{{color:{T.P['err_fg']};}}
        QWidget#TeachingStudio QScrollArea{{border:0;background:transparent;}}''')
        self.canvas.background_key=None;self.canvas.update();self.refresh()
        self.topic_rail.refresh_theme()
        self.course_grid.refresh_theme()
        from ui_kit import IconButton
        for b in self.findChildren(IconButton):b.refresh()
        for b in self.panel.findChildren(IconButton):
            b.setMinimumWidth(0);b.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)

    def resizeEvent(self,event):
        super().resizeEvent(event);narrow=self.width()<640
        self.form.setRowWrapPolicy(QFormLayout.WrapAllRows if self.width()<1220 else QFormLayout.DontWrapRows)
        if narrow!=self._layout_narrow:
            self._layout_narrow=narrow;self.main_grid.removeWidget(self.canvas);self.main_grid.removeWidget(self.panel_scroll)
            self.main_grid.addWidget(self.canvas,0,0,1,2 if narrow else 1)
            self.main_grid.addWidget(self.panel_scroll,1 if narrow else 0,0 if narrow else 1,1,2 if narrow else 1)
            self.panel_scroll.setMinimumHeight(240 if narrow else 0)
    def hideEvent(self,event):
        self.player.set_visible(False);self.save_timer.stop();self._save();super().hideEvent(event)
    def showEvent(self,event):
        self.player.set_visible(True);self.window().installEventFilter(self);super().showEvent(event)
        if self._autoplay_pending:
            self._autoplay_pending=False
            QTimer.singleShot(0,self.player.play)
    def eventFilter(self,watched,event):
        if event.type()==QEvent.WindowStateChange and watched is self.window():self.player.set_visible(self.isVisible() and not watched.isMinimized())
        return super().eventFilter(watched,event)
