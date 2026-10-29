"""实际应用与发布程序教学验收：隔离配置、全场景关键帧和主题布局。"""
import copy,json,shutil,tempfile
from pathlib import Path


def run(app,output):
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QFont
    import config as cfg,theme as T
    from main_window import MainWindow
    from teaching_scene_catalog import SCENES
    from teaching_tool_math import wind_from
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    previous=cfg._path,cfg._cache;records=[];errors=[]
    with tempfile.TemporaryDirectory(prefix='azimuth_teaching_studio_') as folder:
        cfg._path=str(Path(folder)/'config.json');cfg._cache=copy.deepcopy(cfg.DEFAULTS)
        installed=Path('D:/个人项目/20260808 地面站教学/AZIMUTH · 方位角/AZIMUTH/themes')
        if installed.is_dir():shutil.copytree(installed,Path(folder)/'themes',dirs_exist_ok=True)
        T.load_app_fonts();app.setFont(QFont(T.pick_font()))
        win=MainWindow();win.resize(1584,992);win.show();win.switch(2);win.p_know._select_teaching_view(0);QTest.qWait(150)
        studio=win.p_know.teaching_workspace
        try:
            for theme in ('default','chiyo','anna'):
                win.set_app_theme(theme)
                for appearance in ('light','dark'):
                    win._apply_theme(appearance,pref=appearance);win.switch(2);studio.select_scene(49);studio.player.seek(7,.6)
                    QTest.qWait(80);win.grab().save(str(output/f'{theme}-{appearance}.png'))
                    for width,height in ((1024,760),(1280,800),(1584,992)):
                        win.resize(width,height);QTest.qWait(40)
                        rect=studio.footer.geometry();assert rect.bottom()<studio.height(),'播放栏越界'
                        assert studio.scene_selector.geometry().width()>100,'场景选择入口被裁切'
                        values=list(studio.editors.values())
                        for a,b in zip(values,values[1:]):assert a.geometry().bottom()<b.geometry().top(),'工具输入框发生重叠'
                        records.append({'type':'layout','theme':theme,'appearance':appearance,'size':[width,height],'footer_bottom':rect.bottom()})
            win.set_app_theme('default');win._apply_theme('dark',pref='dark');win.resize(1584,992);QTest.qWait(50)
            for scene in SCENES:
                studio.select_scene(scene.number);studio.player.pause();changed=False
                for i,step in enumerate(studio.player.lesson.steps):
                    studio.player.seek(i,.15);app.processEvents();a=studio.canvas.grab().toImage()
                    studio.player.seek(i,.85);app.processEvents();b=studio.canvas.grab().toImage()
                    changed|=a!=b
                assert changed,f'{scene.number:02d} 场景无可见运动'
                studio.player.seek(len(studio.player.lesson.steps)-1,1);app.processEvents()
                win.grab().save(str(output/f'scene-{scene.number:02d}.png'))
                studio.canvas.grab().save(str(output/f'canvas-{scene.number:02d}.png'))
                records.append({'type':'scene','number':scene.number,'title':scene.title,'chapter':scene.chapter,
                                'steps':len(studio.player.lesson.steps),'frame_changed':changed,'warnings':studio.player.lesson.warnings})
            for n in range(1,23):assert Path(T.asset_path('teaching','ppt',f'slide-{n}.png')).is_file(),f'原课件第{n}页缺失'
            studio.select_scene(47);studio.drag_bearing(359);studio.canvas.grab();assert abs(studio.canvas.drawn_values['compass']-359)<1e-5
            studio.set_heading(1,True);studio.canvas.grab();assert studio.canvas.drawn_values['compass']==1
            studio.select_scene(48)
            for mode in ('顺风','逆风','正左侧风','正右侧风'):
                studio.set_wind(mode);studio.player.seek(1,1);studio.canvas.grab();assert studio.canvas.drawn_values['wind']==wind_from(90,mode)
            studio.select_scene(49)
            for name in ('H12','12H','21H','32H'):
                for side in ('顺时针','逆时针','未指定（两解）'):
                    studio.recalculate(dict(studio.parameters,angle_name=name,turn_side=side),True);assert not studio.error.text()
            studio.player.replay();studio.player.pause();assert not studio.player.timer.isActive()
            win.switch(0);QTest.qWait(50);assert not studio.player.timer.isActive()
            for index in (1,3,4,2):win.switch(index);QTest.qWait(50)
            win.grab().save(str(output/'final-window.png'))
        except Exception:
            import traceback;errors.append(traceback.format_exc())
        finally:
            costs=sorted(studio.canvas.paint_costs)
            records.append({'type':'performance','paint_samples':len(costs),'paint_p95_ms':costs[int(len(costs)*.95)] if costs else 0,
                            'note':'绘制耗时不是屏幕实际呈现帧率。'})
            win.close();app.processEvents();cfg._path,cfg._cache=previous
    (output/'result.json').write_text(json.dumps({'ok':not errors,'records':records,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if not errors else 2
