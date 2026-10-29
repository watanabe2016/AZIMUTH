"""250 项可追溯的动效契约；编号只按真实交互计数。"""

def rows(page, text):
    result = []
    for index, line in enumerate(text.strip().splitlines(), 1):
        label, owner, target, trigger, recipe = line.split("|")
        result.append(dict(id=f"{page}-{index:02d}", page=page, label=label,
                           owner=owner, target=target, trigger=trigger, recipe=recipe))
    if len(result) != 50:
        raise ValueError(f"{page} 应包含 50 项，实际为 {len(result)}")
    return result


HOME = rows("home", """
页面入场|WebHome|#stage|entrance|reveal
主标题显现|WebHome|h1|entrance|wipe
标题装饰线|WebHome|h1 .accent|entrance|underline
副标题错峰|WebHome|.hero-subtitle,.intro p|entrance|reveal
主视觉柔和显现|WebHome|#motion-hero-art,.chiyo-cover|entrance|reveal
主视觉跟手视差|WebHome|.masthead|pointer|follow
几何装饰入场|WebHome|.anna-card-motifs i|entrance|reveal
签名描画|WebHome|.cover-note|entrance|wipe
点阵渐显|WebHome|.masthead|entrance|dots
滚动主视觉视差|WebHome|.masthead|scroll|follow
主按钮悬停|WebHome|.start-link|pointerenter|fill
主按钮按压|WebHome|.start-link|pointerdown|press
主按钮箭头|WebHome|.start-link .arrow|pointerenter|arrow
主按钮键盘焦点|WebHome|.start-link|focus|focus
不可用状态|WebHome|#review-button|dashboard|disabled
功能卡片错峰|WebHome|.card|entrance|reveal
功能卡悬停|WebHome|.card|pointerenter|lift
功能图标描画|WebHome|.card svg|entrance|draw
功能说明显现|WebHome|.card__subtitle|entrance|reveal
卡片按钮按压|WebHome|.card__action|pointerdown|press
统计数字递增|WebHome|.metric__number|dashboard|count
统计变更翻动|WebHome|.metric__number|dashboard|flip
统计插图显现|WebHome|.visual|entrance|reveal
状态标签更新|WebHome|#review-label|dashboard|change
零数据提示|WebHome|#footnote|dashboard|empty
继续练习恢复|WebHome|#start-label|dashboard|change
题号更新|WebHome|#start-label|dashboard|flip
错题入口状态|WebHome|#review-button|dashboard|change
进度完成勾选|WebHome|#done-count|dashboard|check
练习进度庆祝|WebHome|#done-count|dashboard|spark
导航选中条移动|MainWindow|nav_plate|method:_move_nav_plate|native
导航图标反馈|MainWindow|navs.0|enter|native
页面标题衔接|MainWindow|status|method:switch|change
卡片到题库衔接|MainWindow|navs.1|method:start_practice|arrow
返回首页交接|MainWindow|navs.0|method:switch|native
卡片滚动显现|WebHome|.card|intersection|reveal
顶部区域收拢|WebHome|.masthead|scroll|compact
返回顶部按钮|WebHome|#motion-top|scroll|reveal
滚动进度|WebHome|#motion-scroll-progress|scroll|progress
页脚显现|WebHome|#footnote|intersection|reveal
主题背景切换|WebHome|#stage|theme|change
主题主视觉替换|WebHome|#motion-hero-art,.chiyo-cover|theme|reveal
主题卡片衔接|WebHome|.card|theme|change
装饰延迟显现|WebHome|.anna-home-motifs,.cover-note|theme|reveal
空题库引导|WebHome|#footnote|dashboard|empty
数据加载提示|WebHome|#motion-loading|load|loading
图片加载完成|WebHome|.visual|load|reveal
窗口布局变化|WebHome|.cards|resize|reflow
操作提示|WebHome|#motion-toast|action|toast
首页位置恢复|WebHome|#stage|restore|restore
""")

