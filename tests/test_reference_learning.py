import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import reference_engine as RE
import reference_learning as RL
import config as CFG
import qmeta as QM


def complete_question():
    return {
        "id": 9001,
        "conditions_text": (
            "图形1号点经纬度：N：30.000000°，E：110.000000°。\n"
            "图形2号点经纬度：N：30.000000°，E：110.001000°。\n"
            "图形3号点经纬度：N：30.001000°，E：110.000000°。"),
        "requirements_text": "按图规划一个闭合三角形航线。",
        "suggested_text": "",
        "reference_graph": {
            "nodes": ["1", "2", "3"],
            "geometry_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
            "flight_edges": [["1", "2"], ["2", "3"], ["3", "1"]],
        },
    }


class ReferenceLearningTests(unittest.TestCase):
    def setUp(self):
        self.question = complete_question()
        self.points = {
            "图形1号点": (30.0, 110.0),
            "图形2号点": (30.0, 110.001),
            "图形3号点": (30.001, 110.0),
        }

    def test_complete_independently_determined_attempt_is_accepted(self):
        with (patch.object(RL.QM, "answer_of", return_value=""),
              patch.object(RL.RE, "solve_generated_reference",
                           return_value={"status": "NEEDS_REVIEW"})):
            result = RL.verify_attempt(self.question, self.points)
        self.assertEqual(result["status"], "NEW", result.get("reason"))
        self.assertEqual(set(result["solution"]["points"]), {"1", "2", "3"})
        self.assertEqual(result["solution"]["access_edges"], [])

    def test_missing_or_wrong_vertex_is_not_learned(self):
        with patch.object(RL.QM, "answer_of", return_value=""):
            missing = RL.verify_attempt(self.question,
                                        {k: v for k, v in self.points.items()
                                         if k != "图形3号点"})
            wrong = dict(self.points)
            wrong["图形3号点"] = (30.01, 110.0)
            misplaced = RL.verify_attempt(self.question, wrong)
        self.assertEqual(missing["status"], "REJECTED")
        self.assertEqual(misplaced["status"], "REJECTED")

    def test_correct_attempt_is_persisted_only_after_independent_check(self):
        with (patch.object(RL.QM, "answer_of", return_value=""),
              patch.object(RL.QM, "set_verified_reference", return_value=True) as save,
              patch.object(RL.RE, "solve_generated_reference",
                           return_value={"status": "NEEDS_REVIEW"})):
            accepted = RL.learn_from_attempt(self.question, self.points)
            rejected = RL.learn_from_attempt(self.question,
                                             {"图形1号点": (30.0, 110.0)})
        self.assertEqual(accepted["status"], "SAVED")
        self.assertEqual(rejected["status"], "REJECTED")
        save.assert_called_once()

    def test_verified_answer_survives_config_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = str(Path(directory) / "config.json")
            with (patch.object(CFG, "_path", destination),
                  patch.object(CFG, "_cache", None),
                  patch.object(RL.RE, "solve_generated_reference",
                               return_value={"status": "NEEDS_REVIEW"})):
                learned = RL.learn_from_attempt(self.question, self.points)
                self.assertEqual(learned["status"], "SAVED")
                CFG._cache = None
                restored = RE.solve_reference(self.question)
                self.assertEqual(restored["source"], "verified_attempt")
                self.assertEqual(set(restored["points"]), {"1", "2", "3"})

    def test_reference_fingerprint_changes_with_edited_conditions(self):
        changed = dict(self.question)
        changed["conditions_text"] += "\n图形1到2的距离为100米。"
        self.assertNotEqual(RE.question_fingerprint(self.question),
                            RE.question_fingerprint(changed))

    def test_stale_builtin_answer_metadata_is_removed_but_imported_data_remains(self):
        state = {
            "done_ids": [232, 442],
            "history": {"232": {"count": 1}, "442": {"count": 3}},
            "wrong": {"232": {"n": 1}, "442": {"n": 2}},
            "answer_text": {"232": "旧答案", "442": "导入题答案"},
            "verified_references": {"232": {"status": "READY"},
                                    "442": {"status": "READY"}},
            "manual_tags": {"232": ["wind"], "442": ["angle"]},
            "manual_tags_off": {},
            "avoid_point_overrides": {},
            "trend": {"2026-10-01": {"ok": 2, "bad": 0, "qs": [232]},
                      "2026-10-02": {"ok": 1, "bad": 0, "qs": [442]}},
            "last_question_id": 232,
        }
        with patch.object(CFG, "_cache", state), \
             patch.object(CFG, "save", return_value=True) as save:
            self.assertTrue(QM.retain_imported_question_ids([442]))
            restored = CFG.load()
        self.assertEqual(restored["done_ids"], [442])
        self.assertEqual(set(restored["answer_text"]), {"442"})
        self.assertEqual(set(restored["verified_references"]), {"442"})
        self.assertEqual(restored["trend"], {
            "2026-10-02": {"ok": 1, "bad": 0, "qs": [442]}})
        self.assertIsNone(restored["last_question_id"])
        save.assert_called_once()

    def test_gcs_approach_point_keeps_paper_numbering(self):
        spec = {"nodes": ["1", "2", "3"],
                "segment_constraints": [
                    {"a": "A", "b": "H"}, {"a": "1", "b": "A"}],
                "distance_constraints": []}
        task = {"takeoff": (30.0, 110.0), "points": [
            {"lat": 30.0001, "lon": 110.0},
            {"lat": 30.0002, "lon": 110.0},
            {"lat": 30.0003, "lon": 110.0},
            {"lat": 30.0004, "lon": 110.0}]}
        result = RL._ordered_task_points(spec, task)
        self.assertEqual(result["A"], (30.0001, 110.0))
        self.assertEqual(result["1"], (30.0002, 110.0))
        self.assertEqual(result["3"], (30.0004, 110.0))

if __name__ == "__main__":
    unittest.main()
