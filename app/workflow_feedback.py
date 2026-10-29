"""整理导入结果和无法判分时的恢复提示。"""


def import_report(warnings):
    """重复、失败和可继续使用的识别提醒分别计数。"""
    groups = {"skipped": [], "failed": [], "notes": []}
    for warning in dict.fromkeys(str(item) for item in warnings or []):
        if "相同文件已导入" in warning:
            groups["skipped"].append(warning)
        elif any(word in warning for word in ("失败", "损坏", "不受支持", "处理异常")):
            groups["failed"].append(warning)
        else:
            groups["notes"].append(warning)
    return groups


def recovery_advice(reason, details=None):
    """按实际缺失条件给出下一步操作，不编造参考坐标。"""
    text = str(reason or "") + " ".join(str(value) for value in details or [])
    if any(word in text for word in ("基准", "经纬度", "绝对坐标", "起点坐标")):
        return ("缺少坐标基准", "核对题干中的起飞点或图形点经纬度；在作答区补充题目给定的坐标。"
                "若题目只有相对距离，先补充起点基准后再提交判题。")
    if any(word in text for word in ("航线", "任务", "Mission", "数据库", "LinkGCS")):
        return ("需要确认作答航线", "确认选择的是本题对应的地面站任务，并重新读取航点。"
                "读取到其他题的任务时，先切换任务再判题。")
    if any(word in text for word in ("参数", "高度", "速度", "停留")):
        return ("题目参数需要补充", "核对题干中的高度、水平速度、垂直速度、转弯和停留条件；"
                "缺失或识别错误的内容可在题卡工作台的参考答案校对中补充。")
    if any(word in text for word in ("未识别", "解析", "条件", "图形", "支持", "冲突")):
        return ("题目条件需要校对", "对照原题卡核对图形、点号、边长和方位；"
                "可在参考答案窗口校对条件或录入答案，再重新判题。")
    return ("需要补充判题依据", "核对题卡条件、参考答案和当前作答任务。"
            "如果题目缺少判题依据，可在参考答案窗口校对或录入答案后再试。")
