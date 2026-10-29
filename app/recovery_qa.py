"""隔离验收正式功能、设置页和最新桌面时钟，不调用付费 API。"""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import time
import traceback


def run(app, output):
    from PySide6.QtCore import QPoint, QTimer, QTime, Qt
    from PySide6.QtGui import QCursor, QFont
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QTimeEdit
    import config as cfg
    import bankdata as BD
    import theme as T
    from logger import app_root
    from main_window import MainWindow
    import developer_protocols
    import plugin_manager
    import plugin_runtime

    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    previous = cfg._path, cfg._cache, BD._root
    errors, records = [], []
    win = None
    original_hook = __import__('sys').excepthook

    def collect(kind, value, trace):
        errors.append(''.join(traceback.format_exception(kind, value, trace)))

    __import__('sys').excepthook = collect
    app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory(prefix='azimuth-recovery-qa-') as folder:
        folder = Path(folder)
        cfg._path = str(folder / 'config.json')
        cfg._cache = copy.deepcopy(cfg.DEFAULTS)
        cfg._cache.update(disclaimer_ok=True, animations_on=True,
                          sound=False, sound_master=False, clock_on=True)
        bank = folder / 'data' / 'bank'
        bank.mkdir(parents=True)
        BD._root = str(bank)
        source = Path(app_root())
        for name in ('themes',):
            if (source / name).is_dir():
                shutil.copytree(source / name, folder / name)
        try:
            T.load_app_fonts()
            app.setFont(QFont(T.pick_font()))
            win = MainWindow()
            win.prepare_startup()
            win.show()
            win.first_intro()
            QTest.qWait(1200)
            assert win.stack.currentIndex() == 4
            deadline = time.monotonic() + 12
            while not win.p_home._loaded and time.monotonic() < deadline:
                QTest.qWait(40)
            assert win.p_home._loaded, '首页网页没有加载成功'
            win.grab().save(str(out / 'home.png'))
            assert developer_protocols.plugin_protocol()
            assert developer_protocols.skin_protocol()
            assert plugin_manager and plugin_runtime
            records.append('完整首页、插件管理运行模块、开发协议存在')
            for mode in ('light', 'dark'):
                win.set_theme(mode)
                QTest.qWait(1000)
                for index, name in ((3, 'settings'), (2, 'teaching'),
                                    (1, 'bank'), (0, 'timer'), (4, 'home')):
                    win.switch(index)
                    QTest.qWait(950)
                    assert win.stack.currentIndex() == index, name
                    if index == 3:
                        for group in ('appearance', 'timer', 'bank', 'api',
                                      'developer', 'maintenance'):
                            win.p_set._select_settings_group(group, save=False)
                            QTest.qWait(140)
                            win.p_set.grab()
                        win.p_set._select_settings_group('appearance', save=False)
                    win.grab().save(str(out / f'{name}-{mode}.png'))
                    records.append(f'{mode} {name} 切换与绘制通过')

            clock = win.clock
            assert clock is not None
            assert len(clock._controls_buttons) == 4
            assert clock._controls_hide_timer.interval() == 1000
            clock.set_time(3661, 3661, '自定义倒计时', False)
            assert (clock.hours, clock.minutes, clock.seconds) == (1, 1, 1)
            for scale in (.85, 1.0, 1.4):
                clock.set_scale(scale)
                buttons = (clock.btn_stage, clock.btn_custom,
                           clock.btn_reset, clock.btn_toggle)
                assert len({b.x() for b in buttons}) == 1
                assert all(a.y() < b.y() for a, b in zip(buttons, buttons[1:]))
                assert clock.btn_toggle.width() <= max(24, round(30 * scale))
                assert f'border-radius:{clock.btn_toggle.width() // 2}px' in clock.btn_toggle.styleSheet()
                assert clock._bubble_rect().contains(clock._bubble_rect().center())
                clock.grab().save(str(out / f'clock-{scale}.png'))
            clock.set_scale(1.0)
            assert 'border-radius:15px' in clock.btn_toggle.styleSheet(), '缩小时圆形按钮变成方形'
            inside = clock.mapToGlobal(clock._character_hover_rect().center().toPoint())
            assert clock._controls_hovered(inside), '人物悬停区域无法触发按钮'
            # 隔离真实鼠标移动，避免用户同时操作桌面干扰延时验收。
            clock._controls_hovered = lambda point=None: True
            clock._refresh_control_hover()
            QTest.qWait(400)
            assert all(b._reveal_progress > .99 for b in clock._controls_buttons)
            clock.grab().save(str(out / 'clock-controls.png'))
            clock._controls_hovered = lambda point=None: False
            clock._refresh_control_hover()
            QTest.qWait(600)
            assert clock._controls_target_visible, '按钮在一秒前收回'
            QTest.qWait(850)
            assert not clock._controls_bar.isVisible(), '一秒延迟后按钮没有退场'
            del clock._controls_hovered
            records.append('左下小圆按钮竖排、错峰出入场、一秒延迟、小时显示通过')

            timer = win.p_timer
            timer.set_read_len(0)
            timer.set_do_len(180)
            win._handle_clock_action('toggle')
            assert timer.phase == timer.DOING and timer.tick.isActive()
            win._handle_clock_action('toggle')
            assert not timer.tick.isActive()
            win._handle_clock_action('toggle')
            assert timer.tick.isActive()
            win._handle_clock_action('reset')
            assert timer.phase == timer.IDLE and timer.remain == 180
            assert clock.minutes == 3 and clock.seconds == 0

            def accept_custom():
                dialog = app.activeModalWidget()
                assert isinstance(dialog, QDialog), '自定义时长窗口没有打开'
                dialog.findChild(QTimeEdit).setTime(QTime(0, 0, 20))
                dialog.accept()

            QTimer.singleShot(100, accept_custom)
            win._handle_clock_action('custom')
            assert timer._clock_custom_total == 20
            assert timer.phase == timer.DOING and timer.remain == 20
            QTest.qWait(1150)
            assert 17 <= timer.remain <= 19
            win._handle_clock_action('reset')
            assert timer.remain == 20 and clock.seconds == 20
            win._handle_clock_action('cycle_character')
            from skin_manager import valid_clock_skin
            assert cfg.get('clock_skin') == valid_clock_skin(cfg.get('clock_skin'))
            timer.set_do_len(300)
            assert timer._clock_custom_total is None
            assert timer.ring.total == 300
            records.append('开始、暂停、继续、重置、自定义计时、人物切换与模拟时长兼容通过')
            assert not errors, '\n'.join(errors)
        except Exception:
            errors.append(traceback.format_exc())
        finally:
            if win is not None:
                win.p_timer.tick.stop()
                win.p_timer.phase = win.p_timer.IDLE
                win.close()
                app.processEvents()
            cfg._path, cfg._cache, BD._root = previous
            __import__('sys').excepthook = original_hook
    (out / 'result.json').write_text(json.dumps(
        {'ok': not errors, 'records': records, 'errors': errors},
        ensure_ascii=False, indent=2), encoding='utf-8')
    return 2 if errors else 0
