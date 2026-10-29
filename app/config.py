# -*- coding: utf-8 -*-
"""
配置持久化 —— 主题、UI 缩放、桌面时钟开关等
存放位置：程序目录/config.json（绿色版随身走），失败则退回用户目录
"""
import os
import json
import copy
import math
import re
from storage import writable_directory, atomic_json
from secret_store import protect_secret, reveal_secret

from logger import get_logger, app_root

log = get_logger()

DEFAULTS = {
    "theme": "system",          # light / dark / system
    "app_theme": "default",    # default / chiyo，与外观模式独立
    "wallpaper_path": "",
    "wallpaper_opacity": 38,
    "wallpaper_blur": 0,
    "card_layout": "standard", # standard / paper（广东02题卡样式）
    "ui_scale": 1.0,            # 全局 UI 缩放
    "clock_on": False,          # 桌面倒计时时钟开关
    "clock_skin": "default",   # 独立选择已导入主题解锁的时钟人物
    "clock_scale": 1.2,         # 时钟缩放
    "clock_x": -1,              # 时钟位置（-1 表示未设置，用默认右下）
    "clock_y": -1,
    "shadow_on": True,          # 窗口阴影开关
    "animations_on": True,      # 界面动效开关；同时尊重系统的减少动画设置
    "shadow_strength": 1.0,     # 窗口阴影强度（0.0 ~ 1.5，1.0 为默认）
    "read_sec": 60,             # 阅题时长
    "do_sec": 360,              # 作答时长
    # 打开题卡后看满这么多秒才记一次「做过」。填 0 退回「一打开就记」。
    # 为什么要有它：以前瞄一眼也被算成练过，进度虚高。
    "record_delay_sec": 60,
    "sound": True,
    "sound_master": True,
    "sound_ui": True,
    "sound_stage": True,
    "sound_volume": 35,
    "sound_settings_version": 1,
    "sound_hidden": [],
    "auto_next": True,
    "disclaimer_ok": False,     # 是否已确认免责声明
    "vision_api_disclaimer_ack": False,

    # ---- 做题记录 ----
    # done_ids: 做过的题目 id 列表；history: {"<id>": {"count": n, "last": "时间"}}
    # 两条一起维护，单独存 history 是为了能显示"做过几次、最后什么时候做的"
    "done_ids": [],
    "history": {},
    # wrong: 错题集 {"<id>": {"n": 错几次, "last": 时间, "detail": "错在哪"}}
    # 判题不通过才会写进来；下次判对了自动移除
    "wrong": {},

    # trend: 成绩趋势（按日）{"2026-09-20": {"ok": 对几次, "bad": 错几次, "qs": [题id]}}
    # history 只有"最后时间 + 总次数"，画不出逐日曲线，所以趋势另记一份
    "trend": {},

    # ---- 题目元数据（用户自己标的）----
    # ★ 为什么放 config 而不是 bankdata：用户导入的题卡也要能标，
    #   而 bankdata 只管用户导入的题（程序资源目录只读、不能保存用户标注）。
    #   结构跟 done_ids / wrong 一致：{"<题id>": 数据}
    #
    # manual_tags: 手动考点标签 {"<id>": ["clock","compass", ...]}
    #   软件的自动分类认不出来（或认错）时，用户自己勾。与自动标签取并集。
    "manual_tags": {},
    # manual_tags_off: 手动**排除**的标签 {"<id>": ["wind", ...]}
    #   v1.9.11：右键菜单里取消勾选自动认出的标签时记到这里 ——
    #   只记"并集追加"的话，用户永远关不掉认错的自动标签。
    #   排除是显式覆盖：就算将来规则改了重新认出来，也照样被压住。
    "manual_tags_off": {},
    # answer_text: 手动标注的参考答案 {"<id>": "文本"}
    #   用户填了就优先显示它；没填才显示 answers.derive() 自动算的。
    "answer_text": {},
    # verified_references: 作答经题面条件独立复核后形成的结构化参考答案。
    # 题面改动或求解器规则升级时，以指纹失效，避免沿用旧坐标。
    "verified_references": {},
    # 避让点手动校正：题号 -> 图形点号。空白表示使用题干推断。
    "avoid_point_overrides": {},

    # ---- 视觉模型 API ----
    "vision_api_provider": "deepseek",
    "vision_api_base_url": "https://api.deepseek.com",
    "vision_api_model": "deepseek-flash",
    "vision_api_key": "",
    "vision_api_price_model": "deepseek-flash",
    "vision_api_currency": "USD",
    "vision_api_input_price_per_million": 0.30,
    "vision_api_cached_input_price_per_million": 0.006,
    "vision_api_output_price_per_million": 1.20,
    "vision_api_spend_total": 0.0,
    "vision_api_spend_by_currency": {},
    "vision_api_budget_amount": 0.0,
    "vision_api_call_count": 0,

    # ---- LinkGCS2（第三方地面站软件）----
    # 读它的任务库来判题，不用用户把坐标再抄一遍。
    # linkgcs_path 留空 = 自动找（先问它的 ini，再试几个常规目录）
    "linkgcs_path": "",
}

