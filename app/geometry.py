# -*- coding: utf-8 -*-
"""从题干识别参考模型的图形类型；不从模型臆造标准答案。"""

SHAPES = (
    ("等边三角形", "等边三角形"),
    ("等腰直角三角形", "直角三角形"),
    ("直角三角形", "直角三角形"),
    ("梯形", "梯形"),
    ("蝴蝶形", "蝴蝶形"),
    ("蝴蝶航线", "蝴蝶形"),
    ("五边形", "五边形"),
    ("六边形", "六边形"),
    ("扫描航线", "扫描航线"),
    ("扫描线", "扫描航线"),
    ("蛇形航线", "扫描航线"),
    ("圆形", "圆形"),
    ("圆周航线", "圆形"),
)


def recognize(question):
    text = " ".join(str(question.get(key) or "") for key in
                    ("conditions_text", "requirements_text", "suggested_text"))
    for keyword, name in SHAPES:
        if keyword in text:
            return {"name": name, "source": keyword}
    # 对题面只写“四边形”但明确给出一对相邻直角的题型，标准参考归入
    # 梯形；它表达的是平行边结构，不会推导坐标或答案。
    if ("四边形" in text and
            ("∠2=∠1=90" in text or "∠1=∠2=90" in text or
             (text.count("90°") >= 2 and "角" in text))):
        return {"name": "梯形", "source": "四边形的相邻直角结构"}
    return None
