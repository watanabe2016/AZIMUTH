# -*- coding: utf-8 -*-
"""题目元数据 —— 用户自己标的「考点标签」与「参考答案」。

为什么单独一层：
  · 界面（题库页 / 题卡工作台）都要读写它，散在各处各写各的 cfg.get() 迟早写歪；
  · 用户导入的题卡都要能标，所以存的键统一用**题号字符串**；
  · 为空要能"干净地消失"——删掉最后一个标签必须把整条记录删掉，
    否则 config 里会积一堆 `{"123": []}` 这种垃圾。

存储位置见 config.py 的 manual_tags / answer_text。
"""
from logger import get_logger

log = get_logger()

K_TAGS = "manual_tags"
K_ANS = "answer_text"
K_VERIFIED = "verified_references"


def _cfg():
    import config
    return config


def retain_imported_question_ids(question_ids):
    """清理不再存在的旧题卡元数据，保留现有导入题的标注与进度。"""
    cfg = _cfg()
    valid = set()
    for value in question_ids or ():
        try:
            valid.add(str(int(value)))
        except (TypeError, ValueError, OverflowError):
            continue
    data = cfg.load()
    changed = False

    done = data.get("done_ids", [])
    if isinstance(done, list):
        kept = []
        for value in done:
            try:
                if str(int(value)) in valid:
                    kept.append(value)
            except (TypeError, ValueError, OverflowError):
                continue
        if kept != done:
            data["done_ids"] = kept
            changed = True

    for key in ("history", "wrong", "manual_tags", "manual_tags_off",
                "answer_text", "verified_references", "avoid_point_overrides"):
        value = data.get(key)
        if not isinstance(value, dict):
            continue
        kept = {}
        for qid, record in value.items():
            try:
                if str(int(qid)) in valid:
                    kept[qid] = record
            except (TypeError, ValueError, OverflowError):
                continue
        if kept != value:
            data[key] = kept
            changed = True

    trend = data.get("trend")
    if isinstance(trend, dict):
        kept_trend = {}
        for day, record in trend.items():
            if not isinstance(record, dict):
                continue
            qids = record.get("qs")
            if not isinstance(qids, list):
                continue
            kept_ids = []
            for qid in qids:
                try:
                    if str(int(qid)) in valid and int(qid) not in kept_ids:
                        kept_ids.append(int(qid))
                except (TypeError, ValueError, OverflowError):
                    continue
            if kept_ids:
                updated = dict(record)
                updated["qs"] = kept_ids
                kept_trend[day] = updated
        if kept_trend != trend:
            data["trend"] = kept_trend
            changed = True

    try:
        last = data.get("last_question_id")
        if last is not None and str(int(last)) not in valid:
            data["last_question_id"] = None
            changed = True
    except (TypeError, ValueError, OverflowError):
        data["last_question_id"] = None
        changed = True

    return cfg.save() if changed else True


def _d(key):
    """取整个字典（永远是 dict，坏的当空处理）"""
    v = _cfg().get(key, {})
    return v if isinstance(v, dict) else {}


def _key(qid):
    return str(int(qid))


# ==================================================== 手动标签

def tags_of(qid):
    """这道题用户手动打了哪些标签（code 列表）。没标过 → []"""
    v = _d(K_TAGS).get(_key(qid))
    return list(v) if isinstance(v, list) else []


def all_tags():
    """{题号str: [code, ...]} —— 界面批量筛选时一次取全，别在循环里反复读 cfg"""
    return {k: list(v) for k, v in _d(K_TAGS).items() if isinstance(v, list) and v}


def set_tags(qid, codes):
    """整体替换某题的手动标签。codes 为空 → 把这条记录删掉。"""
    codes = [c for c in dict.fromkeys(codes or []) if c]
    d = dict(_d(K_TAGS))
    k = _key(qid)
    if codes:
        d[k] = codes
    else:
        d.pop(k, None)
    cfg = _cfg()
    cfg.set_(K_TAGS, d)
    return codes


def toggle_tag(qid, code):
    """点一下切换：有就删、没有就加。返回切换后的列表。"""
    cur = tags_of(qid)
    if code in cur:
        cur.remove(code)
    else:
        cur.append(code)
    return set_tags(qid, cur)


