import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


class ImportedWorkbenchOpenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])
        # 主程序先创建 QApplication，再导入题库页，保持与正式启动顺序一致。
        from bank_page import BankPage

        cls.BankPage = BankPage

    def test_imported_question_opens_when_parameter_unit_is_missing(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QPixmap

        with tempfile.TemporaryDirectory() as folder:
            image_path = Path(folder) / "imported-question.png"
            image = QPixmap(180, 240)
            image.fill(Qt.white)
            self.assertTrue(image.save(str(image_path)))

            question = {
                "id": 987654321,
                "source": "导入题测试",
                "imported": True,
                "graph_hint": "三角形",
                "conditions_text": "航线高度75米。",
                "requirements_text": "",
                "suggested_text": "",
                "reference_source": {
                    "kind": "image",
                    "diagram_model": {"shape": "三角形", "nodes": ["1", "2", "3"]},
                },
            }

            page = self.BankPage()
            page.cur = question
            page._fig_path = lambda _qid: str(image_path)
            answer = {
                "conf": "partial",
                "source": "imported",
                "shape": {"name": "三角形"},
                "points": {},
                "params": [{"key": "speed", "meaning": "水平速度"}],
                "extras": {},
            }
            try:
                with patch("answer_panel.AN.derive", return_value=answer):
                    page.open_figure_win()
                self.app.processEvents()
                self.assertIsNotNone(page.fig_win)
                self.assertTrue(page.fig_win.isVisible())
                self.assertEqual(page.fig_win.apanel.q["id"], question["id"])
                self.assertIn("speed", page.fig_win.apanel._pm_edits)
                self.assertEqual(len(page.fig_win._resize_handles), 4)
                self.assertTrue(all(handle.width() == 24 and
                                    handle.height() == 24
                                    for handle in page.fig_win._resize_handles))

                class PointerEvent:
                    def __init__(self, point, button, buttons):
                        self.point = QPointF(point)
                        self._button = button
                        self._buttons = buttons

                    def button(self):
                        return self._button

                    def buttons(self):
                        return self._buttons

                    def globalPosition(self):
                        return self.point

                    def accept(self):
                        pass

                window = page.fig_win
                window.showMaximized()
                self.app.processEvents()
                self.assertTrue(window.isMaximized())
                handle = window._resize_handles[3]
                pressed = handle.mapToGlobal(
                    QPoint(handle.width() // 2, handle.height() // 2))
                left = Qt.MouseButton.LeftButton
                handle.mousePressEvent(PointerEvent(pressed, left, left))
                self.assertTrue(handle._restore_on_move)

                first_move = pressed + QPoint(20, 15)
                handle.mouseMoveEvent(PointerEvent(
                    first_move, Qt.MouseButton.NoButton, left))
                self.assertFalse(window.isMaximized())
                restored = window.geometry()
                self.assertEqual(restored.right(), first_move.x())
                self.assertEqual(restored.bottom(), first_move.y())

                second_move = first_move + QPoint(30, 25)
                handle.mouseMoveEvent(PointerEvent(
                    second_move, Qt.MouseButton.NoButton, left))
                self.assertEqual(window.width(), restored.width() + 30)
                self.assertEqual(window.height(), restored.height() + 25)
            finally:
                if page.fig_win is not None:
                    page.fig_win.close()
                page.close()
                self.app.processEvents()

if __name__ == "__main__":
    unittest.main()