BANK = rows("bank", """
题库页入场|BankPage|heading_widget|method:play_intro|line
搜索焦点|BankPage|q|focus|focus
搜索结果更新|BankPage|filter_summary|method:apply_filter|change
匹配关键词|BankPage|d_title|method:_highlight_query|line
清空搜索|BankPage|q|method:clear_filters|scan
没有匹配结果|BankPage|d_title|method:show_empty|error
筛选展开收起|BankPage|filter_panel|method:_animate_filter_panel|native
筛选条件更新|BankPage|filter_summary|method:_on_tags_changed|chips
筛选数量更新|BankPage|filter_summary|method:refresh_stats|change
清空筛选|BankPage|b_clear|press|press
目录展开关闭|BankPage|listw|method:_toggle_directory|native
当前题目高亮|BankPage|listw|signal:currentRowChanged|outline
目录悬停反馈|BankPage|listw|enter|corner
目录平滑定位|BankPage|listw|method:goto_question|native
匹配项定位|BankPage|q|method:_focus_search|focus
题目内容交接|BankPage|detail|method:on_select|native
前后题方向反馈|BankPage|d_title|method:step|arrow
随机题揭示|BankPage|d_title|method:random_pick|wipe
题目来源变更|BankPage|d_sub|method:_finish_question_detail|change
题号变更|BankPage|d_title|method:_finish_question_detail|flip
原图加载显现|BankPage|fig|method:_show_figure|change
A4整页适配|BankPage|fig|method:_fit_preview|corner
预览锚点缩放|BankPage|fig|method:_change_preview_zoom|arc
工作台全屏交接|BankPage|fig|method:_open_fullscreen_workbench|corner
预览布局变化|BankPage|fig|resize|corner
批注工具交接|FigureDialog|b_pen|method:set_tool|outline
笔迹即时跟手|FigureBoard|self|method:_append_stroke|native
笔迹颜色选择|FigureDialog|b_pen|method:set_color|dot
笔迹宽度预览|FigureDialog|width_lb|method:set_pen_width|wave
橡皮路径反馈|FigureBoard|self|method:_finish_stroke|line
撤销重做反馈|FigureBoard|self|method:undo|corner
备忘录打开|BankPage|b_note|method:open_note|arrow
参考答案展开|RefAnswerPanel|answer_stack|method:toggle_answer_expanded|native
答案内容显现|RefAnswerPanel|answer_info|method:_on_vision_completed|change
避让点选择焦点|AnswerPanel|avoid_pick|focus|focus
扫描进行反馈|FigureDialog|b_judge|method:auto_scan|scan
扫描完成反馈|AnswerPanel|title|method:fill_from|check
判题结果显现|FigureDialog|b_judge|method:_on_judged|check
判题错误提示|FigureDialog|b_judge|method:_gcs_alert|error
判分依据说明|JudgeDialog|card|show|line
标记题目已做|FigureDialog|b_done|method:toggle_done|check
取消已做状态|BankPage|b_done|method:_toggle_current_done|outline
答题记录保存|BankPage|filter_summary|method:commit_record|stamp
参考参数更新|AnswerPanel|title|method:_rebuild_body|line
避让过渡点选择|AnswerPanel|avoid_pick|method:_avoid_pick_changed|chips
导入入口响应|BankPage|b_import|press|press
导入批次状态|BankPage|b_import|method:_set_import_phase|progress
识别阶段进度|BankPage|b_import|method:_on_batch_pdf_step|scan
导入完成汇总|BankPage|filter_summary|method:_finish_import_batch|check
取消失败提示|BankPage|b_import|method:_abort_import_batch|error
""")

