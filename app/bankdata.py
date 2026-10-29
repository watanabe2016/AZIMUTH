# -*- coding: utf-8 -*-
"""
用户题库数据层 —— 存放本地导入的题目与题卡图。

为什么必须跟内置的 assets/ 分开：
  · assets/ 是打包时就定死的，往里写等于改程序本体，写坏了没法回滚；
  · 导入的题属于用户数据，要能删、能整块拷走；
  · 万一程序被解压到 C:\\Program Files 这类只读位置，直接写会炸，
    必须能悄悄回落到 %LOCALAPPDATA%，而不是弹一句"权限不足"把人劝退。

目录优先级（挑第一个真能写的）：
  1. <程序目录>/data/bank/                     绿色版优先，整个目录拷走数据跟着走
  2. %LOCALAPPDATA%/CAAC Ground Station/bank/   只读环境时兜底

文件布局：
  questions.json    用户导入的题目
  figures/qNNN.jpg  用户导入的原始题卡图片
"""
import os
import json
from datetime import datetime
from storage import atomic_json

from logger import get_logger, app_root, APP_NAME

log = get_logger()

_root = None
_portable = True


def _candidates():
    """按优先级列出候选目录：(是否属于绿色版随身目录, 路径)"""
    out = [(True, os.path.join(app_root(), "data", "bank"))]
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    out.append((False, os.path.join(base, APP_NAME, "bank")))
    return out


def _probe(d):
    """试探能不能写：建目录 + 落一个探针文件再删掉。

    只 mkdir 不算数 —— 目录可能建得出来却不让写文件（权限只给了读），
    所以必须真写一个字节再删，才算这块地皮能用。
    """
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def root():
    """返回可写的用户题库根目录。缓存之前会复核目录还在，防止被人手删掉。"""
    global _root, _portable
    if _root is not None and os.path.isdir(_root):
        return _root
    for portable, d in _candidates():
        if _probe(d):
            _root, _portable = d, portable
            log.info("用户题库目录：%s（绿色版随身=%s）", d, portable)
            return d
    # 全都写不了也不能崩：退最后一个，加载时自然读不到任何东西
    _root = _candidates()[-1][1]
    _portable = False
    log.error("无可用写入目录，按只读处理：%s", _root)
    return _root


def is_portable():
    """当前用的是不是程序目录旁边那个（关系到用户能不能直接拷走）"""
    root()
    return _portable


def writable():
    """有没有真的拿到可写目录 —— 导入前先挡一道，别让用户白忙活"""
    global _portable
    for portable, d in _candidates():
        if _probe(d):
            return True
    return False


def qjson_path():
    return os.path.join(root(), "questions.json")


def import_manifest_path():
    return os.path.join(root(), "imports.json")


def fig_dir():
    d = os.path.join(root(), "figures")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def fig_path(qid):
    """用户目录里的题卡图路径（不一定存在，调用方自己判）"""
    return os.path.join(fig_dir(), "q%03d.jpg" % int(qid))


def load():
    """读出用户导入的题目列表；没有 / 损坏都返回空列表，不抛。"""
    p = qjson_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.error("用户题库读取失败 %r", e)
        return []
    if not isinstance(data, list):
        return []

    def migrate_visual_source(value):
        """兼容已有题卡里的旧视觉结果来源标签。"""
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if key in ("source", "graph_source") and item == "local_vlm":
                    value[key] = "vision_model"
                else:
                    migrate_visual_source(item)
        elif isinstance(value, list):
            for item in value:
                migrate_visual_source(item)

    out = []
    for q in data:
        if isinstance(q, dict) and str(q.get("id", "")).strip() != "":
            migrate_visual_source(q)
            q["imported"] = True
            out.append(q)
    return out


def save(items, version_reason="题卡内容更新", record_version=True):
    """整体写回。先落 .tmp 再替换 —— 写到一半断掉不会把旧文件糟蹋掉。"""
    items = list(items)
    previous = load() if record_version else []
    p = qjson_path()
    tmp = p + ".tmp"
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        atomic_json(p, items)
        if record_version:
            try:
                import question_versions as QV
                QV.record_changes(previous, items, reason=version_reason)
            except Exception:
                log.exception("题卡版本记录失败")
        return True
    except Exception as e:
        log.error("用户题库写入失败 %r", e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def next_id(existing_max=0):
    """下一个可用题号：只依据当前用户导入的数据分配。"""
    mine = [int(str(q["id"]).strip()) for q in load()
            if str(q.get("id", "")).strip().isdigit()]
    return max([int(existing_max)] + mine + [0]) + 1


def remove(qid):
    """删掉一条用户导入的题（含它的图）。只能删用户自己的，内置的删不动。"""
    qid = int(qid)
    items = [q for q in load()
             if str(q.get("id", "")).strip() != str(qid)]
    ok = save(items)
    fp = fig_path(qid)
    try:
        if os.path.exists(fp):
            os.remove(fp)
    except Exception as e:
        log.warning("删除题卡图失败 %s: %r", fp, e)
    if ok:
        try:
            import question_versions as QV
            QV.remove_versions(qid)
        except Exception:
            log.exception("清理题卡版本失败 #%s", qid)
    return ok


def remove_batch(qids):
    """按来源一次性清空一批 —— 导入完发现导错了，一键撤干净。"""
    targets = {str(int(x)) for x in qids}
    left = []
    gone = 0
    for q in load():
        sid = str(q.get("id", "")).strip()
        if sid in targets:
            gone += 1
            try:
                fp = fig_path(int(sid))
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception as e:
                log.warning("删除题卡图失败 %s: %r", fp, e)
        else:
            left.append(q)
    if not save(left):
        return 0
    try:
        import question_versions as QV
        for qid in targets:
            QV.remove_versions(qid)
    except Exception:
        log.exception("批量清理题卡版本失败")
    return gone


def count():
    return len(load())


def load_import_manifest():
    """读取导入文件清单；旧版没有清单时由题目来源元数据补齐。"""
    path = import_manifest_path()
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError, TypeError):
        value = []
    return value if isinstance(value, list) else []


