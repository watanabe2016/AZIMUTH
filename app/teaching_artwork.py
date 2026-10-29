"""按教学卡参考图重绘的轮廓，统一使用百分比坐标。"""
from PySide6.QtCore import QRectF
from PySide6.QtSvg import QSvgRenderer

_BOOK = '<path d="M50 22 C34 9 17 8 6 11 L9  seventy"/>'
_PATHS = {
    'book': '<path d="M50 22 C35 10 18 10 7 13 L10 72 C25 70 39 76 50 87 C63 75 78 68 94 68 L97 4 C78 5 61 11 50 22 Z M50 22 L50 87 M5 20 L7 81 Q29 78 47 93 Q50 98 56 94 Q73 80 96 78"/>',
    'reading': '<path d="M50 24 Q30 10 10 18 L10 76 Q32 71 50 85 Q68 71 90 76 L90 18 Q70 10 50 24 Z M50 24 V85 M10 22 L4 25 V82 Q31 77 47 90 Q50 94 54 90 Q71 77 96 82 V25 L90 22 M20 32 Q29 32 39 34 M20 44 Q29 44 39 46 M20 56 Q29 56 39 58 M61 34 Q71 32 80 32 M61 46 Q71 44 80 44 M61 58 Q71 56 80 56"/>',
    'shapes': '<circle cx="53" cy="25" r="21"/><path d="M7 89 Q3 89 5 85 L26 49 Q28 45 31 49 L52 85 Q54 89 50 89 Z"/><rect x="65" y="51" width="32" height="38" rx="3"/>',
    'question': '<path d="M26 32 C25 1 77 1 77 32 C77 49 56 55 56 66 L56 70 L44 70 L44 63 C44 48 63 47 63 33 C63 16 39 16 39 32 Z"/><circle cx="50" cy="87" r="9"/>',
    'compass': '<circle cx="50" cy="50" r="42"/><path d="M50 5 V15 M50 85 V95 M5 50 H15 M85 50 H95 M18 18 L25 25 M75 75 L82 82 M18 82 L25 75 M75 25 L82 18 M30 70 L43 43 L70 30 L57 57 Z M43 43 L57 57"/><circle cx="50" cy="50" r="5"/>',
    'angle': '<path d="M13 86 H93 M13 86 L75 14 M36 59 Q54 66 54 86"/><circle cx="62" cy="59" r="4"/>',
    'angle-tool': '<path d="M13 86 H93 M13 86 L66 16 M31 62 Q53 72 53 86"/><circle cx="13" cy="86" r="6"/><circle cx="58" cy="57" r="5"/>',
    'monitor': '<rect x="8" y="13" width="84" height="55" rx="3"/><path d="M50 68 V84 M26 84 H74"/>',
    'tools': '<path d="M8 38 L18 38 L22 27 L16 20 L27 9 L34 15 L44 11 L46 0 L59 0 L61 11 L71 15 L78 9 L88 20 L82 27 M22 62 L16 70 L27 81 L35 75 L42 78 M8 38 V52 L18 53 L22 62"/><circle cx="52" cy="44" r="18"/><path d="M39 95 Q31 88 39 80 L75 45 C70 32 80 24 93 29 L84 38 Q82 44 88 47 L98 40 C101 55 90 63 78 58 L48 95 Q43 100 39 95 Z"/><circle cx="43" cy="87" r="2"/>',
    'wind': '<path d="M2 36 H29 C40 36 40 19 30 19 M10 47 H29 C39 47 41 54 37 60 M2 58 H21 C34 58 34 69 25 69"/><circle cx="70" cy="51" r="29"/><path d="M70 22 V28 M70 74 V80 M41 51 H47 M93 51 H99 M49 30 L54 35 M86 67 L91 72 M49 72 L54 67 M86 35 L91 30 M55 66 L65 46 L85 36 L75 56 Z M65 46 L75 56"/><circle cx="70" cy="51" r="4"/>',
    'sigma': '<path d="M25 34 H4 L18 49 L4 67 H26 M35 58 L40 56 L46 69 L55 32 H78 M81 50 H94 M81 59 H94"/><text x="56" y="67" font-family="Georgia" font-size="36" font-style="italic" fill="white" stroke="none">x</text>',
}
_RENDERERS = {}

def draw_artwork(painter, motif, rect, entry=False, angle_tool=False):
    """线宽、透明度及图形位置与参考卡片保持同一比例。"""
    key = 'reading' if motif == 'book' and not entry else 'angle-tool' if angle_tool else 'monitor' if motif == 'avoidance' else motif
    if key not in _PATHS:
        return False
    if key not in _RENDERERS:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
               '<g fill="none" stroke="white" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">'
               + _PATHS[key] + '</g></svg>')
        _RENDERERS[key] = QSvgRenderer(svg.encode())
    painter.save()
    painter.setOpacity(.24 if entry else .30)
    _RENDERERS[key].render(painter, QRectF(rect))
    painter.restore()
    return True
