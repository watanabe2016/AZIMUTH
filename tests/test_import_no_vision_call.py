import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import importer as IM


class ImportWithoutVisionTests(unittest.TestCase):
    def test_picture_import_does_not_start_vision_model(self):
        with tempfile.TemporaryDirectory() as folder:
            image_path = os.path.join(folder, "card.png")
            image = QImage(1000, 1000, QImage.Format.Format_RGB32)
            image.fill(0xFFFFFFFF)
            self.assertTrue(image.save(image_path, "PNG"))
            with patch.object(IM.OCR, "recognize_image", return_value=(
                    "起飞点位于1点西南方向200米处", "", [])), \
                 patch.object(IM.DV, "analyze", return_value={}):
                questions, failed = IM.import_images(
                    [image_path], "本地测试", 441)
        self.assertEqual(failed, [])
        self.assertEqual(len(questions), 1)
        self.assertNotIn("condition_assist", questions[0]["_reference_source"])

    def test_pdf_pages_are_saved_directly_from_pixmap(self):
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf

        with tempfile.TemporaryDirectory() as folder:
            pdf_path = os.path.join(folder, "sample.pdf")
            document = pymupdf.open()
            page = document.new_page(width=240, height=320)
            page.insert_text((24, 60), "Altitude 75m. Route 1 2 3. Closed route.")
            document.save(pdf_path)
            document.close()

            emitted = []
            with patch.object(IM.BD, "imported_source_hashes", return_value=set()), \
                 patch.object(IM.OCR, "recognize_figure_words", return_value=[]), \
                 patch.object(IM.OCR, "recognize_image", return_value=("", "", [])), \
                 patch.object(IM.DV, "analyze", return_value={}):
                thread = IM.PdfImportThread(pdf_path, "本地 PDF", 900)
                thread.done.connect(lambda questions, warnings: emitted.append(
                    (questions, warnings)))
                thread.run()

            self.assertEqual(len(emitted), 1)
            questions, _warnings = emitted[0]
            self.assertEqual(len(questions), 1)
            staged = questions[0]["_staged_figure"]
            self.assertTrue(os.path.isfile(staged))
            self.assertFalse(QImage(staged).isNull())
            os.remove(staged)

    def test_pdf_preview_is_low_resolution_and_skips_ocr_and_diagram_analysis(self):
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf

        with tempfile.TemporaryDirectory() as folder:
            pdf_path = os.path.join(folder, "preview.pdf")
            document = pymupdf.open()
            for number in range(1, 4):
                page = document.new_page(width=240, height=320)
                page.insert_text((24, 60), "Route page %d with altitude 75m" % number)
            document.save(pdf_path)
            document.close()

            emitted = []
            with patch.object(IM.BD, "imported_source_hashes", return_value=set()), \
                 patch.object(IM.OCR, "recognize_figure_words",
                              side_effect=AssertionError("预览不能调用 OCR")), \
                 patch.object(IM.OCR, "recognize_image",
                              side_effect=AssertionError("预览不能调用 OCR")), \
                 patch.object(IM.DV, "analyze",
                              side_effect=AssertionError("预览不能识别图形")):
                thread = IM.PdfPreviewThread(pdf_path)
                thread.done.connect(lambda preview, warnings: emitted.append(
                    (preview, warnings)))
                thread.run()

            self.assertEqual(len(emitted), 1)
            preview, warnings = emitted[0]
            self.assertEqual(warnings, [])
            self.assertEqual([page["page"] for page in preview["pages"]], [1, 2, 3])
            for page in preview["pages"]:
                image = QImage(page["preview_path"])
                self.assertFalse(image.isNull())
                self.assertLess(image.width(), 240 * IM.PDF_ZOOM)
            preview_dir = preview["preview_dir"]
            IM.cleanup_pdf_preview(preview)
            self.assertFalse(os.path.exists(preview_dir))

    def test_pdf_import_processes_only_selected_pages(self):
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf

        with tempfile.TemporaryDirectory() as folder:
            pdf_path = os.path.join(folder, "selected.pdf")
            document = pymupdf.open()
            for number in range(1, 5):
                page = document.new_page(width=240, height=320)
                page.insert_text(
                    (24, 60),
                    "Altitude 75m. Route 1 2 3. Closed route. Page %d." % number)
            document.save(pdf_path)
            document.close()

            emitted = []
            with patch.object(IM.BD, "imported_source_hashes", return_value=set()), \
                 patch.object(IM.OCR, "recognize_figure_words", return_value=[]), \
                 patch.object(IM.OCR, "recognize_image", return_value=("", "", [])), \
                 patch.object(IM.DV, "analyze", return_value={}):
                thread = IM.PdfImportThread(
                    pdf_path, "本地 PDF", 900, page_numbers=[3, 1, 3])
                thread.done.connect(lambda questions, warnings: emitted.append(
                    (questions, warnings)))
                thread.run()

            self.assertEqual(len(emitted), 1)
            questions, _warnings = emitted[0]
            self.assertEqual(len(questions), 2)
            self.assertEqual([q["_reference_source"]["page"] for q in questions], [1, 3])
            for question in questions:
                staged = question["_staged_figure"]
                self.assertTrue(os.path.isfile(staged))
                self.assertFalse(QImage(staged).isNull())
                os.remove(staged)


if __name__ == "__main__":
    unittest.main()
