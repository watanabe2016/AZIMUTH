# -*- coding: utf-8 -*-
"""
视觉主题 v2 —— 苹果液态玻璃（Liquid Glass）+ 浅色/深色/跟随系统

设计说明：
  Qt 在 Windows 上无法做真正的背景实时模糊，这里用
  「预模糊底图 + 半透明玻璃面板 + 高光描边 + 内外阴影」拟态实现。

主题机制：
  所有颜色由 PALETTES[theme] 提供，theme ∈ {light, dark}。
  「跟随系统」在启动时与运行中读取 Windows 注册表 AppsUseLightTheme 判定。
  stylesheet() 与 GLASS/btn 样式全部从当前调色板即时生成，
  切换主题时重新调用 build() 即可整屏换肤。
"""
import os
import sys
import json

# ---------------------------------------------------------------- 字体
# 无衬线优先：HarmonyOS Sans → 苹方 → 微软雅黑 UI → 思源黑体
SANS_STACK = '"HarmonyOS Sans SC", "PingFang SC", "Microsoft YaHei UI", ' \
             '"Source Han Sans SC", "Segoe UI", sans-serif'
MONO_STACK = '"JetBrains Mono", "Consolas", "Cascadia Mono", monospace'
# 桌面时钟字体：内置得意黑优先 → 中文手写体 → 英文字体兜底
HAND_STACK = '"Smiley Sans", "得意黑", "STXingkai", "华文行楷", ' \
             '"Segoe Script", "Ink Free", "Dancing Script", ' \
             '"Comic Sans MS", "KaiTi", "楷体", cursive'

# 随包分发的字体（家族名 -> assets 下的文件名）
BUNDLED_FONTS = {"Smiley Sans": "SmileySans-Oblique.ttf",
                 "Alibaba PuHuiTi 2.0": "AlibabaPuHuiTi-Bold.ttf"}
_bundled_loaded = []
_anna_font_loaded = False


def _families():
    try:
        from PySide6.QtGui import QFontDatabase
        return set(QFontDatabase.families())
    except Exception:
        return set()


_AVAIL = None


def avail():
    global _AVAIL
    if _AVAIL is None:
        _AVAIL = _families()
    return _AVAIL


def pick_font():
    """挑一个本机可用的无衬线字体"""
    a = avail()
    if app_theme == "anna":
        load_anna_font()
        a = avail()
        if "Noto Sans SC" in a:
            return "Noto Sans SC"
    for cand in ("HarmonyOS Sans SC", "PingFang SC", "Microsoft YaHei UI",
                 "Microsoft YaHei", "Source Han Sans SC", "思源黑体",
                 "Noto Sans SC", "Segoe UI"):
        if cand in a:
            return cand
    return "Microsoft YaHei UI"


def pick_hand_font():
    """桌面时钟字体：内置得意黑优先，拿不到再退回系统手写体"""
    load_app_fonts()
    a = avail()
    for cand in ("Smiley Sans", "得意黑", "STXingkai", "华文行楷",
                 "Segoe Script", "Ink Free", "Dancing Script",
                 "KaiTi", "楷体", "Comic Sans MS"):
        if cand in a:
            return cand
    return pick_font()


# ---------------------------------------------------------------- 调色板
# 恒定的品牌色（不随主题变）
C_BLUE      = "#1A56C4"   # 主色（CAAC 蓝）
C_BLUE_HI   = "#3B7DE8"   # 亮蓝
C_BLUE_DEEP = "#0E3A8C"   # 深蓝
C_SKY       = "#DCE8FA"   # 天蓝浅底
C_RED       = "#E5484D"   # 警示
C_GREEN     = "#2FA36B"   # 通过
C_AMBER     = "#E8A33D"   # 提醒

# 兼容旧引用的浅色默认值（模块级常量，运行时被 apply() 覆盖）
C_INK       = "#0F1620"
C_INK_2     = "#3A4757"
C_INK_3     = "#7A8798"
C_LINE      = "rgba(255,255,255,0.55)"
G_FILL      = "rgba(255,255,255,0.86)"
G_FILL_HI   = "rgba(255,255,255,0.94)"
G_FILL_LO   = "rgba(255,255,255,0.72)"
G_EDGE      = "rgba(255,255,255,0.95)"
G_SHADOW    = "rgba(23,43,77,0.13)"