# ==================================================== 手动排除层
# v1.9.11：右键菜单取消勾选一个**自动**认出的标签时，光从 manual_tags
# 里删是没用的 —— 那个标签本来就是自动引擎认出来的，下次 tags() 照样给。
# 必须有一层显式的「排除」记录把它压住。见 qtag.tags() 的 off 参数。

K_OFF = "manual_tags_off"


def offs_of(qid):
    """这道题被用户**手动排除**的标签（code 列表）。没排除过 → []"""
    v = _d(K_OFF).get(_key(qid))
    return list(v) if isinstance(v, list) else []


def all_offs():
    """{题号str: [code, ...]} —— 排除表，批量筛选时一次取全"""
    return {k: list(v) for k, v in _d(K_OFF).items() if isinstance(v, list) and v}


def set_offs(qid, codes):
    """整体替换某题的排除表。空 → 删记录（不留空串垃圾）。"""
    codes = [c for c in dict.fromkeys(codes or []) if c]
    d = dict(_d(K_OFF))
    k = _key(qid)
    if codes:
        d[k] = codes
    else:
        d.pop(k, None)
    cfg = _cfg()
    cfg.set_(K_OFF, d)
    return codes


# ==================================================== 参考答案

def answer_of(qid):
    """这道题用户手动标注的参考答案（原始文本）。没标过 → "" """
    v = _d(K_ANS).get(_key(qid))
    return str(v) if v else ""


def all_answers():
    return {k: str(v) for k, v in _d(K_ANS).items() if v}


def set_answer(qid, text):
    """整体替换。空白 → 删掉这条记录（别留空串）。"""
    text = (text or "").strip()
    _record_question_version(qid, "手动答案修改前")
    d = dict(_d(K_ANS))
    k = _key(qid)
    if text:
        d[k] = text
    else:
        d.pop(k, None)
    cfg = _cfg()
    cfg.set_(K_ANS, d)
    _record_question_version(qid, "手动参考答案更新")
    return text


def has_answer(qid):
    return bool(answer_of(qid))


def verified_reference_of(qid):
    """读取与题号关联的完整航线答案；是否过期由求解器核对指纹。"""
    if qid is None:
        return None
    value = _d(K_VERIFIED).get(_key(qid))
    return dict(value) if isinstance(value, dict) else None


def set_verified_reference(qid, result):
    """原子保存独立核验通过的航线；不会覆盖用户手写标注。"""
    if answer_of(qid):
        return False
    _record_question_version(qid, "人工核验修改前")
    data = dict(_d(K_VERIFIED))
    data[_key(qid)] = dict(result)
    cfg = _cfg()
    previous = dict(_d(K_VERIFIED))
    cfg.set_(K_VERIFIED, data, save_now=False)
    if cfg.save():
        _record_question_version(qid, "人工核验状态更新")
        return True
    cfg.set_(K_VERIFIED, previous, save_now=False)
    return False


def _record_question_version(qid, reason):
    """在独立元数据更新前后同步留档，不影响主保存流程。"""
    try:
        import question_versions as QV
        QV.record_current(qid, reason)
    except Exception:
        log.exception("参考答案版本记录失败 #%s", qid)


def restore_versioned_state(qid, manual_answer="", verified_reference=None):
    """原子替换版本中的手动答案与核验答案状态。"""
    try:
        key = _key(qid)
    except (TypeError, ValueError, OverflowError):
        return False
    cfg = _cfg()
    previous_answers = dict(_d(K_ANS))
    previous_verified = dict(_d(K_VERIFIED))
    answers = dict(previous_answers)
    verified = dict(previous_verified)
    manual_answer = str(manual_answer or "").strip()
    if manual_answer:
        answers[key] = manual_answer
    else:
        answers.pop(key, None)
    if isinstance(verified_reference, dict) and verified_reference:
        verified[key] = dict(verified_reference)
    else:
        verified.pop(key, None)
    cfg.set_(K_ANS, answers, save_now=False)
    cfg.set_(K_VERIFIED, verified, save_now=False)
    if cfg.save():
        return True
    cfg.set_(K_ANS, previous_answers, save_now=False)
    cfg.set_(K_VERIFIED, previous_verified, save_now=False)
    return False
