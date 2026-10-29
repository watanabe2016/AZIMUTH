"""发布程序专项验收入口；只用隔离配置，截图与结果写入指定目录。"""
import copy,json,shutil,sys,tempfile,time
from pathlib import Path


def run_legacy(app,output):
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QFont
    import config as cfg,theme as T
    from main_window import MainWindow
    from teaching_courses import CHAPTERS,SHAPE_KEYS,build_course
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    previous=cfg._path,cfg._cache
    records=[];errors=[]
    with tempfile.TemporaryDirectory(prefix='azimuth_teaching_qa_') as folder:
        cfg._path=str(Path(folder)/'config.json');cfg._cache=copy.deepcopy(cfg.DEFAULTS)
        # 只读拷贝安装的皮肤，验收配置不触碰用户数据。
        installed=Path(r'D:\个人项目\20260808 地面站教学\AZIMUTH · 方位角\AZIMUTH\themes')
        if installed.is_dir():shutil.copytree(installed,Path(folder)/'themes',dirs_exist_ok=True)
        T.load_app_fonts();app.setFont(QFont(T.pick_font()))
        win=MainWindow();win.resize(1584,992);win.show();win.switch(2);QTest.qWait(200)
        workspace=win.p_know.teaching_workspace
        try:
            for theme in ('default','chiyo','anna'):
                win.set_app_theme(theme)
                for appearance in ('light','dark'):
                    win._apply_theme(appearance,pref=appearance);win.switch(2)
                    workspace.select_chapter('bearing');workspace.player.seek(1,.5)
                    QTest.qWait(100);win.grab().save(str(output/f'{theme}-{appearance}.png'))
                    for width,height in ((1024,760),(1280,800),(1584,992)):
                        win.resize(width,height);QTest.qWait(50)
                        rect=workspace.footer.geometry()
                        assert rect.bottom()<=workspace.height(),f'{theme} {appearance} 播放栏越界'
                        records.append({'theme':theme,'appearance':appearance,'size':[win.width(),win.height()],'workspace':[workspace.width(),workspace.height()],'footer_bottom':rect.bottom()})
                    win.switch(3);QTest.qWait(50);win.switch(2)
            for key,_ in CHAPTERS:
                workspace.select_chapter(key)
                for i in range(len(workspace.player.lesson.steps)):
                    workspace.player.seek(i,.5);app.processEvents();assert not workspace.canvas.grab().isNull()
                workspace.player.seek(min(3,len(workspace.player.lesson.steps)-1));QTest.qWait(30)
                win.grab().save(str(output/f'chapter-{key}.png'))
            for shape in SHAPE_KEYS:
                workspace.select_chapter('geometry');workspace.parameters['shape']=shape
                workspace._rebuild_form();workspace.apply_conditions()
                workspace.player.seek(len(workspace.player.lesson.steps)-2)
                assert workspace.player.lesson.id=='geometry:'+shape
            for case in ('p1','p2','p4'):
                from teaching_cases import CASES
                assert Path(T.asset_path('teaching','cases',f'page-{CASES[case]["page"]}.png')).is_file()
                assert build_course('cases',{'case':case}).warnings
            workspace.mode.setCurrentIndex(1)
            for q,w in zip(workspace.player.lesson.questions,workspace.quiz_inputs):w.setText(str(q.answer))
            workspace.submit_answers();assert '项正确' in workspace.quiz_result.text()
            workspace.mode.setCurrentIndex(2);workspace.start_exam();workspace.exam_started-=181;workspace._exam_tick();assert not workspace.exam_active
            workspace.mode.setCurrentIndex(0)
            win.switch(0);QTest.qWait(60);win.switch(1);QTest.qWait(60);win.switch(4);QTest.qWait(60)
        except Exception as error:
            import traceback
            errors.append(traceback.format_exc())
        finally:
            costs=sorted(workspace.canvas.paint_costs)
            records.append({'paint_samples':len(costs),'paint_p95_ms':costs[int(len(costs)*.95)] if costs else 0})
            win.close();app.processEvents();cfg._path,cfg._cache=previous
    (output/'result.json').write_text(json.dumps({'ok':not errors,'records':records,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if not errors else 2


def run(app,output):
    from teaching_studio_qa import run as check_studio
    return check_studio(app,output)
