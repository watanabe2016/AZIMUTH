import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

from diagram_crop import diagram_bounds


@unittest.skipIf(cv2 is None or np is None, "图形裁切依赖 OpenCV 与 NumPy")
class DiagramCropTests(unittest.TestCase):
    def test_joins_split_graph_lines_without_absorbing_text_below(self):
        gray = np.full((1000, 800), 255, dtype=np.uint8)
        cv2.line(gray, (280, 150), (360, 290), 0, 4)
        cv2.line(gray, (360, 300), (360, 500), 0, 4)
        cv2.line(gray, (368, 500), (540, 350), 0, 4)
        cv2.line(gray, (200, 550), (650, 550), 0, 3)

        bounds = diagram_bounds(gray)
        self.assertIsNotNone(bounds)
        x, y, width, height = bounds
        self.assertLessEqual(x, 280)
        self.assertLessEqual(y, 150)
        self.assertGreaterEqual(x + width, 540)
        self.assertLess(y + height, 550)

    def test_returns_no_crop_when_the_page_has_no_diagram(self):
        gray = np.full((1000, 800), 255, dtype=np.uint8)
        cv2.putText(gray, "question", (150, 600), cv2.FONT_HERSHEY_SIMPLEX,
                    1, 0, 2)
        self.assertIsNone(diagram_bounds(gray))

    def test_triangle_near_body_preserves_all_edges_and_point_numbers(self):
        gray = np.full((1125, 900),255,dtype=np.uint8)
        cv2.line(gray,(305,190),(305,510),0,3)
        cv2.line(gray,(305,510),(625,510),0,3)
        cv2.line(gray,(305,190),(625,510),0,3)
        for center in ((284,183),(288,522),(638,511)):
            cv2.circle(gray,center,12,0,2)
        cv2.putText(gray,'1. TASK ROUTE HEIGHT AND SPEED',(85,573),
                    cv2.FONT_HERSHEY_SIMPLEX,1,0,2)
        x,y,w,h=diagram_bounds(gray)
        self.assertLessEqual(x,271)
        self.assertLessEqual(y,170)
        self.assertGreaterEqual(x+w,651)
        self.assertGreaterEqual(y+h,535)
        # 字体首行从 551 开始，裁切下界不包含该行。
        self.assertLessEqual(y+h,551)


if __name__ == "__main__":
    unittest.main()