# 两套调色板。键名在各处统一引用，切主题时整体替换。
PALETTES = {
    "light": dict(
        name="浅色",
        # 文字
        ink="#0F1620", ink2="#3A4757", ink3="#596B80",
        # 玻璃
        glass="#FFFFFF",
        glass_hi="#FFFFFF",
        glass_lo="#F5F8FC",
        glass_edge="#DBE3EE",
        glass_soft_edge="#E2E8F1",
        # 面板 / 输入
        input_bg="#FFFFFF",
        input_bg_focus="rgba(255,255,255,0.92)",
        input_border="#CBD6E4",
        panel_bg="#FFFFFF",
        panel_soft="#EBF1F9",
        panel_hover="#E6EFFB",
        list_bg="#F8FAFD",
        list_border="#DDE5EF",
        # 窗口外壳 / 标题栏
        shell="#F2F7FE",
        titlebar="rgba(255,255,255,0.55)",
        titlebar_line="rgba(255,255,255,0.75)",
        # 背景层
        bg_grad=("#F0F6FE", "#F9FCFF", "#E9F2FD"),
        bg_opacity=0.16,
        glow=(255, 255, 255, 215),
        glow_mid=(255, 255, 255, 130),
        tint=(26, 86, 196, 26),
        # 阴影
        shadow=(23, 43, 77, 48),
        shadow_hi=(23, 43, 77, 90),
        # 杂项
        scroll="rgba(26,86,196,0.30)",
        scroll_hover="rgba(26,86,196,0.52)",
        combo_bg="rgba(255,255,255,0.97)",
        combo_border="rgba(200,215,240,0.90)",
        check_border="rgba(120,138,165,0.65)",
        check_bg="rgba(255,255,255,0.72)",
        dialog_bg="#F4F8FE",
        tip_bg="rgba(15,22,32,0.94)",
        tip_fg="#ffffff",
        # 状态色（浅色下用中等明度）
        ok_fg="#187344", warn_fg="#956018", err_fg="#BF323B",
        accent_fg=C_BLUE_DEEP,
        accent_soft="rgba(105,151,216,0.08)",
        accent_edge="rgba(44,124,246,0.16)",
    ),
    "dark": dict(
        name="深色",
        ink="#EAF1FA", ink2="#A9B9CE", ink3="#A0B1C5",
        glass="#1B293B",
        glass_hi="#223247",
        glass_lo="#182536",
        glass_edge="rgba(255,255,255,0.16)",
        glass_soft_edge="rgba(255,255,255,0.12)",
        input_bg="rgba(46,62,84,0.72)",
        input_bg_focus="rgba(56,74,99,0.92)",
        input_border="rgba(255,255,255,0.14)",
        panel_bg="rgba(44,60,82,0.52)",
        panel_soft="rgba(40,55,76,0.46)",
        panel_hover="rgba(58,78,105,0.72)",
        list_bg="rgba(36,50,70,0.50)",
        list_border="rgba(255,255,255,0.10)",
        shell="#0E1622",
        titlebar="rgba(38,52,72,0.62)",
        titlebar_line="rgba(255,255,255,0.10)",
        bg_grad=("#0C1420", "#101A28", "#0A1119"),
        bg_opacity=0.30,
        glow=(38, 54, 78, 180),
        glow_mid=(30, 44, 66, 110),
        tint=(59, 125, 232, 42),
        shadow=(0, 0, 0, 130),
        shadow_hi=(0, 0, 0, 175),
        scroll="rgba(120,170,240,0.32)",
        scroll_hover="rgba(120,170,240,0.55)",
        combo_bg="rgba(38,52,72,0.99)",
        combo_border="rgba(255,255,255,0.16)",
        check_border="rgba(255,255,255,0.30)",
        check_bg="rgba(255,255,255,0.10)",
        dialog_bg="#131C2A",
        tip_bg="rgba(20,28,40,0.97)",
        tip_fg="#EAF1FA",
        ok_fg="#4CC98A", warn_fg="#F0B457", err_fg="#FF7A7E",
        accent_fg="#8FB8F5",
        accent_soft="rgba(105,151,216,0.08)",
        accent_edge="rgba(44,124,246,0.16)",
    ),
}

