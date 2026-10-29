"""更多菜单的点击命中与单次选择状态。"""


def should_dismiss_menu(point, menu_bounds, anchor_bounds, event_inside_menu=False):
    """判断点击是否落在菜单或锚点之外。边界坐标采用 Qt QRect 的闭区间语义。"""
    x, y = point

    def contains(bounds):
        left, top, right, bottom = bounds
        return left <= x <= right and top <= y <= bottom

    return not (event_inside_menu or contains(menu_bounds) or contains(anchor_bounds))


class MenuSelectionGate:
    """每次菜单展开只允许一个选择，收起动画不影响第一次有效点击。"""

    def __init__(self):
        self._claimed = False

    def reset(self):
        self._claimed = False

    def claim(self):
        if self._claimed:
            return False
        self._claimed = True
        return True
