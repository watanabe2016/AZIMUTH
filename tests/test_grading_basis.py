"""导入题卡本地判题依据的回归检查。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import answers as A
import geo
import judge
import linkgcs


class GradingBasisTests(unittest.TestCase):
    def test_imported_conditions_without_api(self):
        q = {'id': 169, 'imported': True, 'graph_hint': '①②③④',
             'conditions_text': '起飞点经纬度：104.4165°，30.8690°\n'
             '起飞点在1点距离90米，∠H12=90°，∠H14=120°。\n'
             '航线高度为50M。水平速度为5m/s，垂直速度为2m/s。\n'
             '停止转弯，各点停留时间5秒。任务航线闭合不循环。'}
        ans = A.derive_for_grading(q)
        self.assertEqual(set(ans['points']), {'起飞点'})
        self.assertEqual(len(ans['params']), 4)
        self.assertTrue(ans['partial_grading'])
        self.assertTrue(A.derive(q)['api_answer_pending'])
        result = judge.judge(q, {'起飞点': (30.869, 104.4165)},
                             {p['key']: p['value'] for p in ans['params']},
                             ans['extras'], ans)
        self.assertTrue(result['judgeable'])
        self.assertTrue(result['partial_grading'])
        self.assertEqual(result['n_ok'], 7)

    def test_coordinates_do_not_guess_ambiguous_order(self):
        self.assertEqual(geo.parse_latlon('经纬度：30.869°，104.4165°'), (30.869, 104.4165))
        self.assertEqual(geo.parse_latlon('经纬度：50°，40°'), (None, None))
        self.assertEqual(geo.parse_latlon('边长75米，夹角30°'), (None, None))

    def test_no_evidence_is_not_zero_error_pass(self):
        q = {'imported': True, 'conditions_text': '停留时间为f。'}
        result = judge.judge(q)
        self.assertFalse(result['judgeable'])
        self.assertFalse(result['ok'])

    def test_default_roles_share_waypoint(self):
        for text, resolver, role in (
            ('存在空域限制', A._resolve_avoid_point, A.AVOID),
            ('先到过渡点', A._resolve_transit_point, A.TRANSIT)):
            points = {'图形1号点': (30, 104)}
            resolver({'conditions_text': text}, points, {})
            self.assertEqual(points[role], points['图形1号点'])
        ans = {'points': {name: {'lat': 30, 'lon': 104} for name in
                          ('图形1号点', A.AVOID, A.TRANSIT)}}
        matched, _ = linkgcs.align_points(ans, {'points': [{'lat': 30, 'lon': 104}]})
        self.assertEqual(len(matched), 3)


if __name__ == '__main__':
    unittest.main()
