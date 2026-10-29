"""检索用户核验过的题卡经验，为视觉模型 API 提供解题提示。"""

import re


def domain_rules():
    return [
        "题图的几何边、任务飞行航线和起飞/避让航段必须分别识别；航迹顺序为起飞点→避让点→1号点。",
        "优先逐字保留用户导入题卡的坐标、距离、角度、方位与单位；不确定处明确标记。",
        "双坐标题先用题面给出的两个坐标锚点确定坐标系和朝向，再推算其余点。",
        "夹角题先核对角度的顶点与两条邻边，再检查航线与图形角度是否满足条件。",
        "航向以北为 0°、顺时针增加；顺风加 180°，逆风不变，正左侧风加 90°，正右侧风减 90°。",
    ]


def _shape_from_text(text, diagram):
    value = str((diagram or {}).get("shape") or "")
    if value:
        return value
    value = str(text or "")
    for shape in ("等边三角形", "直角三角形", "三角形", "梯形", "五边形",
                  "六边形", "蝴蝶形", "扫描航线", "圓形", "圆形"):
        if shape in value:
            return "圆形" if shape == "圓形" else shape
    return ""


def _features(text):
    text = str(text or "")
    checks = {
        "coordinate": r"坐标|经纬|纬度|经度|\b[NE]\s*[:：]",
        "distance": r"距离|边长|\d+(?:\.\d+)?\s*(?:m|米|公里|km)",
        "angle": r"夹角|角度|∠|角\s*[1-9一二三四五六七八九]",
        "bearing": r"航向|方位|NW|NE|SW|SE|正东|正西|正南|正北",
        "direction_relation": r"相同|相反|同向|反向|平行|垂直|偏东|偏西|偏南|偏北",
        "wind": r"风向|顺风|逆风|侧风|左侧风|右侧风",
        "access_point": r"起飞点|避让点|过渡点|转场点|返航点",
        "midpoint": r"中点|中心点|垂直平分",
        "equality": r"等长|相等|等于|=",
    }
    return {name for name, pattern in checks.items()
            if re.search(pattern, text, re.I)}


def _overlap_score(query, item):
    score = 0.0
    shape = query["shape"]
    if shape and shape == str(item.get("shape") or ""):
        score += 8.0
    count = query["node_count"]
    if count and count == len(item.get("nodes") or []):
        score += 4.0
    item_features = set(item.get("features") or [])
    shared = query["features"] & item_features
    score += len(shared) * 2.4
    if "angle" in shared and "distance" in shared:
        score += 1.5
    # 更贴近当前图形的类型优先，但 OCR 条件本身仍是主要召回依据。
    query_chars = set(re.findall(r"[\u4e00-\u9fff]{2}", query["text"]))
    item_chars = set(re.findall(r"[\u4e00-\u9fff]{2}",
                                str(item.get("question_excerpt") or "")))
    if query_chars and item_chars:
        score += 3.0 * len(query_chars & item_chars) / max(1, len(query_chars))
    return score


def _runtime_verified_examples():
    """读取当前指纹匹配，或经当前求解器重新严格校验通过的用户答案。"""
    try:
        import bankdata as BD
        import config
        import reference_engine as RE
        records = config.get("verified_references", {})
        if not isinstance(records, dict) or not records:
            return []
        bank_questions = {
            str(question.get("id")): question for question in BD.load()
            if question.get("imported") and question.get("id") is not None
        }
        output = []
        for key, question in bank_questions.items():
            saved = records.get(key)
            if not isinstance(saved, dict):
                continue
            if saved.get("status") != "READY":
                continue
            spec = RE.extract_question_spec(question)
            same_fingerprint = (
                saved.get("fingerprint") == RE.question_fingerprint(question))
            revalidated = None
            if not same_fingerprint:
                if saved.get("shape_source") != str(
                        question.get("requirements_text") or ""):
                    continue
                revalidated = RE.solve_reference(question)
                if (not isinstance(revalidated, dict) or
                        revalidated.get("status") != "READY" or
                        not revalidated.get("verified_reference_revalidated")):
                    continue
            nodes = [str(value) for value in (spec.get("nodes") or [])]
            edges = [[str(value) for value in edge[:2]]
                     for edge in (spec.get("geometry_edges") or [])]
            if len(nodes) < 3 or len(edges) < 3:
                continue
            conditions = "\n".join(str(question.get(key) or "") for key in
                                    ("conditions_text", "requirements_text")
                                    if question.get(key))
            output.append({
                "id": key,
                "trust": "user_verified",
                "shape": str(spec.get("shape") or ""),
                "nodes": nodes,
                "features": sorted(_features(conditions)),
                "question_excerpt": conditions[:1800],
                "condition_lines": [line.strip() for line in
                                    re.split(r"[\r\n]+", conditions) if line.strip()][:18],
                "diagram": {"shape": spec.get("shape") or "", "nodes": nodes,
                            "geometry_edges": edges},
                "audit_checks": [
                    "当前题面与用户核验答案指纹一致" if same_fingerprint else
                    "旧指纹答案已按当前题面硬条件重新核验通过"],
                "audit_steps": [str(value)[:240] for value in
                                (saved.get("steps") or [])[:3]],
            })
        return output
    except Exception:
        return []


def retrieve_examples(ocr_text, diagram_model=None, limit=3):
    """按图形与条件类型检索样例；只给识别格式，不传答案坐标。"""
    text = str(ocr_text or "")[:12000]
    query = {
        "text": text,
        "shape": _shape_from_text(text, diagram_model),
        "node_count": len((diagram_model or {}).get("nodes") or []),
        "features": _features(text),
    }
    examples = _runtime_verified_examples()
    ranked = []
    for item in examples:
        if not isinstance(item, dict):
            continue
        score = _overlap_score(query, item)
        if item.get("trust") == "user_verified":
            score += 3.0
        if score > 0:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("id", ""))))

    selected = []
    seen = set()
    cap = max(0, min(4, int(limit)))
    for score, item in ranked:
        key = (item.get("trust"), str(item.get("id")))
        if key in seen:
            continue
        seen.add(key)
        selected.append({
            "case_id": str(item.get("id", "")),
            "trust": str(item.get("trust") or "solver_audited"),
            "features": list(item.get("features") or []),
            # 控制样例长度，给原图与完整题面留出上下文空间。
            "question_excerpt": str(item.get("question_excerpt") or "")[:420],
            "recognized_conditions": [str(value)[:300] for value in
                                      (item.get("condition_lines") or [])[:4]],
            "diagram": item.get("diagram") or {},
            "checks": [str(value)[:180] for value in
                       (item.get("audit_checks") or [])[:5]],
            "method_notes": [str(value)[:180] for value in
                             (item.get("audit_steps") or [])[:1]],
            "similarity": round(score, 2),
        })
        if len(selected) >= cap:
            break
    return selected
