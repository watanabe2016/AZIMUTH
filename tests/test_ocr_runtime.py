import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import ocr


class OCRRuntimePathTests(unittest.TestCase):
    def test_rapid_ocr_runs_one_adaptively_sized_inference_per_page(self):
        class FakeImage:
            def __init__(self, height, width):
                self.shape = (height, width, 3)

        class FakeCV2:
            IMREAD_COLOR = 1
            INTER_AREA = 2
            INTER_CUBIC = 3

            @staticmethod
            def imdecode(_data, _mode):
                return FakeImage(3000, 5000)

            @staticmethod
            def resize(_image, _none, fx, fy, interpolation):
                self_interpolation.append(interpolation)
                return FakeImage(round(3000 * fy), round(5000 * fx))

        class FakeEngine:
            def __init__(self):
                self.images = []

            def __call__(self, image):
                self.images.append(image)
                return SimpleNamespace(
                    txts=["1、航线高度60米"], scores=[0.98],
                    boxes=[[[20, 20], [260, 20], [260, 60], [20, 60]]])

        self_interpolation = []
        fake_numpy = SimpleNamespace(uint8=object(),
                                     fromfile=lambda *_args, **_kwargs: object())
        fake_rapid = SimpleNamespace(RapidOCR=lambda: None)
        engine = FakeEngine()
        with tempfile.NamedTemporaryFile() as image:
            with patch.dict(sys.modules, {"cv2": FakeCV2,
                                          "numpy": fake_numpy,
                                          "rapidocr": fake_rapid}), \
                 patch.object(ocr, "_rapid_ocr", engine):
                words = ocr._rapid_words(image.name)

        self.assertEqual(len(engine.images), 1)
        self.assertEqual(engine.images[0].shape[:2], (1080, 1800))
        self.assertEqual(self_interpolation, [FakeCV2.INTER_AREA])
        self.assertEqual(words[0]["text"], "1、航线高度60米")

    def test_tesseract_language_probe_is_cached(self):
        ocr.available_languages.cache_clear()
        engine = Path("fake-tesseract.exe")
        process = SimpleNamespace(stdout="chi_sim\neng\n", stderr="", returncode=0)
        with patch.object(ocr, "engine_path", return_value=engine), \
             patch.object(ocr, "tessdata_path", return_value=Path("fake-data")), \
             patch.object(ocr.subprocess, "run", return_value=process) as run:
            first = ocr.available_languages()
            second = ocr.available_languages()
        ocr.available_languages.cache_clear()

        self.assertEqual(first, second)
        self.assertIn("chi_sim", first[0])
        run.assert_called_once()

    def test_missing_numbered_condition_row_is_recovered_and_reordered(self):
        words = []
        for number, y in ((1, 10), (2, 30), (4, 70), (5, 90)):
            words.append({
                "text": f"{number}、条件内容{number}", "confidence": 0.99,
                "x": 0, "y": y, "width": 100, "height": 12, "angle": 0,
            })
        recovered = [{
            "text": "3、起飞点到1点SW方向50米。", "confidence": 0.99,
            "x": 0, "y": 50, "width": 180, "height": 12, "angle": 0,
        }]

        merged = ocr._merge_recovered_condition_rows(words, recovered)
        text = ocr._rapid_question_text(merged)

        self.assertIn("3、起飞点到1点SW方向50米。", text)
        self.assertLess(text.index("2、条件内容2"),
                        text.index("3、起飞点到1点SW方向50米。"))
        self.assertLess(text.index("3、起飞点到1点SW方向50米。"),
                        text.index("4、条件内容4"))

    def test_bundled_model_path_is_relative_to_unicode_install_directory(self):
        engine = Path(r"D:\个人项目\AZIMUTH\_internal\tools\ocr\Tesseract-OCR\tesseract.exe")
        model = engine.parent / "tessdata"

        cwd, model_arg = ocr._tesseract_path_args(engine, model)

        self.assertEqual(cwd, engine.parent)
        self.assertEqual(model_arg, "tessdata")

    def test_external_model_path_is_preserved(self):
        engine = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        model = Path(r"E:\共享数据\tessdata")

        cwd, model_arg = ocr._tesseract_path_args(engine, model)

        self.assertEqual(cwd, engine.parent)
        self.assertEqual(model_arg, str(model))


if __name__ == "__main__":
    unittest.main()
