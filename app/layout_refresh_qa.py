"""隔离配置运行四个真实页面，检查布局与按钮可打断动效。"""
import sys,os,json,tempfile,copy,shutil,time
from pathlib import Path

os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS','--disable-gpu --disable-software-rasterizer')
from PySide6.QtWidgets import QApplication,QPushButton
from PySide6.QtTest import QTest
from PySide6.QtCore import QPoint,QEvent,QCoreApplication
from PySide6.QtGui import QFont
import config as cfg,theme as T
import bankdata as BD


def run(app,output):
    from main_window import MainWindow
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    records=[];errors=[];old=(cfg._path,cfg._cache)
    old_bank=BD._root
    with tempfile.TemporaryDirectory(prefix='azimuth_layout_') as folder:
        cfg._path=str(Path(folder)/'config.json');cfg._cache=copy.deepcopy(cfg.DEFAULTS)
        cfg._cache['animations_on']=False
        source=Path('D:/个人项目/20260808 地面站教学/AZIMUTH · 方位角/AZIMUTH')
        bank=Path(folder)/'bank'
        shutil.copytree(source/'data/bank',bank)
        BD._root=str(bank)
        for name in ('themes',):
            if (source/name).is_dir():shutil.copytree(source/name,Path(folder)/name)
        T.load_app_fonts();app.setFont(QFont(T.pick_font()));win=MainWindow();win.show()
        try:
            assert win.p_know.teaching_stack.currentIndex()==2,'教学功能入口未默认展示'
            assert win.p_know.teaching_hub.stack.currentIndex()==3,'教学未先展示一级菜单'
            assert win.p_bank._preview_fit=='width','题卡未默认适合宽度'
            for theme in ('default','chiyo','anna'):
                win.set_app_theme(theme)
                for mode in ('light','dark'):
                    win._apply_theme(mode,pref=mode)
                    for size in ((1024,760),(1280,800),(1584,992)):
                        win.resize(*size)
                        for index,name in ((1,'bank'),(2,'teaching'),(0,'timer'),(3,'settings')):
                            win.switch(index);QTest.qWait(180)
                            page=win.p_bank if index==1 else win.p_know.teaching_workspace if index==2 else win.p_timer if index==0 else win.p_set
                            if index==2:win.p_know._select_teaching_view(0);page.select_scene(49);page.player.pause();page.player.seek(7,1);QTest.qWait(50)
                            assert page.isVisible(),f'{name} 实际页面不可见'
                            if index==0:
                                arrangement = win.p_timer
                                metrics = (arrangement.arrangement_dock.isVisible(), arrangement.arrangement_dock.size().width(), arrangement.arrangement_dock.size().height(), arrangement.arrangement_scroll.isVisible(), arrangement.right_panel.isVisible(), arrangement.right_panel.size().width(), arrangement.right_panel.size().height(), arrangement.arrangement_title.isVisible())
                                assert metrics[0] and metrics[1] >= 200 and metrics[2] >= 300 and metrics[3] and metrics[4] and metrics[5] >= 200 and metrics[6] >= 240 and metrics[7], f'计时准备态训练安排卡尺寸或可见性错误：{metrics}'
                            selected = next(button for button in win.navs if button.page_index == index)
                            selected_point = selected.mapTo(win.side,QPoint())
                            offset = 8 if theme == 'chiyo' else 0
                            assert abs(win.nav_plate.y()-selected_point.y()+offset)<=1,'侧栏选中底板错位'
                            win.grab().save(str(out/f'{name}-{theme}-{mode}-{size[0]}.png'))
                            buttons=[w for w in page.findChildren(QPushButton) if w.isVisibleTo(page)]
                            for w in buttons:
                                assert w.width()>0 and w.height()>0,f'{name} 空按钮尺寸'
                            if index==1:
                                a=page.preview_fit_choice.mapTo(page,QPoint());b=page.b_full.mapTo(page,QPoint())
                                assert abs(a.y()+page.preview_fit_choice.height()/2-b.y()-page.b_full.height()/2)<=2,'预览工具未对齐'
                            if index==2:
                                assert page.footer.geometry().bottom()<page.height(),'播放栏越界'
                                hub=win.p_know.teaching_hub
                                win.p_know._select_teaching_view(2)
                                hub.show_menu();QTest.qWait(50)
                                win.grab().save(str(out/f'teaching-hub-menu-{theme}-{mode}-{size[0]}.png'))
                                records.append({'page':'teaching-hub','state':'menu','theme':theme,'mode':mode,'size':size})
                                for section in ('courses','tools','shapes'):
                                    hub.select_section(section);QTest.qWait(50)
                                    assert hub.stack.currentWidget().isVisible(),'教学入口分页不可见'
                                    if section=='courses':
                                        assert len(hub.course_row.cards)==6,'六章课程缺失'
                                        assert hub.course_row.cards[0].width()>=330,'课程卡没有保持宽度'
                                        assert hub.course_row.width()>hub.course_row.parentWidget().width(),'课程卡没有横向浏览空间'
                                    elif section=='tools':assert len(hub.tool_row.cards)==4,'四个工具缺失'
                                    else:
                                        assert win.p_know.demo_toggle.isHidden() and win.p_know.demo_controls.isHidden(),'图形展示仍有复杂控制'
                                        assert win.p_know.figure_stack.currentWidget() is win.p_know.route_demo,'图形展示未使用动态画布'
                                    win.grab().save(str(out/f'teaching-hub-{section}-{theme}-{mode}-{size[0]}.png'))
                                    records.append({'page':'teaching-hub','state':section,'theme':theme,'mode':mode,'size':size})
                            records.append({'page':name,'theme':theme,'mode':mode,'size':size,'buttons':len(buttons)})
                    win.resize(1280,800);win.switch(1);QTest.qWait(80)
                    bank_page=win.p_bank;question=bank_page.cur['id'];bank_page._change_preview_zoom(.25)
                    for reading in ('original','focus'):
                        bank_page.set_reading_mode(reading,animate=False);QTest.qWait(60)
                        assert bank_page.cur['id']==question and bank_page._preview_zoom==1.25,'阅读切换丢失题目或缩放'
                        assert bank_page.body_column.isVisible()==(reading=='original'),'题卡说明显示状态错误'
                        win.grab().save(str(out/f'bank-{reading}-{theme}-{mode}.png'))
                        records.append({'page':'bank','state':reading,'theme':theme,'mode':mode})
                    bank_page._change_preview_zoom(-.25)
                    win.switch(3);QTest.qWait(80)
                    for section in ('appearance','timer','bank','api','maintenance'):
                        win.p_set._select_settings_group(section,save=False);QTest.qWait(60)
                        assert win.p_set.settings_navigation.isVisible(),'设置分类丢失'
                        assert win.p_set.scroll.horizontalScrollBar().maximum()==0,'设置编辑区横向溢出'
                        if section == 'appearance':
                            chosen=win.p_set._theme_btns[win.p_set._theme_pref]
                            assert abs(win.p_set.theme_indicator.geometry().center().x()-chosen.geometry().center().x())<=1,'外观选中框错位'
                        win.grab().save(str(out/f'settings-{section}-{theme}-{mode}.png'))
                        records.append({'page':'settings','state':section,'theme':theme,'mode':mode})
                    win.p_set._select_settings_group('appearance',save=False)
                    win.switch(2);win.p_know._select_teaching_view(0);studio=win.p_know.teaching_workspace
                    for topic in ('heading','wind','avoidance','angle'):
                        studio.select_topic(topic);studio.player.pause();studio.player.seek(1 if topic in ('heading','wind') else len(studio.player.lesson.steps)-1 if topic=='avoidance' else 7,1)
                        QTest.qWait(60)
                        if studio.panel_scroll.horizontalScrollBar().maximum():
                            details=[(type(w).__name__,w.minimumSizeHint().width(),w.width()) for w in studio.panel.findChildren(__import__('PySide6.QtWidgets',fromlist=['QWidget']).QWidget) if w.isVisible() and w.minimumSizeHint().width()>220]
                            raise AssertionError('教学参数横向溢出 '+str((topic,studio.panel.width(),studio.panel_scroll.viewport().width(),details)))
                        assert not studio.topic_rail.buttons[topic].icon().isNull(),'正式教学图标未加载'
                        win.grab().save(str(out/f'teaching-{topic}-{theme}-{mode}.png'))
                        records.append({'page':'teaching','state':topic,'theme':theme,'mode':mode})
                    win.switch(0);timer=win.p_timer;timer.chk_sound.setChecked(False);timer.set_read_len(20);timer.set_do_len(60)
                    timer.start_session();deadline=timer._phase_deadline
                    assert timer.duration_editor.isHidden(),'运行后时长配置未收起'
                    for state in ('read','answer','complete'):
                        if state=='answer':timer.skip_stage()
                        elif state=='complete':
                            timer.phase=timer.LOCK;timer.tick.stop();timer._sync_ring_copy()
                        QTest.qWait(70);win.grab().save(str(out/f'timer-{state}-{theme}-{mode}.png'))
                        records.append({'page':'timer','state':state,'theme':theme,'mode':mode})
                    timer.reset();timer.set_read_len(60);timer.set_do_len(360)
                    timer.toggle_custom_duration();QTest.qWait(50)
                    assert timer.custom_read.height()==timer.custom_do.height()==44,'自定义时长输入高度不统一'
                    assert timer.custom_read.y()==timer.custom_do.y(),'自定义时长未对齐'
                    assert abs(timer.custom_read.width()-timer.custom_do.width())<=1,'自定义时长列宽不统一'
                    assert 'border-radius:10px' in timer.custom_read.styleSheet(),'自定义时长未使用圆角主题样式'
                    assert timer.arrangement_scroll.horizontalScrollBar().maximum()==0,'计时安排横向溢出'
                    win.grab().save(str(out/f'timer-custom-{theme}-{mode}.png'))
                    timer.toggle_custom_duration()
                    win.switch(2);win.p_know._select_teaching_view(2);win.p_know.teaching_hub.select_section('shapes');QTest.qWait(60)
                    assert win.p_know.demo_advanced.isHidden(),'低频教学选项未收纳'
                    win.grab().save(str(out/f'teaching-basic-{theme}-{mode}.png'))
                    win.p_know._select_teaching_view(0);studio.select_topic('courses');studio.player.pause();studio.player.seek(2,1);QTest.qWait(60)
                    assert studio.panel.y()<10,'讲解卡片未顶端对齐'
                    win.grab().save(str(out/f'teaching-courses-{theme}-{mode}.png'))
            button=win.p_timer.b_reset;win.switch(0);QTest.qWait(100)
            motion=button._interactive_hover
            cfg._cache['animations_on']=True
            win.switch(3);QTest.qWait(80)
            nav=win.p_set.settings_navigation
            QTest.mouseClick(nav.buttons['timer'],__import__('PySide6.QtCore',fromlist=['Qt']).Qt.LeftButton)
            QTest.qWait(60)
            from PySide6.QtCore import QAbstractAnimation
            assert nav.animation.state()==QAbstractAnimation.Running,'设置点击回调中止选中框动画'
            QTest.qWait(600)
            win.switch(2);win.p_know._select_teaching_view(2);QTest.qWait(600)
            win.p_know.teaching_hub.select_section('courses');QTest.qWait(120)
            row=win.p_know.teaching_hub.course_row
            assert row.entrance is not None,'大卡片没有入场动效'
            win.grab().save(str(out/'teaching-card-entry-motion.png'))
            QTest.mouseClick(row.cards[0],__import__('PySide6.QtCore',fromlist=['Qt']).Qt.LeftButton)
            assert row.entrance is None,'点击卡片未清理入场快照'
            assert win.p_know.teaching_stack.currentIndex()==0,'点击课程未进入详情'
            assert getattr(win.p_know,'_workbench_reveal',None) is not None,'教学分页没有切换动效'
            QTest.qWait(600)
            win.switch(0)
            motion.animate(1);QTest.qWait(120);before=motion.progress
            assert 0<before<1,'悬停蒙版没有渐进展开'
            motion.animate(0);assert abs(motion.progress-before)<.001,'反转动画发生跳变'
            QTest.qWait(550);assert motion.progress==0,'动画未停止'
            button.setEnabled(False);assert motion.progress==0,'禁用未清理动画'
            button.setEnabled(True);cfg._cache['animations_on']=False
            motion.animate(1);assert motion.progress==0,'减少动态效果仍有位移'
            # 在真实主窗口检查透明快照，避免仅验收动画结束状态漏掉白底。
            cfg._cache['animations_on'] = True
            win.set_app_theme('default')
            for mode in ('light', 'dark'):
                win._apply_theme(mode, pref=mode)
                win.switch(0); QTest.qWait(400)
                timer = win.p_timer
                timer.set_read_len(0); timer.set_do_len(60)
                timer.start_session(); QTest.qWait(85)
                frame = timer._arrangement_exit.pixmap.toImage()
                assert frame.pixelColor(0, 0).alpha() == 0, '训练卡快照角落带底色'
                frame.save(str(out / f'timer-transparent-snapshot-{mode}.png'))
                # 小窗口中卡片可能滚动到底边，检查卡片外侧的空白，避免误判真实内容。
                assert frame.pixelColor(0, frame.height() - 1).alpha() < 20, '训练卡快照外侧带遮罩'
                assert timer._timer_visual_motion is not None, '计时信息未执行出入场'
                win.grab().save(str(out / f'timer-exit-motion-{mode}.png'))
                QTest.qWait(350)
                timer.reset(); QTest.qWait(150)
                assert timer._arrangement_exit.returning_dock is timer.arrangement_dock
                win.grab().save(str(out / f'timer-return-motion-{mode}.png'))
                QTest.qWait(350)
                for widget in timer._timer_motion_widgets():
                    assert widget.mask().isEmpty(), '计时信息动画残留绘制遮罩'
                assert timer.b_custom.isVisible(), '回归后自定义时长按钮不可见'
                win.switch(3)
                assert win._timer_stack_exit is not None, '计时页面缺少退场动画'
                QTest.qWait(220)
            cfg._cache['animations_on'] = False
        except Exception:
            import traceback;errors.append(traceback.format_exc())
        finally:
            win.p_timer.tick.stop()
            win.p_timer.phase = win.p_timer.IDLE
            win.close();app.processEvents();cfg._path,cfg._cache=old;BD._root=old_bank
    (out/'result.json').write_text(json.dumps({'ok':not errors,'records':records,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    return 2 if errors else 0