mode = "light"          # 当前生效主题：light / dark
pref = "system"         # 用户偏好：light / dark / system
app_theme = "default"   # 品牌主题包，与浅色/深色外观独立
P = PALETTES["light"]   # 当前调色板


def _color_with_alpha(value, alpha):
    try:
        red, green, blue = (int(value[index:index + 2], 16)
                            for index in (1, 3, 5))
        return f"rgba({red},{green},{blue},{alpha})"
    except (TypeError, ValueError, IndexError):
        return f"rgba(26,86,196,{alpha})"


def _palette_for(theme):
    """按外观模式和独立主题包组装当前颜色变量。"""
    palette = dict(PALETTES[theme])
    if app_theme == "industrial":
        if theme == "light":
            palette.update(
                name="浅色", ink="#15191C", ink2="#50595E", ink3="#778187",
                glass="#FFFFFF", glass_hi="#FFFFFF", glass_lo="#F6F7F7",
                glass_edge="#CFD4D7", glass_soft_edge="#DFE3E5",
                input_bg="#FFFFFF", input_bg_focus="#FFFFFF",
                input_border="#CFD4D7", panel_bg="#FFFFFF",
                panel_soft="#F6F7F7", panel_hover="#ECEFF0",
                list_bg="#FFFFFF", list_border="#CFD4D7",
                shell="#EEF0F1", titlebar="#FFFFFF",
                titlebar_line="#CFD4D7",
                bg_grad=("#EEF0F1", "#F5F6F6", "#EEF0F1"),
                bg_opacity=0.0, glow=(255, 255, 255, 0),
                glow_mid=(255, 255, 255, 0), tint=(241, 52, 45, 0),
                shadow=(21, 25, 28, 20), shadow_hi=(21, 25, 28, 35),
                scroll="rgba(112,120,125,0.40)",
                scroll_hover="rgba(80,89,94,0.62)",
                combo_bg="#FFFFFF", combo_border="#CFD4D7",
                check_border="#92999D", check_bg="#FFFFFF",
                dialog_bg="#EEF0F1", tip_bg="#15191C", tip_fg="#FFFFFF",
                ok_fg="#2D7651", warn_fg="#9A671F", err_fg="#D92722",
                accent_fg="#F1342D", accent_soft="rgba(241,52,45,0.08)",
                accent_edge="rgba(241,52,45,0.34)")
        else:
            palette.update(
                name="深色", ink="#F3F5F5", ink2="#B0B8BB", ink3="#A0AAAE",
                glass="#1B2023", glass_hi="#202629", glass_lo="#171B1E",
                glass_edge="#394145", glass_soft_edge="#30383B",
                input_bg="#171B1E", input_bg_focus="#202629",
                input_border="#394145", panel_bg="#1B2023",
                panel_soft="#202629", panel_hover="#252B2E",
                list_bg="#1B2023", list_border="#394145",
                shell="#111416", titlebar="#1B2023",
                titlebar_line="#394145",
                bg_grad=("#111416", "#111416", "#111416"),
                bg_opacity=0.0, glow=(17, 20, 22, 0),
                glow_mid=(17, 20, 22, 0), tint=(255, 69, 62, 0),
                shadow=(0, 0, 0, 60), shadow_hi=(0, 0, 0, 90),
                scroll="rgba(86,97,102,0.60)",
                scroll_hover="rgba(176,184,187,0.62)",
                combo_bg="#1B2023", combo_border="#394145",
                check_border="#738085", check_bg="#1B2023",
                dialog_bg="#111416", tip_bg="#F3F5F5", tip_fg="#111416",
                ok_fg="#70C99A", warn_fg="#F0B457", err_fg="#FF7771",
                accent_fg="#FF625C", accent_soft="rgba(255,69,62,0.12)",
                accent_edge="rgba(255,69,62,0.40)")
        return palette
    if app_theme == "anna":
        try:
            with open(asset_path("anna_theme", "tokens", "theme.json"),
                      "r", encoding="utf-8") as stream:
                colors = json.load(stream)["modes"][theme]
            palette.update(
                name="杏菜浅色" if theme == "light" else "杏菜深色",
                ink=colors["text"], ink2=colors["muted"], ink3=colors["muted"],
                glass=colors["surface"], glass_hi=colors["surfaceAlt"],
                glass_lo=colors["surfaceAlt"], glass_edge=colors["border"],
                glass_soft_edge=colors["border"], input_bg=colors["surface"],
                input_bg_focus=colors["surfaceAlt"],
                input_border=colors["controlBorder"], panel_bg=colors["surface"],
                panel_soft=colors["surfaceAlt"], panel_hover=colors["surfaceAlt"],
                list_bg=colors["surface"], list_border=colors["border"],
                shell=colors["bg"], titlebar=colors["sidebar"],
                titlebar_line=colors["border"],
                bg_grad=(colors["bg"], colors["bg"], colors["bg"]),
                bg_opacity=0.0, glow=(255, 255, 255, 0),
                glow_mid=(255, 255, 255, 0), tint=(49, 91, 196, 18),
                shadow=(16, 35, 67, 30), shadow_hi=(16, 35, 67, 55),
                scroll="rgba(49,91,196,0.34)",
                scroll_hover="rgba(49,91,196,0.56)",
                combo_bg=colors["surface"], combo_border=colors["border"],
                check_border=colors["controlBorder"], check_bg=colors["surface"],
                dialog_bg=colors["bg"], tip_bg=colors["text"],
                tip_fg=colors["surface"], ok_fg=colors["success"],
                warn_fg=colors["yellow"], err_fg=colors["danger"],
                accent_fg=colors["coral"], accent_soft=colors["coralSoft"],
                accent_edge=colors["border"])
        except (OSError, KeyError, ValueError, TypeError):
            pass
        return palette
    if app_theme.startswith("skin:"):
        try:
            from skin_manager import palette as skin_palette
            colors = skin_palette(app_theme.split(":", 1)[1], theme)
            canvas = colors.get("canvas")
            surface = colors.get("surface")
            ink = colors.get("ink")
            muted = colors.get("muted")
            accent = colors.get("accent")
            border = colors.get("border")
            if canvas:
                palette.update(shell=canvas, panel_soft=canvas,
                               dialog_bg=canvas, titlebar=canvas)
            if surface:
                palette.update(glass=surface, glass_hi=surface, glass_lo=surface,
                               panel_bg=surface, input_bg=surface,
                               input_bg_focus=surface, list_bg=surface,
                               combo_bg=surface)
            if ink:
                palette["ink"] = ink
            if muted:
                palette.update(ink2=muted, ink3=muted)
            if accent:
                palette.update(accent_fg=accent,
                               accent_soft=_color_with_alpha(accent, 0.10),
                               accent_edge=_color_with_alpha(accent, 0.34))
            if border:
                palette.update(glass_edge=border, glass_soft_edge=border,
                               input_border=border, list_border=border,
                               combo_border=border)
        except (ImportError, OSError, ValueError, TypeError):
            pass
        return palette
    if app_theme != "chiyo":
        palette.update(accent_soft="rgba(105,151,216,0.08)",
                       accent_edge="rgba(44,124,246,0.16)")
        return palette
    if theme == "light":
        palette.update(
            ink="#20232A", ink2="#3C3D42", ink3="#62656B",
            glass="#FFFFFF", glass_hi="#FFFFFF", glass_lo="#FFF9F8",
            glass_edge="#20232A", glass_soft_edge="#D7D4D0",
            input_bg="#FFFFFF", input_bg_focus="#FFFDFC",
            input_border="#C8C4C0", panel_bg="#FFFFFF",
            panel_soft="#F7F5F1", panel_hover="#FFF0F1",
            list_bg="#FFFFFF", list_border="#D7D4D0",
            shell="#F7F5F1", titlebar="#FFFEFA",
            titlebar_line="#D7D4D0", bg_grad=("#F7F5F1", "#FFFEFA", "#F7F5F1"),
            bg_opacity=0.07, glow=(255, 255, 255, 220),
            glow_mid=(255, 255, 255, 160), tint=(239, 65, 84, 12),
            shadow=(27, 34, 45, 42), shadow_hi=(27, 34, 45, 76),
            scroll="rgba(239,65,84,0.28)", scroll_hover="rgba(239,65,84,0.48)",
            combo_bg="#FFFFFF", combo_border="#D7D4D0",
            check_border="#9099A7", check_bg="#FFFFFF",
            dialog_bg="#FFFEFA", accent_fg="#D72F46",
            accent_soft="rgba(239,65,84,0.08)",
            accent_edge="rgba(239,65,84,0.20)")
    else:
        palette.update(
            ink="#EAE6DF", ink2="#B8B4B0", ink3="#8F8B87",
            glass="#242731", glass_hi="#2D303C", glass_lo="#191B22",
            glass_edge="#55545E", glass_soft_edge="#41414A",
            input_bg="#2D303C", input_bg_focus="#353844",
            input_border="#55545E", panel_bg="#242731",
            panel_soft="#191B22", panel_hover="#343743",
            list_bg="#242731", list_border="#41414A",
            shell="#191B22", titlebar="#15171D",
            titlebar_line="#363840", bg_grad=("#191B22", "#1D1F27", "#191B22"),
            bg_opacity=0.17, glow=(39, 46, 57, 150),
            glow_mid=(32, 39, 50, 105), tint=(255, 78, 98, 25),
            shadow=(0, 0, 0, 150), shadow_hi=(0, 0, 0, 195),
            scroll="rgba(255,105,122,0.30)",
            scroll_hover="rgba(255,105,122,0.54)",
            combo_bg="#242731", combo_border="#55545E",
            check_border="#777680", check_bg="#2D303C",
            dialog_bg="#191B22", accent_fg="#FF8494",
            accent_soft="rgba(255,82,103,0.10)",
            accent_edge="rgba(255,105,122,0.24)")
    try:
        token_file = asset_path(
            "chiyo_pack", "tokens_dark.json" if theme == "dark"
            else "tokens_light.json")
        with open(token_file, "r", encoding="utf-8") as stream:
            colors = json.load(stream)["color"]
        palette.update(
            ink=colors["text"], ink2=colors["textMuted"],
            ink3=colors["textMuted"], shell=colors["canvas"],
            titlebar=colors["sidebar"], glass=colors["surface"],
            glass_hi=colors["surface"], glass_lo=colors["canvas"],
            glass_edge=colors["border"], glass_soft_edge=colors["border"],
            input_bg=colors["surface"], input_bg_focus=colors["surfaceRaised"],
            input_border=colors["border"], panel_bg=colors["surface"],
            panel_soft=colors["surfaceRaised"], list_bg=colors["surface"],
            list_border=colors["border"], combo_bg=colors["surface"],
            combo_border=colors["border"], dialog_bg=colors["canvas"],
            accent_fg=colors["accent"],
            accent_soft=colors["accentSoft"],
            accent_edge=colors["focus"])
    except (OSError, KeyError, ValueError, TypeError):
        pass
    if app_theme == "chiyo":
        # 千代主题使用实色漫画墨线，避免资源 token 中的淡色描边显得像半透明玻璃。
        if theme == "light":
            palette.update(glass_edge="#29272C", glass_soft_edge="#716B70",
                           input_border="#766F75", list_border="#29272C")
        else:
            palette.update(glass_edge="#E6DFD6", glass_soft_edge="#8B8389",
                           input_border="#8B8389", list_border="#E6DFD6")
    return palette


