"""唯一教学时间轴；跳步、变速和隐藏均保留确定状态。"""
import time
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtWidgets import QApplication
from teaching_models import LessonSession
from ui_kit import animations_enabled


class TeachingPlayer(QObject):
    changed = Signal()
    frame = Signal()
    completed = Signal(str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.lesson=None
        self.session=LessonSession()
        self.timer=QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        screen=QApplication.instance().primaryScreen()
        hz=max(60,min(144,round(screen.refreshRate() or 60))) if screen else 60
        self.timer.setInterval(round(1000/hz))
        self.timer.timeout.connect(self._tick)
        self.elapsed=0.0
        self.last=0.0
        self.visible=True
        self.intervals=[]
        self.single_step=False

    @property
    def current(self):
        return self.lesson.steps[self.session.step] if self.lesson else None

    def load(self,lesson,saved=None):
        self.pause()
        revision=self.session.revision+1
        self.lesson=lesson
        self.session=LessonSession(lesson.id,revision)
        if saved:
            self.session.step=max(0,min(len(lesson.steps)-1,int(saved.get('step',0))))
            self.session.progress=max(0,min(1,float(saved.get('progress',1))))
            self.session.speed=max(.25,min(2,float(saved.get('speed',1))))
            self.session.viewport=dict(saved.get('viewport',{}))
        self.elapsed=self.session.progress*self.current.duration
        self.changed.emit();self.frame.emit()

    def seek(self,index,progress=1):
        self.pause()
        self.session.step=max(0,min(len(self.lesson.steps)-1,int(index)))
        self.session.progress=max(0,min(1,float(progress)))
        self.elapsed=self.session.progress*self.current.duration
        self.changed.emit();self.frame.emit()

    def previous(self):self.seek(self.session.step-1)
    def next(self):
        animated=animations_enabled() and self.visible
        self.seek(self.session.step+1,0 if animated else 1)
        if animated:self.play(single=True)

    def pause(self):
        self.timer.stop()
        self.session.playing=False
        self.changed.emit()

    def play(self,single=False):
        if not self.lesson or not self.visible:return
        # 减少动效时仍逐步阅读，不自动跨过知识步骤。
        if not animations_enabled():
            self.session.progress=1
            self.frame.emit();self.changed.emit()
            return
        if self.session.progress>=1:
            self.elapsed=0
            self.session.progress=0
        self.session.playing=True
        self.single_step=single
        self.last=time.monotonic()
        self.timer.start()
        self.changed.emit()

    def replay(self):
        self.seek(0,0)
        self.play()

    def set_speed(self,speed):
        speed=float(speed)
        if not .25<=speed<=2:raise ValueError('播放速度须在 0.25～2 倍之间')
        self.session.speed=speed
        self.last=time.monotonic()
        self.changed.emit()

    def set_visible(self,visible):
        self.visible=bool(visible)
        if not visible:self.pause()

    def snapshot(self):
        return {'step':self.session.step,'progress':self.session.progress,'speed':self.session.speed,'viewport':self.session.viewport}

    def _tick(self):
        if not self.visible or not self.session.playing:
            self.pause();return
        if not animations_enabled():
            self.session.progress=1;self.pause();self.frame.emit();return
        now=time.monotonic()
        delta=(now-self.last)*1000
        self.last=now
        self.intervals.append(delta)
        self.intervals=self.intervals[-4096:]
        self.advance(delta)

    def advance(self,milliseconds):
        """可定位时钟，测试与真实播放使用相同路径。"""
        if not self.lesson:return
        self.elapsed+=max(0,float(milliseconds))*self.session.speed
        self.session.progress=min(1,self.elapsed/self.current.duration)
        if self.elapsed >= self.current.duration+2200:
            if self.single_step:
                self.pause()
            elif self.session.step==len(self.lesson.steps)-1:
                self.pause();self.completed.emit(self.lesson.id)
            else:
                self.session.step+=1
                self.elapsed=0
                self.session.progress=0
                self.changed.emit()
        self.frame.emit()