def save_import_manifest(records):
    try:
        atomic_json(import_manifest_path(), list(records))
        return True
    except Exception as exc:
        log.error("导入文件清单写入失败 %r", exc)
        return False


def imported_sources(questions=None):
    """汇总文件元数据和题目关联，兼容清单功能上线前导入的旧题卡。"""
    questions = load() if questions is None else list(questions)
    records = {str(row.get("source_id") or row.get("sha256") or ""): dict(row)
               for row in load_import_manifest() if isinstance(row, dict)}
    grouped = {}
    for question in questions:
        if not isinstance(question, dict):
            continue
        source = question.get("reference_source") or {}
        if not isinstance(source, dict):
            source = {}
        source_id = str(question.get("source_id") or source.get("sha256") or "")
        file_name = str(source.get("file_name") or question.get("source") or "本地导入")
        file_type = str(source.get("kind") or "image").lower()
        if not source_id:
            source_id = "legacy:" + file_type + ":" + file_name
        group = grouped.setdefault(source_id, {
            "source_id": source_id,
            "file_name": file_name,
            "file_type": "PDF" if file_type == "pdf" else "图片",
            "size_bytes": int(source.get("file_size") or 0),
            "imported_at": "历史导入",
            "question_ids": [],
            "status": "成功",
            "failure_reason": "",
        })
        try:
            group["question_ids"].append(int(question.get("id")))
        except (TypeError, ValueError):
            pass
    for source_id, row in records.items():
        if not source_id:
            continue
        current = grouped.get(source_id, {})
        merged = dict(row)
        merged.update({key: value for key, value in current.items()
                       if value not in (None, "", [], 0)})
        merged["source_id"] = source_id
        merged["question_ids"] = sorted(set(
            int(value) for value in (merged.get("question_ids") or [])
            if str(value).isdigit()))
        grouped[source_id] = merged
    return sorted(grouped.values(),
                  key=lambda row: (str(row.get("imported_at") or ""),
                                   str(row.get("file_name") or "")), reverse=True)


def imported_source_hashes():
    """返回已导入来源文件的摘要，用于在识别前阻止重复处理。"""
    hashes = {str(row.get("source_id") or row.get("sha256") or "").lower()
              for row in load_import_manifest() if isinstance(row, dict)}
    for question in load():
        source = question.get("reference_source") or {}
        if isinstance(source, dict) and source.get("sha256"):
            hashes.add(str(source["sha256"]).lower())
    return {value for value in hashes if len(value) == 64}


def register_imported_sources(records):
    """在题目成功保存后登记来源文件及其关联题号。"""
    old = {str(row.get("source_id") or row.get("sha256") or ""): dict(row)
           for row in load_import_manifest() if isinstance(row, dict)}
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for row in records:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("source_id") or row.get("sha256") or "")
        if not source_id:
            continue
        merged = dict(old.get(source_id, {}))
        merged.update(row)
        merged["source_id"] = source_id
        merged["imported_at"] = merged.get("imported_at") or now
        merged["question_ids"] = sorted(set(
            int(value) for value in merged.get("question_ids", [])
            if str(value).isdigit()))
        old[source_id] = merged
    return save_import_manifest(list(old.values()))


def remove_imported_sources(source_ids):
    """按文件批量删除其导入题卡与题图，并同步清单。"""
    targets = {str(value) for value in source_ids}
    if not targets:
        return 0
    rows = imported_sources()
    question_ids = {int(qid) for row in rows if row["source_id"] in targets
                    for qid in row.get("question_ids", [])}
    removed = remove_batch(question_ids) if question_ids else 0
    if question_ids and not removed:
        return 0
    manifest = [row for row in load_import_manifest()
                if str(row.get("source_id") or row.get("sha256") or "") not in targets]
    if not save_import_manifest(manifest):
        return 0
    return removed