def apply(theme):
    """切换生效主题，并把调色板同步到模块级常量（兼容旧引用）"""
    global mode, P
    global C_INK, C_INK_2, C_INK_3, C_LINE
    global G_FILL, G_FILL_HI, G_FILL_LO, G_EDGE, G_SHADOW
    global C_BLUE, C_BLUE_HI, C_BLUE_DEEP, C_SKY
    global C_RED
    mode = "dark" if theme == "dark" else "light"
    P = _palette_for(mode)
    if app_theme == "chiyo":
        C_BLUE, C_BLUE_HI, C_BLUE_DEEP, C_SKY = (
            ("#F04459", "#FF687A", "#CC2C42", "#FFABB5")
            if mode == "light" else
            ("#F34D63", "#FF7789", "#D93249", "#FFADB8"))
    elif app_theme.startswith("skin:"):
        C_BLUE = P.get("accent_fg", "#1A56C4")
        C_BLUE_HI = C_BLUE
        C_BLUE_DEEP = C_BLUE
        C_SKY = C_BLUE
    elif app_theme == "industrial":
        C_BLUE = P["accent_fg"]
        C_BLUE_HI = C_BLUE
        C_BLUE_DEEP = "#D92722" if mode == "light" else "#FF453E"
        C_SKY = C_BLUE
    elif app_theme == "anna":
        try:
            with open(asset_path("anna_theme", "tokens", "theme.json"),
                      "r", encoding="utf-8") as stream:
                colors = json.load(stream)["modes"][mode]
            C_BLUE = colors["primary"]
            C_BLUE_HI = colors["primary"]
            C_BLUE_DEEP = colors["primaryHover"]
            C_SKY = colors["link"]
        except (OSError, KeyError, ValueError, TypeError):
            C_BLUE, C_BLUE_HI, C_BLUE_DEEP, C_SKY = (
                "#315BC4", "#315BC4", "#264BA7", "#315BC4")
    else:
        C_BLUE, C_BLUE_HI, C_BLUE_DEEP, C_SKY = (
            "#1A56C4", "#3B7DE8", "#0E3A8C", "#5AA8F5")
    C_RED = (P["err_fg"] if app_theme == "anna" else
             P["accent_fg"] if app_theme == "industrial" else "#E5484D")
    C_INK, C_INK_2, C_INK_3 = P["ink"], P["ink2"], P["ink3"]
    C_LINE = P["glass_edge"]
    G_FILL, G_FILL_HI = P["glass"], P["glass_hi"]
    G_FILL_LO, G_EDGE, G_SHADOW = P["glass_lo"], P["glass_edge"], P["shadow"]
    return P


