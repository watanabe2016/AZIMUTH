# -*- coding: utf-8 -*-
"""用户题卡版本：保存题面、参考答案与题图的本地历史快照。"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil

import bankdata as BD
import qmeta as QM
from storage import atomic_json
from logger import get_logger

log = get_logger()
_recording_suspended = 0


def _question_id(question_or_id):
    value = (question_or_id.get("id") if isinstance(question_or_id, dict)
             else question_or_id)
    try:
        return str(int(value))
    except (TypeError, ValueError, OverflowError):
        return ""


def _history_dir(qid):
    return Path(BD.root()) / "versions" / str(int(qid))


def _normalized_question(question):
    result = deepcopy(question) if isinstance(question, dict) else {}
    result.pop("imported", None)
    return result


def _image_digest(image_path):
    digest = hashlib.sha256()
    try:
        with open(image_path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _signature(question, manual_answer, verified_reference, image_sha256):
    payload = {
        "question": _normalized_question(question),
        "manual_answer": str(manual_answer or ""),
        "verified_reference": verified_reference,
        "image_sha256": str(image_sha256 or ""),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_version(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        if isinstance(value, dict) and isinstance(value.get("question"), dict):
            return value
    except (OSError, ValueError, TypeError):
        log.warning("题目版本文件不可读：%s", path)
    return None


def versions_of(qid):
    """返回某道题的版本记录，最新版本在前。"""
    qid = _question_id(qid)
    if not qid:
        return []
    directory = _history_dir(qid)
    rows = []
    try:
        for path in directory.glob("v*.json"):
            row = _read_version(path)
            if row and str(row.get("question_id")) == qid:
                rows.append(row)
    except OSError:
        return []
    return sorted(rows, key=lambda row: int(row.get("version", 0)), reverse=True)


def image_path_of(qid, row):
    """返回版本题图路径；缺图时返回空路径。"""
    qid = _question_id(qid)
    name = Path(str((row or {}).get("image_file") or "")).name
    path = _history_dir(qid) / name if qid and name else None
    return str(path) if path and path.is_file() else ""


def _matching_existing(question, manual_answer, verified_reference):
    """同一题面/答案已存档时不因后台重复保存而堆出重复版本。"""
    qid = _question_id(question)
    normalized = _normalized_question(question)
    for row in versions_of(qid):
        if (_normalized_question(row.get("question")) == normalized and
                str(row.get("manual_answer") or "") == str(manual_answer or "") and
                row.get("verified_reference") == verified_reference):
            return row
    return None


def record_snapshot(question, reason="题卡更新"):
    """记录一个版本；若当前题面、答案和题图未变，则返回已有版本。"""
    if _recording_suspended:
        return None
    qid = _question_id(question)
    if not qid or not isinstance(question, dict):
        return None
    normalized = _normalized_question(question)
    manual_answer = QM.answer_of(qid)
    verified = QM.verified_reference_of(qid)
    image_source = Path(BD.fig_path(int(qid)))
    image_hash = _image_digest(image_source)
    signature = _signature(normalized, manual_answer, verified, image_hash)
    latest = versions_of(qid)
    if latest and latest[0].get("signature") == signature:
        return latest[0]

    version = max((int(row.get("version", 0)) for row in latest), default=0) + 1
    directory = _history_dir(qid)
    directory.mkdir(parents=True, exist_ok=True)
    image_name = ""
    if image_hash and image_source.is_file():
        suffix = image_source.suffix.lower() or ".jpg"
        image_name = "image_%s%s" % (image_hash, suffix)
        image_target = directory / image_name
        if not image_target.exists():
            temporary = directory / (image_name + ".tmp")
            try:
                shutil.copyfile(image_source, temporary)
                os.replace(temporary, image_target)
            finally:
                if temporary.exists():
                    temporary.unlink()

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    row = {
        "question_id": qid,
        "version": version,
        "created_at": now,
        "reason": str(reason or "题卡更新"),
        "signature": signature,
        "question": normalized,
        "manual_answer": manual_answer,
        "verified_reference": deepcopy(verified),
        "image_file": image_name,
        "image_sha256": image_hash,
    }
    atomic_json(directory / ("v%04d.json" % version), row)
    return row


def record_current(qid, reason="参考答案更新"):
    """按题号读取当前题卡并留档，供独立元数据更新调用。"""
    qid = _question_id(qid)
    if not qid:
        return None
    question = next((row for row in BD.load()
                     if _question_id(row) == qid), None)
    return record_snapshot(question, reason) if question else None


def record_changes(previous_questions, current_questions,
                   reason="题卡内容更新"):
    """题库写盘成功后自动保存新增题卡及有变化的题卡。"""
    if _recording_suspended:
        return
    old_by_id = {_question_id(row): row for row in previous_questions or ()
                 if _question_id(row)}
    for question in current_questions or ():
        qid = _question_id(question)
        if not qid or not isinstance(question, dict):
            continue
        previous = old_by_id.get(qid)
        if previous is None:
            record_snapshot(question, "首次导入")
            continue
        manual_answer = QM.answer_of(qid)
        verified_reference = QM.verified_reference_of(qid)
        current_image_hash = _image_digest(BD.fig_path(int(qid)))
        current_signature = _signature(
            question, manual_answer, verified_reference, current_image_hash)
        latest = versions_of(qid)
        if latest and latest[0].get("signature") == current_signature:
            continue
        existing = _matching_existing(
            previous, manual_answer, verified_reference)
        # 图片已经换掉且旧快照不存在时，不能拿新图伪装成旧版本。
        previous_image_is_current = (
            not latest or latest[0].get("image_sha256", "") == current_image_hash)
        if existing is None and previous_image_is_current:
            record_snapshot(previous, "修改前版本")
        record_snapshot(question, reason)


@contextmanager
def suspend_recording():
    """合并恢复操作中的多份文件写入，最后仅保存一个完整版本。"""
    global _recording_suspended
    _recording_suspended += 1
    try:
        yield
    finally:
        _recording_suspended = max(0, _recording_suspended - 1)


def _copy_image_from_version(row, qid):
    source_path = image_path_of(qid, row)
    source = Path(source_path) if source_path else None
    destination = Path(BD.fig_path(int(qid)))
    if source and source.is_file():
        temporary = destination.with_suffix(destination.suffix + ".restore.tmp")
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    elif destination.exists():
        destination.unlink()


def restore_version(qid, version):
    """恢复指定题卡版本；恢复本身也会作为一条新版本保留。"""
    qid = _question_id(qid)
    try:
        version = int(version)
    except (TypeError, ValueError, OverflowError):
        return False, "版本号无效。"
    target = next((row for row in versions_of(qid)
                   if int(row.get("version", -1)) == version), None)
    if target is None:
        return False, "找不到该题目版本。"

    questions = BD.load()
    index = next((i for i, row in enumerate(questions)
                  if _question_id(row) == qid), None)
    if index is None:
        return False, "这道题已不在当前题库中。"
    current = questions[index]
    backup = record_snapshot(current, "恢复前自动备份")
    if backup is None:
        return False, "无法创建恢复前备份，当前题目未更改。"

    replacement = deepcopy(target["question"])
    replacement["id"] = int(qid)
    replacement["imported"] = True
    questions[index] = replacement
    try:
        with suspend_recording():
            _copy_image_from_version(target, qid)
            if not BD.save(questions, record_version=False):
                _copy_image_from_version(backup, qid)
                return False, "题卡内容保存失败，已保留当前版本。"
            if not QM.restore_versioned_state(
                    qid, target.get("manual_answer", ""),
                    target.get("verified_reference")):
                questions[index] = current
                BD.save(questions, record_version=False)
                _copy_image_from_version(backup, qid)
                QM.restore_versioned_state(
                    qid, backup.get("manual_answer", ""),
                    backup.get("verified_reference"))
                return False, "参考答案状态保存失败，已还原当前题卡。"
    except Exception as exc:
        log.exception("恢复题目版本失败 #%s v%s", qid, version)
        questions[index] = current
        BD.save(questions, record_version=False)
        try:
            _copy_image_from_version(backup, qid)
            QM.restore_versioned_state(
                qid, backup.get("manual_answer", ""),
                backup.get("verified_reference"))
        except Exception:
            log.exception("回滚题目版本失败 #%s", qid)
        return False, "恢复失败，已尝试还原当前题卡：%s" % exc

    record_snapshot(replacement, "恢复自版本 v%d" % version)
    return True, "已恢复到版本 v%d。" % version


def remove_versions(qid):
    """删除题卡时同步删除只属于该题的版本图像与记录。"""
    qid = _question_id(qid)
    if not qid:
        return False
    directory = _history_dir(qid)
    if not directory.exists():
        return True
    try:
        shutil.rmtree(directory)
        return True
    except OSError as exc:
        log.warning("删除题目版本失败 #%s: %r", qid, exc)
        return False