TIMER = rows("timer", """
模拟页进入|TimerPage|heading_widget|method:play_intro|line
阅题阶段点亮|TimerPage|chips.read|method:start_session|outline
作答阶段交接|TimerPage|chips.do|method:_enter_doing|outline
完成阶段落定|TimerPage|chips.lock|method:_to_lock|check
阶段指示线|TimerPage|phase_relay|method:_set_chips|native
计时环启动|TimerPage|ring|method:start_session|native
真实时间进度弧|RingTimer|self|method:set_remain|native
秒数字切换|RingTimer|self|method:set_remain|native
阶段数字交接|TimerPage|elapsed|method:_sync_ring_copy|change
最后十秒提示|TimerPage|countdown_runway|method:_last_seconds_feedback|native
阅题时长选择|TimerPage|read_btns.60|press|press
作答时长选择|TimerPage|do_btns.180|press|press
自定义时长展开|TimerPage|custom_duration|method:toggle_custom_duration|native
开始按钮响应|TimerPage|b_start|press|press
暂停按钮文案交接|TimerPage|b_pause|method:toggle_pause|change
暂停状态提示|TimerPage|hint|method:toggle_pause|change
计时恢复反馈|TimerPage|ring|method:toggle_pause|native
重置进度回退|TimerPage|ring|method:reset|native
追加一分钟|TimerPage|ring|method:extend_time|native
跳过阶段|TimerPage|chips.read|method:skip_stage|arrow
自动进入作答|TimerPage|hint|method:_to_doing|change
完成下一轮入口|TimerPage|b_start|method:_to_lock|outline
阶段说明更新|TimerPage|rec|method:_sync_ring_copy|change
提示音开关|TimerPage|chk_sound|signal:toggled|wave
自动流程开关|TimerPage|chk_auto|signal:toggled|arrow
音效试听波形|SettingsPage|b_sound_preview|method:_preview_sound|wave
音量反馈|SettingsPage|sound_slider|signal:sliderReleased|wave
音效选项反馈|SettingsPage|sound_ui_label|method:_on_sound_selection|change
到点锁定提示|TimeUpDialog|self|show|corner
解锁确认反馈|TimeUpDialog|btn|press|check
时间到窗口进入|TimeUpDialog|self|method:_animate_visibility|native
主题人物进入|TimeUpDialog|self|method:showEvent|dot
确认按钮反馈|TimeUpDialog|btn|focus|focus
时间到窗口退出|TimeUpDialog|self|method:_ok|native
桌面时钟显现|ClockBubble|btn_toggle|show|outline
小窗拖动响应|ClockBubble|self|method:mouseReleaseEvent|corner
小窗开关交接|ClockBubble|self|show|line
人物菜单反馈|ClockBubble|btn_stage|press|arrow
当前阶段数字|ClockBubble|btn_toggle|method:set_time|change
已练次数更新|TimerPage|rec|method:_save_session_result|stamp
历史记录进入|TimerPage|history_panel|method:toggle_history|native
模拟总结展开|TimerPage|history_label|method:toggle_history|line
练习记录详情|TimerPage|history_label|method:_save_session_result|change
用时统计显现|TimerPage|elapsed|method:_save_session_result|flip
计时环主题交接|TimerPage|ring|method:theme_changed|native
导入人物解锁|SettingsPage|clock_unlock_hint|method:_reload_clock_skins|check
人物切换|ClockBubble|self|method:set_skin|change
键盘操作反馈|TimerPage|b_start|focus|focus
关闭提醒恢复|TimerPage|hint|method:lock_stop|change
减少动态效果|SettingsPage|chk_motion|method:_on_motion_switch|native
""")