def set_app_theme(theme):
    """只切换品牌主题包，不改变当前的浅色/深色外观。"""
    global app_theme
    selected = "default"
    if theme == "anna":
        selected = "anna"
    elif theme == "chiyo":
        selected = "chiyo"
    elif theme == "industrial":
        selected = "industrial"
    elif isinstance(theme, str) and theme.startswith("skin:"):
        try:
            from skin_manager import get_theme
            if get_theme(theme.split(":", 1)[1]):
                selected = theme
        except (ImportError, OSError, ValueError):
            pass
    app_theme = selected
    apply(mode)
    return app_theme


def system_theme():
    """读取 Windows 系统深浅色设置；失败则回退浅色"""
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        winreg.CloseKey(k)
        return "light" if int(v) == 1 else "dark"
    except Exception:
        return "light"


def resolve_theme(preference):
    """把用户偏好解析成实际主题"""
    if preference == "system":
        return system_theme()
    return preference if preference in ("light", "dark") else "light"


# ---------------------------------------------------------------- 几何
def radius(pct):
    """把设计稿的圆角比例换算成像素（以 22 为基准）"""
    return pct


# ---------------------------------------------------------------- 全局样式
def stylesheet() -> str:
    if app_theme == "industrial":
        skin_path = asset_path(
            "azimuth_frequency", "qt",
            f"azimuth-frequency-{mode}.qss")
        try:
            with open(skin_path, "r", encoding="utf-8") as skin_file:
                base = skin_file.read()
        except (OSError, UnicodeError):
            base = ""
        return base + f"""
QFrame#Glass {{ background:{P['glass']}; border:1px solid {P['glass_edge']}; border-radius:8px; }}
QFrame#GlassStrong {{ background:{P['glass_hi']}; border:1px solid {P['glass_edge']}; border-radius:8px; }}
QFrame#GlassSoft {{ background:{P['glass_lo']}; border:1px solid {P['glass_soft_edge']}; border-radius:6px; }}
QLabel#H1 {{ color:{P['ink']}; font-size:30px; font-weight:750; letter-spacing:0px; }}
QPushButton#Danger {{ color:#ffffff; background:{P['accent_fg']}; border:none; border-radius:5px; }}
QPushButton#Danger:hover {{ background:{C_BLUE_DEEP}; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ border-radius:3px; }}
QToolTip {{ border-radius:2px; }}
"""
    f = pick_font()
    C_INK, C_INK_2, C_INK_3 = P["ink"], P["ink2"], P["ink3"]
    frame_edge = "2px" if app_theme == "chiyo" else "1px"
    control_edge = "2px" if app_theme == "chiyo" else "1px"
    chiyo_sections = (
        f"QFrame#Glass[chiyoSection=\"true\"] {{ background:{P['glass']};"
        f"border:2px solid {P['glass_edge']};border-left:5px solid #E9435B;"
        "border-radius:3px;}"
        if app_theme == "chiyo" else "")
    return f"""
* {{
    font-family: {f};
    outline: none;
}}

QWidget#Root {{ background: transparent; }}

/* ---------------- 玻璃卡片 ---------------- */
QFrame#Glass {{
    background: {P['glass']};
    border: {frame_edge} solid {P['glass_edge']};
    border-radius: {6 if app_theme == 'chiyo' else 14}px;
}}
QFrame#GlassStrong {{
    background: {P['glass_hi']};
    border: {frame_edge} solid {P['glass_edge']};
    border-radius: {6 if app_theme == 'chiyo' else 14}px;
}}
QFrame#GlassSoft {{
    background: {P['glass_lo']};
    border: 1px solid {P['glass_soft_edge']};
    border-radius: {5 if app_theme == 'chiyo' else 12}px;
}}
{chiyo_sections}
QFrame#GlassDeep {{
    background: rgba(14,58,140,0.92);
    border: 1px solid rgba(255,255,255,0.32);
    border-radius: 14px;
}}

/* ---------------- 文字 ---------------- */
QLabel {{ background: transparent; color: {C_INK}; }}
QLabel#H1 {{ font-size: 27px; font-weight: 700; color: {C_INK}; letter-spacing: 1px; }}
QLabel#H2 {{ font-size: 20px; font-weight: 700; color: {C_INK}; }}
QLabel#H3 {{ font-size: 15px; font-weight: 700; color: {P['accent_fg']}; letter-spacing: .5px; }}
QLabel#Dim {{ font-size: 13px; color: {C_INK_2}; }}
QLabel#Dim2 {{ font-size: 12px; color: {C_INK_3}; }}
QLabel#OnBlue {{ color: #ffffff; }}
QLabel#OnBlueDim {{ color: rgba(255,255,255,0.72); font-size: 12px; }}

/* ---------------- 按钮 ---------------- */
QPushButton {{
    background: {P['panel_soft']};
    color: {C_INK};
    border: none;
    border-radius: {12 if app_theme == 'chiyo' or app_theme == 'default' else 15}px;
    padding: 10px 20px;
    font-size: 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {P['panel_hover']};
    border: none;
}}
QPushButton:focus {{ border: none; background: {P['accent_soft']}; }}
QPushButton:pressed {{ background: rgba(230,238,250,0.35); }}
QPushButton:disabled {{
    background: {P['panel_soft']};
    color: {C_INK_3};
    border: none;
}}

QPushButton#Danger {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #EE5B60, stop:1 {C_RED});
    color: #fff; border: none; font-weight: 700;
}}

/* ---------------- 输入 ---------------- */
QLineEdit, QComboBox {{
    background: {P['input_bg']};
    border: {control_edge} solid {P['input_border']};
    border-radius: {6 if app_theme == 'chiyo' else 13}px;
    padding: 9px 14px;
    font-size: 13px;
    color: {C_INK};
    selection-background-color: {C_BLUE_HI};
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid rgba(59,125,232,0.85);
    background: {P['input_bg_focus']};
}}
QLineEdit::placeholder {{ color: {C_INK_3}; }}

/* 下拉框：不用原生 popup（原生弹出视图在无边框窗口下会渲染成黑底），
   改为 Qt 自绘列表 + 自管样式，见 ui_kit.Combo */
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox QAbstractItemView {{
    background: {P['combo_bg']};
    border: 1px solid {P['combo_border']};
    border-radius: 12px;
    selection-background-color: {C_BLUE_HI};
    selection-color: #ffffff;
    outline: none;
    padding: 4px;
    color: {C_INK};
}}

/* ---------------- 列表 ---------------- */
QListWidget {{
    background: {P['list_bg']};
    border: 1px solid {P['list_border']};
    border-radius: 12px;
    padding: 7px;
    outline: none;
    font-size: 13px;
    color: {C_INK};
}}
QListWidget::item {{
    padding: 9px 11px;
    border-radius: 11px;
    margin: 2px 0;
    color: {C_INK_2};
}}
QListWidget::item:hover {{ background: {P['panel_hover']}; color: {C_INK}; }}
QListWidget::item:selected {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {C_BLUE_HI}, stop:1 {C_BLUE});
    color: #ffffff;
    font-weight: 600;
}}

/* ---------------- 富文本 ---------------- */
QTextBrowser {{
    background: {P['panel_bg']};
    border: 1px solid {P['list_border']};
    border-radius: 12px;
    padding: 16px;
    font-size: 14px;
    color: {C_INK};
}}

/* ---------------- 滚动条 ---------------- */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 3px; }}
QScrollBar::handle:vertical {{
    background: {P['scroll']};
    border-radius: 5px;
    min-height: 34px;
}}
QScrollBar::handle:vertical:hover {{ background: {P['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 3px; }}
QScrollBar::handle:horizontal {{
    background: {P['scroll']};
    border-radius: 5px;
    min-width: 34px;
}}
QScrollBar::handle:horizontal:hover {{ background: {P['scroll_hover']}; }}

QSplitter::handle {{ background: transparent; width: 10px; }}

/* ---------------- 勾选框 ---------------- */
QCheckBox {{
    spacing: 9px;
    font-size: 13px;
    color: {C_INK_2};
}}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 1.5px solid {P['check_border']};
    border-radius: 6px;
    background: {P['check_bg']};
}}
QCheckBox::indicator:hover {{ border-color: {C_BLUE_HI}; }}
QCheckBox::indicator:checked {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {C_BLUE_HI}, stop:1 {C_BLUE});
    border: 1.5px solid {C_BLUE};
}}

/* ---------------- 进度条 ---------------- */
QProgressBar {{
    background: {P['panel_soft']};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    font-size: 0px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {C_BLUE_HI}, stop:1 {C_SKY});
    border-radius: 4px;
}}

/* ---------------- 对话框 ---------------- */
QMessageBox {{ background: {P['dialog_bg']}; }}
QMessageBox QLabel {{ color: {C_INK}; font-size: 13px; }}
QDialog {{ background: {P['dialog_bg']}; }}

QToolTip {{
    background: {P['tip_bg']};
    color: {P['tip_fg']};
    border: none;
    border-radius: 8px;
    padding: 7px 11px;
    font-size: 12px;
}}
"""