_cache = None
_path = None


def _migrate_visual_source(value):
    """把旧版视觉结果来源标签升级为不绑定部署方式的名称。"""
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key in ("source", "graph_source") and item == "local_vlm":
                value[key] = "vision_model"
            else:
                _migrate_visual_source(item)
    elif isinstance(value, list):
        for item in value:
            _migrate_visual_source(item)


def config_path():
    global _path
    if _path is None:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        directory = writable_directory(app_root(), os.path.join(base, "CAAC Ground Station"))
        _path = os.path.join(directory, "config.json")
    return _path


def load():
    global _cache
    if _cache is not None:
        return _cache
    d = copy.deepcopy(DEFAULTS)
    stored = {}
    p = config_path()
    if not os.path.exists(p):
        p = os.path.join(app_root(), "config.json")
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                stored = json.load(f)
                if isinstance(stored, dict):
                    d.update(stored)
    except Exception as e:
        log.warning("配置读取失败，用默认值：%r", e)
    _migrate_visual_source(d)
    # 旧版默认关闭按钮音效，容易让用户误以为整个音效系统失效。
    # 本版首次升级主动启用轻触反馈；之后仍尊重用户自行关闭的选择。
    try:
        sound_version = int(stored.get("sound_settings_version", 0))
    except (AttributeError, TypeError, ValueError):
        sound_version = 0
    if sound_version < 1:
        d["sound_ui"] = True
        d["sound_settings_version"] = 1
    # 旧版本将桌面时钟误拆成“桌面宠物”；把原有开启意图和位置合并回唯一时钟。
    if d.get("pet_on"):
        d["clock_on"] = True
        for key in ("x", "y"):
            if d.get("clock_" + key, -1) == -1 and d.get("pet_" + key, -1) != -1:
                d["clock_" + key] = d["pet_" + key]
    d.pop("pet_on", None)
    d.pop("pet_style", None)
    d.pop("pet_x", None)
    d.pop("pet_y", None)
    # 移除旧版本地模型开关和地址，升级后统一使用用户配置的 API。
    d.pop("vision_model_enabled", None)
    d.pop("vision_model_base_url", None)
    d.pop("vision_model_name", None)
    # 分发后可能读到旧版或手工改过的配置，按类型回退而不丢弃其他记录。
    for key, default in DEFAULTS.items():
        if isinstance(default, (dict, list, bool)) and not isinstance(d[key], type(default)):
            d[key] = copy.deepcopy(default)
    for key, low, high in (("ui_scale", .8, 1.4), ("clock_scale", .85, 2),
                           ("shadow_strength", 0, 1.5), ("read_sec", 0, 7200),
                           ("do_sec", 1, 7200), ("record_delay_sec", 0, 7200)):
        try:
            value = float(d[key])
            if not math.isfinite(value):
                raise ValueError(key)
            d[key] = max(low, min(high, value))
        except (ValueError, TypeError):
            d[key] = DEFAULTS[key]
    for key in ("read_sec", "do_sec", "record_delay_sec", "clock_x", "clock_y"):
        try:
            d[key] = int(d[key])
        except (ValueError, TypeError, OverflowError):
            d[key] = DEFAULTS[key]
    if d.get("theme") not in ("system", "light", "dark"):
        d["theme"] = "system"
    app_theme = d.get("app_theme")
    if (app_theme not in ("default", "chiyo", "industrial") and
            not (isinstance(app_theme, str) and
                 re.fullmatch(r"skin:[a-z0-9][a-z0-9-]{0,47}", app_theme))):
        d["app_theme"] = "default"
    if d.get("card_layout") not in ("standard", "paper"):
        d["card_layout"] = "standard"
    clock_skin = d.get("clock_skin")
    if (clock_skin not in ("default", "chiyo", "anna", "industrial") and
            not (isinstance(clock_skin, str) and
                 re.fullmatch(r"skin:[a-z0-9][a-z0-9-]{0,47}", clock_skin))):
        d["clock_skin"] = "default"
    for key in ("sound_volume",):
        try:
            d[key] = max(0, min(100, int(d[key])))
        except (TypeError, ValueError, OverflowError):
            d[key] = DEFAULTS[key]
    for key, high in (("wallpaper_opacity", 100), ("wallpaper_blur", 24)):
        try:
            d[key] = max(0, min(high, int(d[key])))
        except (TypeError, ValueError, OverflowError):
            d[key] = DEFAULTS[key]
    if not isinstance(d.get("wallpaper_path"), str):
        d["wallpaper_path"] = ""
    if d.get("vision_api_provider") not in ("openai", "deepseek", "custom"):
        d["vision_api_provider"] = DEFAULTS["vision_api_provider"]
    for key in ("vision_api_base_url", "vision_api_model"):
        if not isinstance(d.get(key), str) or not d[key].strip():
            d[key] = DEFAULTS[key]
        else:
            d[key] = d[key].strip()
    if not isinstance(d.get("vision_api_key"), str):
        d["vision_api_key"] = ""
    for key in ("vision_api_price_model",):
        if not isinstance(d.get(key), str):
            d[key] = DEFAULTS[key]
        d[key] = d[key].strip()[:120] or DEFAULTS[key]
    if d.get("vision_api_currency") not in ("USD", "CNY", "EUR", "GBP"):
        d["vision_api_currency"] = DEFAULTS["vision_api_currency"]
    for key in ("vision_api_input_price_per_million",
                "vision_api_cached_input_price_per_million",
                "vision_api_output_price_per_million",
                "vision_api_spend_total", "vision_api_budget_amount"):
        try:
            value = float(d[key])
            d[key] = max(0.0, min(1000000.0, value)) if math.isfinite(value) else DEFAULTS[key]
        except (TypeError, ValueError, OverflowError):
            d[key] = DEFAULTS[key]
    spend_map = d.get("vision_api_spend_by_currency")
    if not isinstance(spend_map, dict):
        spend_map = {}
    clean_spend = {}
    for currency in ("USD", "CNY", "EUR", "GBP"):
        try:
            amount = float(spend_map.get(currency, 0) or 0)
            clean_spend[currency] = max(0.0, min(1000000.0, amount))
            if not math.isfinite(amount):
                clean_spend[currency] = 0.0
        except (TypeError, ValueError, OverflowError):
            clean_spend[currency] = 0.0
    d["vision_api_spend_by_currency"] = clean_spend
    try:
        d["vision_api_call_count"] = max(0, min(100000000,
                                               int(d["vision_api_call_count"])))
    except (TypeError, ValueError, OverflowError):
        d["vision_api_call_count"] = DEFAULTS["vision_api_call_count"]
    _cache = d
    return _cache


def get(key, default=None):
    return load().get(key, DEFAULTS.get(key, default))


def set_(key, value, save_now=True):
    load()[key] = value
    if save_now:
        save()


def get_vision_api_key():
    """读取用户设置的视觉 API 密钥。"""
    return reveal_secret(get("vision_api_key", ""))


def set_vision_api_key(value):
    """保存视觉 API 密钥；Windows 设置文件只保存 DPAPI 密文。"""
    set_("vision_api_key", protect_secret(value))


def save():
    p = config_path()
    try:
        atomic_json(p, load())
        return True
    except Exception as e:
        log.warning("配置写入失败：%r", e)
        return False


def reset():
    global _cache
    _cache = copy.deepcopy(DEFAULTS)
    save()
