"""教学 2.0 工作区：章节、可编辑条件、统一播放器及本地练习。"""
import html,math,time
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QUrl,QEvent
from PySide6.QtGui import QPixmap,QTextDocument,QImage,QFont,QKeySequence,QShortcut
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QPushButton,
    QComboBox,QDoubleSpinBox,QScrollArea,QFrame,QStackedWidget,QListWidget,QFormLayout,
    QDialog,QFileDialog,QLineEdit,QSizePolicy,QMessageBox)
from PySide6.QtPrintSupport import QPrinter
import theme as T
from teaching_courses import CHAPTERS,FIELDS,CHOICE_LABELS,SHAPE_KEYS,SHAPE_NAMES,defaults,build_course
from teaching_player import TeachingPlayer
from teaching_canvas import TeachingCanvas
from teaching_models import VisualObject
from teaching_math import normalizeBearing
import teaching_progress as progress


def label(text='',size=12,bold=False):
    w=QLabel(text);w.setWordWrap(True)
    w.setTextFormat(Qt.PlainText)
    w.setFont(QFont(T.pick_font(),size,QFont.Bold if bold else QFont.Normal))
    return w


def button(text,callback=None):
    from ui_kit import IconButton
    b=IconButton(text,font_size=12,radius=10);b.setMinimumHeight(40);b.setCursor(Qt.PointingHandCursor)
    if callback:b.clicked.connect(callback)
    return b