def asset_path(*parts):
    """资源路径（兼容 PyInstaller）"""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    direct = os.path.join(base, "assets", *parts)
    if os.path.exists(direct):
        return direct
    if parts and parts[0] in ("anna_theme", "chiyo_pack"):
        theme_id = "anna" if parts[0] == "anna_theme" else "chiyo"
        try:
            from skin_manager import theme_path
            imported = theme_path(theme_id, "assets/" + "/".join(parts))
            if imported:
                return imported
        except (ImportError, OSError, ValueError):
            pass
    return direct


def load_anna_font():
    """按需注册八奈见主题随包提供的中文字体。"""
    global _AVAIL, _anna_font_loaded
    if _anna_font_loaded:
        return True
    try:
        from PySide6.QtGui import QFontDatabase
        path = asset_path("anna_theme", "assets", "fonts",
                          "NotoSansSC-Variable.ttf")
        if not os.path.isfile(path):
            return False
        font_id = QFontDatabase.addApplicationFont(path)
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        _anna_font_loaded = bool(families)
        if _anna_font_loaded:
            _AVAIL = None
        return _anna_font_loaded
    except Exception:
        return False


def load_app_fonts():
    """把 assets 里随包分发的字体注册进 Qt，返回成功注册的家族名列表。

    为什么必须内置：得意黑（Smiley Sans）不是 Windows 自带字体，用户机器
    大概率没装；把 TTF 随包分发再 addApplicationFont，换台电脑也能保证
    桌面时钟用对字体，不依赖系统安装。
    许可：得意黑基于 SIL OFL 1.1，允许自由使用、嵌入与再分发。
    """
    global _AVAIL, _bundled_loaded
    if _bundled_loaded:
        return _bundled_loaded
    try:
        from PySide6.QtGui import QFontDatabase
    except Exception:
        return _bundled_loaded
    for want, fn in BUNDLED_FONTS.items():
        p = asset_path(fn)
        if not os.path.exists(p):
            continue
        fid = QFontDatabase.addApplicationFont(p)
        if fid < 0:
            continue
        fams = QFontDatabase.applicationFontFamilies(fid)
        if fams:
            _bundled_loaded.append(fams[0])
    # 注册完要让 avail() 重新枚举，否则缓存里没有刚加入的家族
    _AVAIL = None
    return _bundled_loaded
