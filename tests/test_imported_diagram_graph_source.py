import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import reference_engine as RE


class ImportedDiagramGraphSourceTests(unittest.TestCase):
    def test_ocr_labeled_imported_points_build_graph_without_name_error(self):
        words = [
            {"text": str(index), "x": index * 100, "y": 20,
             "width": 12, "height": 18, "confidence": 0.99}
            for index in range(1, 5)
        ]
        question = {
            "imported": True,
            "reference_source": {"ocr_words": words},
        }

        nodes, geometry_edges, flight_edges, source = RE._graph(
            question, "四边形")

        self.assertEqual(nodes, ["1", "2", "3", "4"])
        self.assertEqual(len(geometry_edges), 4)
        self.assertEqual(flight_edges, geometry_edges)
        self.assertEqual(source, "imported_diagram")


if __name__ == "__main__":
    unittest.main()