SETTINGS = rows("settings", """
设置页面进入|SettingsPage|heading_widget|method:play_intro|line
分类选中指示|SettingsPage|settings_navigation|method:_select_settings_group|outline
分类内容交接|SettingsPage|page_title|method:_select_settings_group|change
设置卡片错峰|SettingsPage|_cards.0|method:play_intro|corner
分类滚动提示|SettingsPage|settings_navigation|enter|line
费用区域折叠|SettingsPage|api_pricing_panel|method:_toggle_api_pricing|native
设置帮助展开|SettingsPage|wallpaper_hint|method:_sync_wallpaper_label|change
展开按钮反馈|SettingsPage|b_pricing_toggle|press|arrow
折叠中断连续|SettingsPage|b_pricing_toggle|method:_toggle_api_pricing|outline
展开布局跟随|SettingsPage|api_pricing_panel|resize|native
主题卡选中描边|SettingsPage|theme_host|method:_move_theme_indicator|native
主题预览交接|SettingsPage|theme_host|method:set_theme_state|change
当前主题标记|SettingsPage|app_theme_label|method:set_app_theme_state|check
主题键盘焦点|SettingsPage|_app_theme_btns.default|focus|focus
浅深色选择反馈|SettingsPage|theme_label|method:set_theme_state|line
壁纸信息显现|SettingsPage|wallpaper_path_label|method:_choose_wallpaper|change
壁纸预览替换|SettingsPage|wallpaper_path_label|method:_sync_wallpaper_label|wipe
透明度调整|SettingsPage|wallpaper_opacity_slider|signal:sliderReleased|progress
模糊度调整|SettingsPage|wallpaper_blur_slider|signal:sliderReleased|wave
恢复默认壁纸|SettingsPage|b_wallpaper_clear|method:_clear_wallpaper|check
声音主开关|SettingsPage|chk_sound_master|signal:toggled|outline
阶段音效开关|SettingsPage|chk_sound_stage|signal:toggled|dot
试听提示反馈|SettingsPage|b_sound_preview|press|wave
音量状态更新|SettingsPage|sound_volume_label|method:_on_sound_volume_preview|change
界面音效状态|SettingsPage|sound_ui_label|method:_on_sound_mode|change
主题包选择反馈|SettingsPage|b_skin_import|press|press
主题包导入状态|SettingsPage|page_subtitle|method:_import_skin|check
新主题预览显现|SettingsPage|custom_theme_host|method:_reload_custom_themes|corner
人物解锁提示|SettingsPage|clock_unlock_hint|method:_import_skin|check
主题导入键盘焦点|SettingsPage|b_skin_import|focus|focus
主题导出反馈|SettingsPage|b_skin_template|press|press
主题模板完成|SettingsPage|page_subtitle|method:_export_skin_template|stamp
主题模板路径提示|SettingsPage|b_skin_template|method:_export_skin_template|line
服务商切换|SettingsPage|vision_api_provider|method:_on_vision_provider_changed|outline
API地址输入焦点|SettingsPage|vision_api_url_edit|focus|focus
API密钥输入焦点|SettingsPage|vision_api_key_edit|focus|focus
API测试启动|SettingsPage|b_vision_api_check|method:_check_vision_api|scan
API结果状态|SettingsPage|vision_api_status|method:_on_vision_api_checked|change
API测试结束提示|SettingsPage|vision_api_status|method:_on_vision_api_check_finished|line
费用设置变更|SettingsPage|vision_api_budget|focus|focus
日志导出反馈|SettingsPage|page_subtitle|method:do_export|stamp
打开日志反馈|SettingsPage|page_subtitle|method:open_logs|line
维护操作状态|SettingsPage|page_subtitle|method:set_status|change
清理记录按钮|SettingsPage|b_pr_clear|press|press
清理完成状态|SettingsPage|pr_lb|method:do_clear_progress|change
赞赏码外观切换|SettingsPage|reward_code|method:_refresh_reward_code|native
赞赏区显示|SettingsPage|reward_support_card|method:_select_settings_group|native
设置保存状态|SettingsPage|page_subtitle|method:_save_vision_api|check
动效偏好更新|SettingsPage|chk_motion|signal:toggled|native
设置位置恢复|SettingsPage|settings_navigation|method:on_show|line
""")

