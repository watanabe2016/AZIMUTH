# -*- coding: utf-8 -*-
"""按题卡内容把附加条件、航线要求和建议参数分开。"""

import re


def paper_sections(question):
    """把未能由导入器分段的题干按内容归入对应版面区域。"""
    conditions = []
    requirements = [str(question.get("requirements_text") or "").strip()]
    suggestions = [str(question.get("suggested_text") or "").strip()]
    condition_text = str(question.get("conditions_text") or "")
    condition_markers = re.compile(
        r"起飞点|起点|经纬度|坐标|避让点|避让|风向|逆风|顺风|风速|夹角|∠|航向")
    suggestion_markers = re.compile(r"建议为|值建议|建议值|推荐参数")
    requirement_markers = re.compile(
        r"规划|图形|三角形|六边形|五边形|梯形|蝴蝶形|扫描线|闭合|边长|航点|地面高度|航线高度|水平速度|垂直速度|"
        r"转弯方式|停留时间|循环执行|图形高度")

    for raw_line in re.split(r"[\r\n]+", condition_text):
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^[（(]二[）)]", line):
            suggestions.append(line)
        elif re.match(r"^[（(]一[）)]", line):
            requirements.append(line)
        elif condition_markers.search(line):
            conditions.append(line)
        elif suggestion_markers.search(line):
            suggestions.append(line)
        elif requirement_markers.search(line):
            requirements.append(line)
        else:
            conditions.append(line)

    return {
        "conditions_text": "\n".join(part for part in conditions if part),
        "requirements_text": "\n".join(part for part in requirements if part),
        "suggested_text": "\n".join(part for part in suggestions if part),
    }
