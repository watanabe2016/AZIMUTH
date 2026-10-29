import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import bankdata as BD
import question_versions as QV


class QuestionVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_root = BD._root
        self.previous_portable = BD._portable
        BD._root = self.temp.name
        BD._portable = True
        self.meta = {"answer": "", "verified": None}
        self.answer_patch = patch.object(
            QV.QM, "answer_of", side_effect=lambda _qid: self.meta["answer"])
        self.verified_patch = patch.object(
            QV.QM, "verified_reference_of",
            side_effect=lambda _qid: self.meta["verified"])
        self.restore_patch = patch.object(
            QV.QM, "restore_versioned_state", side_effect=self._restore_meta)
        self.answer_patch.start()
        self.verified_patch.start()
        self.restore_patch.start()

    def tearDown(self):
        self.restore_patch.stop()
        self.verified_patch.stop()
        self.answer_patch.stop()
        QV._recording_suspended = 0
        BD._root = self.previous_root
        BD._portable = self.previous_portable
        self.temp.cleanup()

    def _restore_meta(self, _qid, answer, verified):
        self.meta["answer"] = answer
        self.meta["verified"] = verified
        return True

    @staticmethod
    def _question(conditions="原始条件"):
        return {"id": 760001, "imported": True, "source": "测试题",
                "conditions_text": conditions, "requirements_text": "要求"}

    def _save_image(self, content):
        image = Path(BD.fig_path(760001))
        image.write_bytes(content)
        return image

    def test_identical_snapshots_share_image_file(self):
        question = self._question()
        self.assertTrue(BD.save([question], record_version=False))
        self._save_image(b"same image")

        first = QV.record_snapshot(question, "导入")
        second = QV.record_snapshot(question, "再次查看")

        self.assertEqual(first["version"], second["version"])
        self.assertEqual(first["image_file"], second["image_file"])
        stored_images = list((Path(BD.root()) / "versions" / "760001").glob("image_*"))
        self.assertEqual(len(stored_images), 1)

    def test_restore_replaces_card_image_and_answer_state(self):
        original = self._question("原始条件")
        self.assertTrue(BD.save([original], record_version=False))
        original_image = self._save_image(b"original image")
        old_version = QV.record_snapshot(original, "导入")

        changed = self._question("修改后的条件")
        self.meta["answer"] = "当前人工答案"
        self._save_image(b"changed image")
        self.assertTrue(BD.save([changed], record_version=False))
        current_version = QV.record_snapshot(changed, "内容修改")

        success, _message = QV.restore_version(760001, old_version["version"])

        self.assertTrue(success)
        restored = BD.load()[0]
        self.assertEqual(restored["conditions_text"], "原始条件")
        self.assertEqual(original_image.read_bytes(), b"original image")
        self.assertEqual(self.meta["answer"], "")
        self.assertGreater(QV.versions_of(760001)[0]["version"],
                           current_version["version"])


if __name__ == "__main__":
    unittest.main()
