# -*- coding: utf-8 -*-
"""從題卡掃描圖中尋找完整的原始航線圖形區域。"""


def diagram_bounds(gray):
    """合併彼此相鄰的斷裂線段，回傳圖形外框 (x, y, w, h)。"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    if gray is None or len(gray.shape) != 2:
        return None
    height, width = gray.shape
    if width < 80 or height < 100:
        return None

    mask = cv2.threshold(gray, 145, 255, cv2.THRESH_BINARY_INV)[1]
    # 只补细线的抗锯齿断口；大核会把图形、编号和下方正文粘成一块。
    bridge = 3
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        np.ones((bridge, bridge), dtype=np.uint8))
    contours = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]

    candidates = []
    min_w = max(24, int(width * 0.07))
    min_h = max(28, int(height * 0.055))
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        ratio = w / float(max(1, h))
        vertical_edge = h > height * .12 and w < width * .03
        if ((not vertical_edge and (w < min_w or h < min_h or
                not 0.18 <= ratio <= 5.6)) or
                x < width * 0.025 or x + w > width * 0.975 or
                y < height * 0.07 or y + h > height * 0.80):
            continue
        candidates.append((x, y, w, h))
    if not candidates:
        return None

    seed = max(candidates, key=lambda box: box[2] * box[3])
    cluster = [seed]
    bounds = list(seed)
    gap_limit = max(10, min(28, int(max(width, height) * 0.012)))

    def axis_gap(a0, a1, b0, b1):
        if a1 < b0:
            return b0 - a1
        if b1 < a0:
            return a0 - b1
        return 0

    remaining = [box for box in candidates if box != seed]
    changed = True
    while changed:
        changed = False
        bx, by, bw, bh = bounds
        for box in tuple(remaining):
            x, y, w, h = box
            dx = axis_gap(bx, bx + bw, x, x + w)
            dy = axis_gap(by, by + bh, y, y + h)
            if dx <= gap_limit and dy <= gap_limit:
                cluster.append(box)
                remaining.remove(box)
                left = min(bx, x)
                top = min(by, y)
                right = max(bx + bw, x + w)
                bottom = max(by + bh, y + h)
                bounds = [left, top, right - left, bottom - top]
                changed = True
                bx, by, bw, bh = bounds

    x, y, w, h = bounds
    # 编号、角度和方向箭头通常独立于航线连通域，留出完整标注边距。
    pad_x = max(12, int(width * 0.13))
    pad_y = max(12, int(height * 0.045))
    left = max(0, x - pad_x)
    top = max(0, y - pad_y)
    right = min(width, x + w + pad_x)
    bottom = min(height, y + h + pad_y)
    # 边距遇到整行正文或横幅时收住，避免把正文半行带进图形。
    dark = gray < 145
    ink_rows = dark.sum(axis=1)
    solid_rows = (gray < 90).sum(axis=1)
    for row in range(y - 1, top - 1, -1):
        if ink_rows[row] > width * .28:
            top = row + 3
            break
    # 还要查看边距外的后半行；否则裁切线落在文字上沿时，尚未达到
    # 密集行阈值，仍会留下半行字。
    scan_end = min(height, bottom + max(12, int(height * .035)))
    for row in range(y + h, scan_end):
        xs = np.flatnonzero(dark[row])
        text_row = (len(xs) > width * .01 and xs[-1] - xs[0] > width * .70)
        runs = 1 + np.count_nonzero(np.diff(xs) > 3) if len(xs) else 0
        short_text_row = (len(xs) > width * .06 and
                          xs[-1] - xs[0] > width * .35 and runs >= 12)
        if ink_rows[row] > width * .28 or text_row or short_text_row:
            # 从正文密集行回溯到该行的第一个笔画，不能截留半行字。
            start = row
            while start > y + h and solid_rows[start - 1] > width * .003:
                start -= 1
            # 少数原图把正文和末端编号排在同一高度；此时保留原图
            # 边距，不能为了去掉正文而一起截断编号。
            if start - 2 < y + h + 10:
                return None
            bottom = max(y + h, min(bottom, start - 2))
            break
    return left, top, right - left, bottom - top