class TeachingWorkspace(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('TeachingWorkspace')
        self.player=TeachingPlayer(self)
        self.chapter='bearing';self.parameters=defaults(self.chapter)
        self.editors={};self.quiz_inputs=[];self.sessions={};self._loading=False
        self.exam_started=None;self.exam_remaining=180;self.exam_active=False;self.exam_answers={}
        self.exam_timer=QTimer(self);self.exam_timer.setInterval(250);self.exam_timer.timeout.connect(self._exam_tick)
        self._build()
        self.player.changed.connect(self.refresh)
        self.player.completed.connect(self._completed)
        self.canvas.bearingDragged.connect(self.drag_bearing)
        self.select_chapter('bearing',save=False)
        self.theme_changed()
        for key,call in (('Space',self.toggle_play),('Left',self.player.previous),('Right',self.player.next)):
            shortcut=QShortcut(QKeySequence(key),self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut);shortcut.activated.connect(call)

    def _build(self):
        outer=QVBoxLayout(self);outer.setContentsMargins(24,18,24,12);outer.setSpacing(12)
        heading=QHBoxLayout()
        titlebox=QVBoxLayout();titlebox.addWidget(label('地面站教学 2.0',24,True))
        titlebox.addWidget(label('读懂条件，跟随推导，再独立完成航线。',11))
        heading.addLayout(titlebox,1)
        self.mode=QComboBox();self.mode.addItem('分步教学','teach');self.mode.addItem('自由练习','practice');self.mode.addItem('本地模拟','exam');self.mode.setMinimumHeight(38)
        self.mode.currentIndexChanged.connect(self.change_mode);heading.addWidget(self.mode)
        outer.addLayout(heading)
        navscroll=QScrollArea();navscroll.setWidgetResizable(True);navscroll.setFixedHeight(58);navscroll.setFrameShape(QFrame.NoFrame)
        navscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded);navscroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav=QWidget();navrow=QHBoxLayout(nav);navrow.setContentsMargins(0,0,0,0);navrow.setSpacing(6)
        self.chapter_buttons={}
        for key,name in CHAPTERS:
            b=button(name,lambda checked=False,k=key:self.select_chapter(k));b.setCheckable(True);b.setMinimumWidth(100)
            navrow.addWidget(b);self.chapter_buttons[key]=b
        navscroll.setWidget(nav);outer.addWidget(navscroll)
        self.shape_scroll=QScrollArea();self.shape_scroll.setWidgetResizable(True);self.shape_scroll.setFixedHeight(54);self.shape_scroll.setFrameShape(QFrame.NoFrame)
        self.shape_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        shapes=QWidget();shape_row=QHBoxLayout(shapes);shape_row.setContentsMargins(0,0,0,0);shape_row.setSpacing(6)
        self.shape_buttons={}
        for key,name in zip(SHAPE_KEYS,SHAPE_NAMES):
            b=button(name,lambda checked=False,k=key:self.select_geometry(k));b.setCheckable(True);b.setMinimumWidth(96)
            shape_row.addWidget(b);self.shape_buttons[key]=b
        self.shape_scroll.setWidget(shapes);outer.addWidget(self.shape_scroll);self.shape_scroll.hide()
        self.overview=label('',11);outer.addWidget(self.overview)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.NoFrame)
        body=QWidget();self.body_layout=QVBoxLayout(body);self.body_layout.setContentsMargins(0,0,0,0);self.body_layout.setSpacing(12)
        self.main=QWidget();self.main_grid=QGridLayout(self.main);self.main_grid.setContentsMargins(0,0,0,0);self.main_grid.setSpacing(18)
        self.canvas=TeachingCanvas(self.player);self.canvas.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
        left=QWidget();leftbox=QVBoxLayout(left);leftbox.setContentsMargins(0,0,0,0);leftbox.setSpacing(8)
        leftbox.addWidget(self.canvas,1)
        canvasrow=QHBoxLayout();canvasrow.addWidget(label('模型与公式同步 · 示例不冒充标准答案',9),1)
        canvasrow.addWidget(button('复位视图',self.canvas.reset_view));leftbox.addLayout(canvasrow)
        self.left=left
        self.panel=QFrame();self.panel.setObjectName('TeachingPanel')
        panel=QVBoxLayout(self.panel);panel.setContentsMargins(18,16,18,16);panel.setSpacing(10)
        self.step_title=label('',17,True);panel.addWidget(self.step_title)
        self.explanation=label('',11);panel.addWidget(self.explanation)
        self.formula=label('',14,True);self.formula.setObjectName('TeachingFormula');panel.addWidget(self.formula)
        self.substitution=label('',11);panel.addWidget(self.substitution)
        self.result=label('',12,True);self.result.setObjectName('TeachingResult');panel.addWidget(self.result)
        self.warning=label('',10);self.warning.setObjectName('TeachingWarning');panel.addWidget(self.warning)
        row=QHBoxLayout();self.conditions_toggle=button('编辑条件 ▾',self.toggle_conditions);row.addWidget(self.conditions_toggle)
        self.source_button=button('查看原题',self.open_source);row.addWidget(self.source_button);panel.addLayout(row)
        self.conditions=QWidget();conditionsbox=QVBoxLayout(self.conditions);conditionsbox.setContentsMargins(0,0,0,0)
        self.form=QFormLayout();self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow);conditionsbox.addLayout(self.form)
        formrow=QHBoxLayout();self.apply_button=button('应用条件',self.apply_conditions);formrow.addWidget(self.apply_button)
        formrow.addWidget(button('恢复示例',self.reset_parameters));conditionsbox.addLayout(formrow)
        self.error=label('',10);self.error.setObjectName('TeachingWarning');conditionsbox.addWidget(self.error)
        panel.addWidget(self.conditions);self.conditions.hide()
        self.step_list=QListWidget();self.step_list.setMinimumHeight(110);self.step_list.setMaximumHeight(180)
        self.step_list.currentRowChanged.connect(self.jump_step);panel.addWidget(self.step_list)
        self.main_grid.addWidget(left,0,0);self.main_grid.addWidget(self.panel,0,1)
        self.main_grid.setColumnStretch(0,6);self.main_grid.setColumnStretch(1,4)
        self.body_layout.addWidget(self.main)
        self.quiz=QFrame();self.quiz.setObjectName('TeachingPanel');self.quiz_box=QVBoxLayout(self.quiz);self.quiz_box.setContentsMargins(18,16,18,16)
        self.quiz_title=label('先独立作答，再核对推导',16,True);self.quiz_box.addWidget(self.quiz_title)
        self.quiz_edit_button=button('编辑本章练习条件',self.edit_practice_conditions)
        self.quiz_box.addWidget(self.quiz_edit_button)
        self.quiz_fields=QFormLayout();self.quiz_box.addLayout(self.quiz_fields)
        quizrow=QHBoxLayout();self.exam_start_button=button('开始 3 分钟模拟',self.start_exam);quizrow.addWidget(self.exam_start_button)
        self.submit_button=button('核对答案',self.submit_answers);quizrow.addWidget(self.submit_button)
        self.quiz_result=label('',11);self.quiz_box.addLayout(quizrow);self.quiz_box.addWidget(self.quiz_result)
        self.body_layout.addWidget(self.quiz);self.quiz.hide()
        self.scroll.setWidget(body);outer.addWidget(self.scroll,1)
        self.footer=QWidget();footer=QVBoxLayout(self.footer);footer.setContentsMargins(0,0,0,0);footer.setSpacing(6)
        self.position=label('',10);footer.addWidget(self.position)
        controls=QGridLayout();controls.setHorizontalSpacing(8)
        self.previous_button=button('← 上一步',self.player.previous);controls.addWidget(self.previous_button,0,0)
        self.play_button=button('播放推导',self.toggle_play);controls.addWidget(self.play_button,0,1)
        self.next_button=button('下一步 →',self.player.next);controls.addWidget(self.next_button,0,2)
        self.replay_button=button('重播',self.player.replay);controls.addWidget(self.replay_button,0,3)
        self.speed=QComboBox()
        for value in (.5,.75,1,1.5,2):self.speed.addItem(f'{value:g} 倍',value)
        self.speed.setCurrentIndex(2);self.speed.setMinimumHeight(36)
        self.speed.currentIndexChanged.connect(lambda:self.player.set_speed(self.speed.currentData()))
        controls.addWidget(self.speed,0,4)
        self.export_button=button('A4 学习卡',self.export_card);controls.addWidget(self.export_button,1,0,1,2)
        self.mastered_button=button('标记已掌握',self.mark_complete);controls.addWidget(self.mastered_button,1,2,1,3)
        footer.addLayout(controls);outer.addWidget(self.footer)

    def _save(self):
        if not self.player.lesson:return
        self.sessions[self.chapter]={'parameters':dict(self.parameters),'snapshot':self.player.snapshot()}
        progress.save_session(self.chapter,self.parameters,self.player.snapshot())

    def select_chapter(self,chapter,save=True):
        if self.exam_active:
            self.quiz_result.setText('模拟进行中，请先提交本章答案。');return
        if save:self._save()
        self.player.pause();self.chapter=chapter
        stored=self.sessions.get(chapter) or progress.load_progress().get('sessions',{}).get(chapter,{})
        self.parameters=defaults(chapter)|stored.get('parameters',{})
        try:lesson=build_course(chapter,self.parameters)
        except (ValueError,TypeError,KeyError,OverflowError):
            self.parameters=defaults(chapter);lesson=build_course(chapter,self.parameters)
        for key,b in self.chapter_buttons.items():b.setChecked(key==chapter)
        self._rebuild_form();self.player.load(lesson,stored.get('snapshot'))
        saved=dict(self.player.session.viewport)
        self.canvas.reset_view()
        if saved:
            from PySide6.QtCore import QPointF
            self.canvas.zoom=saved.get('zoom',1);self.canvas.pan=QPointF(*saved.get('pan',[0,0]))
            self.player.session.viewport=saved
        self._rebuild_steps();self._rebuild_quiz();self.refresh()
        if self.mode.currentData()!='teach':self.change_mode()

    def _clear_form(self,form):
        while form.rowCount():form.removeRow(0)

    def select_geometry(self,key):
        if self.exam_active:return
        self.parameters['shape']=key;self._rebuild_form();self.apply_conditions()

    def _rebuild_form(self):
        self._loading=True;self._clear_form(self.form);self.editors={}
        for key,name,value,choices in FIELDS[self.chapter]:
            value=self.parameters[key]
            if choices:
                editor=QComboBox()
                for choice in choices:editor.addItem(CHOICE_LABELS.get(choice,choice),choice)
                editor.setCurrentIndex(max(0,editor.findData(value)))
                editor.setMinimumHeight(32)
            else:
                editor=QDoubleSpinBox();editor.setRange(-1000000,1000000);editor.setDecimals(4);editor.setValue(float(value));editor.setMinimumHeight(32)
            self.form.addRow(name,editor);self.editors[key]=editor
        self._loading=False

    def _rebuild_steps(self):
        self.step_list.blockSignals(True);self.step_list.clear()
        self.step_list.addItems([f'{i+1:02d}  {s.title}' for i,s in enumerate(self.player.lesson.steps)])
        self.step_list.setCurrentRow(self.player.session.step);self.step_list.blockSignals(False)

    def _rebuild_quiz(self):
        self._clear_form(self.quiz_fields);self.quiz_inputs=[]
        for q in self.player.lesson.questions:
            editor=QLineEdit();editor.setPlaceholderText('请输入数值'+('（'+q.unit+'）' if q.unit else ''));editor.setMinimumHeight(36)
            editor.setAccessibleName(q.prompt);self.quiz_fields.addRow(q.prompt,editor);self.quiz_inputs.append(editor)
        self.quiz_result.clear();self.exam_answers={}

    def toggle_conditions(self):
        from motion_polish import fold
        expanded=self.conditions.isHidden() or self.conditions.maximumHeight()==0
        fold(self.conditions,expanded)
        self.conditions_toggle.setText('收起条件 ▴' if expanded else '编辑条件 ▾')

    def apply_conditions(self):
        if self.exam_active:return
        parameters={k:(w.currentData() if isinstance(w,QComboBox) else w.value()) for k,w in self.editors.items()}
        try:lesson=build_course(self.chapter,parameters)
        except (ValueError,TypeError,OverflowError) as error:
            self.player.pause();self.error.setText(str(error)+'；画布保留上次有效条件，请修正后应用。');return
        self.parameters=parameters;self.error.clear()
        self.player.load(lesson);self.canvas.reset_view();self._rebuild_steps();self._rebuild_quiz();self.refresh();self._save()

    def reset_parameters(self):
        if self.exam_active:return
        self.parameters=defaults(self.chapter);self._rebuild_form();self.apply_conditions()

    def drag_bearing(self,angle):
        if self.chapter!='bearing' or self.exam_active:return
        # 拖动时直接更新同一课程条件，不做追随滤镜，不逐帧写磁盘。
        quadrant=int(angle//90)
        bases=['北','东','南','西'];towards=['东','南','西','北']
        p={'base':bases[quadrant],'toward':towards[quadrant],'angle':angle-quadrant*90}
        self.parameters=p
        self.player.load(build_course('bearing',p));self.player.seek(1,1)
        self._loading=True
        for key in ('base','toward'):
            w=self.editors[key];w.setCurrentIndex(w.findData(p[key]))
        self.editors['angle'].setValue(p['angle']);self._loading=False
        self._rebuild_steps()
        # 自由练习每次条件变更都重新生成问题，避免旧答案与新条件混用。
        if self.mode.currentData()!='teach':self._rebuild_quiz()

    def jump_step(self,index):
        if index>=0 and not self._loading:self.player.seek(index)

    def toggle_play(self):
        if self.player.session.playing:self.player.pause()
        else:self.player.play()

    def refresh(self):
        if not self.player.lesson:return
        lesson=self.player.lesson;s=self.player.current
        practicing=self.mode.currentData()!='teach'
        done=lesson.id in progress.load_progress().get('completed',[])
        self.overview.setText(f'{lesson.title} · {len(lesson.steps)} 个推导步骤 · '+('已掌握' if done else '学习中')+' · '+lesson.source)
        self.step_title.setText(s.title);self.explanation.setText(s.explanation)
        self.formula.setText(s.formula);self.substitution.setText(s.substitution);self.result.setText(s.result)
        for w in (self.formula,self.substitution,self.result):w.setVisible(bool(w.text()) and not practicing)
        self.warning.setText('待复核：'+'；'.join(dict.fromkeys(lesson.warnings+s.warnings)) if lesson.warnings or s.warnings else '')
        self.warning.setVisible(bool(self.warning.text()))
        self.step_list.blockSignals(True);self.step_list.setCurrentRow(self.player.session.step);self.step_list.blockSignals(False)
        self.position.setText(f'步骤 {self.player.session.step+1} / {len(lesson.steps)} · '+('播放中' if self.player.session.playing else '已暂停，可逐步阅读')+' · 空格播放 / 左右切步')
        self.play_button.setText('暂停推导' if self.player.session.playing else '播放推导')
        self.previous_button.setEnabled(self.player.session.step>0)
        self.next_button.setEnabled(self.player.session.step<len(lesson.steps)-1)
        self.mastered_button.setText('✓ 已掌握' if done else '标记已掌握')
        self.source_button.setVisible(self.chapter=='cases')
        self.shape_scroll.setVisible(self.chapter=='geometry')
        for key,b in self.shape_buttons.items():b.setChecked(key==self.parameters.get('shape'))
        self.speed.blockSignals(True);self.speed.setCurrentIndex(max(0,self.speed.findData(self.player.session.speed)));self.speed.blockSignals(False)
        self.conditions_toggle.setEnabled(not self.exam_active)
        self.apply_button.setEnabled(not self.exam_active)

    def change_mode(self):
        if self.exam_active:
            self.mode.blockSignals(True);self.mode.setCurrentIndex(2);self.mode.blockSignals(False);return
        mode=self.mode.currentData();self.player.pause();self.player.session.mode=mode
        self.quiz.setVisible(mode!='teach');self.main.setVisible(mode=='teach')
        self.footer.setVisible(mode=='teach');self.exam_start_button.setVisible(mode=='exam')
        self.submit_button.setEnabled(mode!='exam')
        self._rebuild_quiz();self.refresh()
        self.quiz_title.setText('本地模拟 · 3 分钟 · 仅评已确认知识点' if mode=='exam' else '自由练习 · 条件可编辑，答案提交后显示')
        # 练习模式提供独立条件入口，解题面板隐藏避免泄露答案。
        if mode!='teach':
            self.quiz_title.setText(self.quiz_title.text()+'\n条件：'+self._parameter_summary())

    def _parameter_summary(self):
        if self.chapter=='cases':
            from teaching_cases import CASES
            return '\n'.join(CASES[self.parameters['case']]['conditions'])
        return '；'.join(f'{name}={CHOICE_LABELS.get(str(self.parameters[k]),self.parameters[k])}' for k,name,_,_ in FIELDS[self.chapter])

    def edit_practice_conditions(self):
        if self.exam_active:return
        dialog=QDialog(self);dialog.setWindowTitle('编辑练习条件');dialog.resize(460,560)
        box=QVBoxLayout(dialog);form=QFormLayout();editors={}
        for key,name,_,choices in FIELDS[self.chapter]:
            if choices:
                w=QComboBox()
                for choice in choices:w.addItem(CHOICE_LABELS.get(choice,choice),choice)
                w.setCurrentIndex(w.findData(self.parameters[key]))
            else:
                w=QDoubleSpinBox();w.setRange(-1000000,1000000);w.setDecimals(4);w.setValue(float(self.parameters[key]))
            w.setMinimumHeight(34);form.addRow(name,w);editors[key]=w
        box.addLayout(form);error=label();box.addWidget(error)
        def apply():
            parameters={k:(w.currentData() if isinstance(w,QComboBox) else w.value()) for k,w in editors.items()}
            try:lesson=build_course(self.chapter,parameters)
            except (ValueError,TypeError,OverflowError) as failure:error.setText(str(failure));return
            self.parameters=parameters;self.player.load(lesson);self._rebuild_form();self._rebuild_steps();self._rebuild_quiz();self.change_mode();self._save();dialog.accept()
        box.addWidget(button('应用条件',apply));box.addWidget(button('取消',dialog.reject));dialog.exec()

    def start_exam(self):
        self.exam_active=True;self.exam_started=time.monotonic();self.exam_remaining=180
        self.exam_answers={};self.submit_button.setEnabled(True);self.exam_start_button.setEnabled(False)
        self.quiz_result.clear()
        for w in self.quiz_inputs:w.setReadOnly(False);w.clear()
        for b in self.chapter_buttons.values():b.setEnabled(False)
        self.mode.setEnabled(False);self.exam_timer.start();self._exam_tick()
        self.quiz_edit_button.setEnabled(False)

    def _exam_tick(self):
        if not self.exam_active:return
        remaining=max(0,180-int(time.monotonic()-self.exam_started))
        self.exam_remaining=remaining;self.exam_start_button.setText(f'剩余 {remaining//60:02d}:{remaining%60:02d}')
        if remaining==0:self.submit_answers(expired=True)

    def submit_answers(self,checked=False,expired=False):
        rows=[];answers={};correct=0
        for i,(q,w) in enumerate(zip(self.player.lesson.questions,self.quiz_inputs)):
            try:
                value=float(w.text());ok=math.isfinite(value) and abs(value-q.answer)<=q.tolerance
            except ValueError:value=None;ok=False
            if ok:correct+=1
            answers[str(i)]=value
            rows.append(('✓ ' if ok else '待复习：')+q.prompt+f' → {q.answer:.6g}{q.unit}'+('；'+q.explanation if q.explanation else ''))
        mode=self.mode.currentData()
        if self.exam_active:
            self.exam_timer.stop();self.exam_active=False
            for b in self.chapter_buttons.values():b.setEnabled(True)
            self.mode.setEnabled(True);self.exam_start_button.setEnabled(True);self.exam_start_button.setText('重新开始 3 分钟模拟')
            for w in self.quiz_inputs:w.setReadOnly(True)
            self.submit_button.setEnabled(False)
            self.quiz_edit_button.setEnabled(True)
        self.quiz_result.setText(('时间到，已提交。\n' if expired else '')+f'{correct} / {len(rows)} 项正确\n'+'\n'.join(rows)+'\n仅为本地知识练习，不代替原题任务判分。')
        progress.record_attempt(self.player.lesson.id,mode,answers,{'correct':correct,'total':len(rows)})

    def mark_complete(self):
        progress.complete(self.player.lesson.id);self.refresh()

    def _completed(self,lesson_id):
        self._save()
        self.position.setText('推导演示完成；可重播、练习或标记掌握。')

    def open_source(self):
        from teaching_cases import CASES
        page=CASES[self.parameters['case']]['page']
        path=T.asset_path('teaching','cases',f'page-{page}.png')
        dialog=QDialog(self);dialog.setWindowTitle(self.player.lesson.source);dialog.resize(840,760)
        box=QVBoxLayout(dialog);note=label('原题图保留原文；疑似笔误与条件矛盾见教学复核提示。',11);box.addWidget(note)
        scroll=QScrollArea();view=QLabel();pix=QPixmap(path)
        if pix.isNull():view.setText('原题资源未找到，请核对发布包。')
        else:view.setPixmap(pix.scaledToWidth(800,Qt.SmoothTransformation))
        scroll.setWidget(view);box.addWidget(scroll);box.addWidget(button('关闭',dialog.accept));dialog.exec()

    def export_card(self):
        path,_=QFileDialog.getSaveFileName(self,'保存 A4 学习卡',self.player.lesson.title+'.pdf','PDF (*.pdf)')
        if not path:return
        if not path.lower().endswith('.pdf'):path+='.pdf'
        try:
            from PySide6.QtGui import QPageSize
            T.load_app_fonts()
            lesson=self.player.lesson;doc=QTextDocument();doc.setDefaultFont(QFont('Alibaba PuHuiTi 2.0',11))
            image=self.canvas.grab().toImage();doc.addResource(QTextDocument.ImageResource,QUrl('teaching-canvas'),image)
            content=f'<h1>{html.escape(lesson.title)}</h1><p>{html.escape(lesson.source)}</p><p>{html.escape(self._parameter_summary())}</p><img src="teaching-canvas" width="620"/>'
            if lesson.warnings:content+='<p>待复核：'+html.escape('；'.join(lesson.warnings))+'</p>'
            for i,s in enumerate(lesson.steps):
                content+=f'<h2>{i+1}. {html.escape(s.title)}</h2><p>{html.escape(s.explanation)}</p><p>{html.escape(s.formula)}<br/>{html.escape(s.substitution)}<br/><b>{html.escape(s.result)}</b></p>'
            doc.setHtml(content);printer=QPrinter(QPrinter.HighResolution);printer.setOutputFormat(QPrinter.PdfFormat);printer.setPageSize(QPageSize(QPageSize.A4));printer.setOutputFileName(path);doc.print_(printer)
            self.position.setText('已保存学习卡：'+path)
        except (OSError,RuntimeError) as error:QMessageBox.warning(self,'保存失败',str(error))

    def theme_changed(self):
        accent='#f04464' if T.app_theme=='chiyo' else T.C_BLUE
        result_color=('#ff839a' if T.app_theme=='chiyo' else '#8ab5ff') if T.mode=='dark' else accent
        self.setStyleSheet(f'''QWidget#TeachingWorkspace{{background:transparent;color:{T.C_INK};}}
        QWidget#TeachingWorkspace QWidget{{background:transparent;}}
        QWidget#TeachingWorkspace QFrame#TeachingPanel{{background:{T.P['panel_bg']};border:1px solid {T.P['input_border']};border-radius:14px;}}
        QWidget#TeachingWorkspace QPushButton{{color:{T.C_INK};background:{T.P['input_bg']};border:0;border-radius:10px;padding:6px 10px;font-weight:600;}}
        QWidget#TeachingWorkspace QPushButton:checked{{background:{accent};color:white;border:0;}}
        QWidget#TeachingWorkspace QPushButton:hover{{background:{T.P['accent_soft']};}}
        QWidget#TeachingWorkspace QPushButton:disabled{{color:{T.C_INK_3};}}
        QLabel#TeachingFormula,QLabel#TeachingResult{{color:{result_color};padding:4px 0;}}
        QLabel#TeachingWarning{{color:{T.P['err_fg']};}}
        QWidget#TeachingWorkspace QComboBox,QWidget#TeachingWorkspace QDoubleSpinBox,QWidget#TeachingWorkspace QLineEdit{{background:{T.P['input_bg']};color:{T.C_INK};border:1px solid {T.P['input_border']};border-radius:8px;padding:4px;}}
        QWidget#TeachingWorkspace QListWidget{{background:transparent;color:{T.C_INK};border:0;}}
        QWidget#TeachingWorkspace QListWidget::item{{padding:6px;border-radius:7px;}}
        QWidget#TeachingWorkspace QListWidget::item:selected{{background:{accent};color:white;}}
        QWidget#TeachingWorkspace QScrollArea{{border:0;background:transparent;}}''')
        self.canvas.update();self.refresh()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        narrow=self.width()<1000
        self.main_grid.removeWidget(self.left);self.main_grid.removeWidget(self.panel)
        self.main_grid.addWidget(self.left,0,0,1,2 if narrow else 1)
        self.main_grid.addWidget(self.panel,1 if narrow else 0,0 if narrow else 1,1,2 if narrow else 1)
        self.canvas.setMinimumHeight(340 if narrow else 410)
        self.panel.setMinimumWidth(0 if narrow else 320)

    def hideEvent(self,event):
        self.player.set_visible(False)
        self._save()
        if self.exam_active:
            self.exam_timer.stop();self._exam_tick()
        super().hideEvent(event)

    def showEvent(self,event):
        self.player.set_visible(True)
        self.window().installEventFilter(self)
        if self.exam_active:self.exam_timer.start()
        super().showEvent(event)

    def eventFilter(self,watched,event):
        if event.type()==QEvent.WindowStateChange and watched is self.window():
            self.player.set_visible(self.isVisible() and not watched.isMinimized())
        return super().eventFilter(watched,event)