TEACHING = rows("teaching", """
教学页面进入|KnowledgePage|page_title|method:play_intro|line
课程卡片错峰|KnowledgePage|feature|method:play_intro|corner
图形卡悬停|KnowledgePage|shape_nav|enter|halo
当前图形标记|KnowledgePage|shape_nav|method:select_shape|outline
继续学习进度|KnowledgePage|course_progress|show|progress
基础知识展开|KnowledgePage|details_panel|method:_toggle_details|native
章节完成记录|KnowledgePage|course_progress|method:complete_lesson|stamp
章节高亮指示|KnowledgePage|hero_title|method:select_shape|line
学习进度变化|KnowledgePage|course_progress|method:complete_lesson|change
图形详情交接|KnowledgePage|content_motion_layer|method:select_shape|native
航线逐边描绘|FlightRouteCanvas|self|method:replay|native
航点依次落位|FlightRouteCanvas|self|method:_advance|native
点位选择反馈|FlightRouteCanvas|self|method:mousePressEvent|halo
边长标记显现|FlightRouteCanvas|self|method:toggle_measurements|line
图形线条切换|FlightRouteCanvas|self|method:set_shape|wipe
角度弧显现|FlightRouteCanvas|self|method:toggle_angles|arc
边段选择高亮|FlightRouteCanvas|self|method:select_segment|line
边距标注反馈|FlightRouteCanvas|self|method:toggle_measurements|corner
点位编号显现|KnowledgePage|hero_meta|method:_apply_shape_content|change
图形重置反馈|KnowledgePage|hero_figure|method:reset_demo|corner
航线轨迹连续|FlightRouteCanvas|self|method:_advance|native
罗盘最短寻向|FlightRouteCanvas|self|method:_turn|native
当前航段说明|KnowledgePage|demo_caption|method:update_demo_caption|change
当前航点提示|FlightRouteCanvas|self|method:select_segment|dot
单步航点推进|FlightRouteCanvas|self|method:step_forward|native
风向矢量演示|LessonConceptCanvas|self|method:replay|native
风速矢量反馈|LessonConceptCanvas|self|method:set_strength|native
高度剖面绘制|LessonConceptCanvas|self|method:set_mode|native
爬升下降演示|LessonConceptCanvas|self|method:step_forward|native
高度步骤标签|KnowledgePage|demo_caption|method:step_demo|change
坐标方向定位|LessonConceptCanvas|self|method:set_mode|native
坐标航点联动|LessonConceptCanvas|self|method:replay|native
空域边界展开|FlightRouteCanvas|self|method:set_zone|native
限制区域显现|KnowledgePage|demo_zone|signal:toggled|outline
空域提示位置|FlightRouteCanvas|self|method:_expand_zone|native
过渡点绕行|LessonConceptCanvas|self|method:set_mode|native
过渡路径推进|LessonConceptCanvas|self|method:step_forward|native
限制区边界提示|LessonConceptCanvas|self|method:toggle_pause|corner
安全路线重绘|KnowledgePage|diagram_surface|method:_replay_demo|line
教学演示完成|KnowledgePage|demo_caption|method:complete_lesson|check
播放暂停交接|KnowledgePage|demo_pause|method:pause_demo|change
暂停轨迹冻结|FlightRouteCanvas|self|method:toggle_pause|native
教学单步控制|KnowledgePage|demo_step|press|press
重播轨迹交接|KnowledgePage|demo_replay|method:_replay_demo|arrow
演示速度选择|KnowledgePage|demo_speed|method:set_demo_speed|line
A4学习卡展开|KnowledgePage|demo_card|method:open_lesson_card|corner
学习卡等比缩放|LessonPreviewDialog|preview|method:zoom_by|native
学习卡整页适配|LessonPreviewDialog|preview|method:fit_page|native
课程说明收起|KnowledgePage|lesson_toggle|press|arrow
完成课程落章|KnowledgePage|b_complete|method:complete_lesson|check
""")

ALL = HOME + BANK + TIMER + SETTINGS + TEACHING
NATIVE = [entry for entry in ALL if entry["owner"] != "WebHome"]
