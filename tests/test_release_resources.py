from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from build.check_release_integrity import check_names


class ReleaseResourceTests(unittest.TestCase):
    def test_bundled_assets_contain_no_old_question_corpus(self):
        assets = ROOT / "assets"
        forbidden = (
            "questions.json", "reference_answers.json", "figure_constraints.json",
            "figure_labels.json", "figure_shapes.json", "layout_map.json",
            "vision_experience.json", "figures", "figure_models",
        )
        for name in forbidden:
            with self.subTest(name=name):
                self.assertFalse((assets / name).exists())

    def test_static_layout_map_is_not_shipped_with_user_data(self):
        self.assertFalse((ROOT / "data" / "layout_map.json").exists())

    def test_imported_question_storage_is_separate_from_packaged_assets(self):
        source = (ROOT / "app" / "bankdata.py").read_text(encoding="utf-8")
        self.assertIn('"data", "bank"', source)
        self.assertIn('return os.path.join(root(), "questions.json")', source)

    def test_integrity_check_allows_theme_art_but_rejects_removed_root_assets(self):
        names={"assets/anna_theme/assets/figures/bow-motif.svg",
               "assets/chiyo_theme/assets/figures/sakura.svg"}
        self.assertEqual(check_names(names),names)
        with self.assertRaises(RuntimeError):check_names(["assets/figures/question.svg"])
        with self.assertRaises(RuntimeError):check_names(["AZIMUTH/_internal/assets/questions.json"])


if __name__ == "__main__":
    unittest.main()
