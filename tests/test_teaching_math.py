"""独立数学校验：正确值、逆向、边界与反例。"""
import sys,math,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from teaching_math import *
from teaching_courses import build_course,defaults,SHAPE_KEYS
from teaching_cases import case_course


class TeachingMathTests(unittest.TestCase):
    def test_bearing_boundaries(self):
        for value,answer in ((360,0),(-15,345),(725,5),(0,0)):
            self.assertEqual(normalizeBearing(value),answer)
        self.assertEqual(reverseBearing(255),75)
        for value in (math.nan,math.inf,-math.inf):
            with self.assertRaises(ValueError):normalizeBearing(value)

    def test_quadrants_and_invalid_pairs(self):
        for base,toward,angle,answer in [('西','南',15,255),('北','东',30,30),('东','北',30,60),('南','西',90,270)]:
            self.assertEqual(quadrantBearingToAzimuth(base,toward,angle),answer)
        for args in [('北','南',20),('东','东',0),('西','南',91)]:
            with self.assertRaises(ValueError):quadrantBearingToAzimuth(*args)

    def test_plane_access_and_inverse(self):
        h=Point(0,0);r=destinationPoint(h,315,600);p=destinationPoint(r,135,500)
        self.assertAlmostEqual(distanceBetween(h,p),100)
        self.assertAlmostEqual(azimuthBetweenPoints(h,p),315)
        for bearing in range(0,360,15):
            target=destinationPoint(Point(42,-12),bearing,321)
            self.assertAlmostEqual(azimuthBetweenPoints(Point(42,-12),target),bearing)
        with self.assertRaises(ValueError):azimuthBetweenPoints(h,h)
        with self.assertRaises(ValueError):destinationPoint(h,0,-1)

    def test_triangle_methods_and_ssa_branches(self):
        for known in ({'a':3,'b':4,'c':5},{'b':3,'c':4,'A':90},{'a':3,'A':30,'B':60}):
            result=solveTriangle(**known);self.assertEqual(result.status,'unique')
            for candidate in result.candidates:self.assertAlmostEqual(sum(candidate[k] for k in 'ABC'),180)
        result=solveTriangle(a=7,b=10,A=30)
        self.assertEqual(result.status,'multi');self.assertEqual(len(result.candidates),2)
        self.assertEqual(solveTriangle(a=3,b=10,A=30).status,'no_solution')
        self.assertEqual(solveTriangle(a=1,b=2,c=3).status,'no_solution')
        self.assertEqual(solveTriangle(A=30,B=60).status,'insufficient')
        self.assertEqual(solveTriangle(a=3,b=4,c=5,A=30).status,'no_solution')

    def test_exact_regular_geometry(self):
        for kind,n in [('triangle',3),('hexagon',6)]:
            data=solvePolygon(kind,300,255).candidates[0]
            self.assertEqual(len(data['points']),n)
            for distance in data['lengths']:self.assertAlmostEqual(distance,300)
            for angle in data['interior']:self.assertAlmostEqual(angle,180-360/n)
            self.assertAlmostEqual(data['headings'][0],255)
        data=solvePolygon('trapezoid',200,250,short=200,long=300,angle=60).candidates[0]
        for value,expected in zip(data['headings'],[250,160,100,340]):self.assertAlmostEqual(value,expected)
        self.assertAlmostEqual(data['lengths'][0],100*math.sqrt(3))
        self.assertAlmostEqual(data['lengths'][2],200)

    def test_concave_crossed_open_and_circle_semantics(self):
        data=solvePolygon('concave').candidates[0]
        self.assertGreater(max(data['interior']),180)
        self.assertEqual(solvePolygon('butterfly').candidates[0]['interior'],[])
        scan=solvePolygon('scan',rows=3).candidates[0]
        self.assertFalse(scan['closed']);self.assertEqual(len(scan['lengths']),len(scan['points'])-1)
        self.assertAlmostEqual(solvePolygon('circle',2*math.pi).candidates[0]['radius'],1)
        self.assertEqual(solvePolygon('unknown').status,'insufficient')
        self.assertEqual(solvePolygon('trapezoid',short=200,long=100).status,'no_solution')

    def test_speed_time_and_coordinate_signs(self):
        self.assertEqual(convertSpeed(7.2),2)
        self.assertAlmostEqual(convertSpeed(2,'m/s','km/h'),7.2)
        self.assertEqual(convertDistance(1000),1)
        self.assertEqual(travelTime(60,2),30)
        with self.assertRaises(ValueError):travelTime(1,0)
        self.assertAlmostEqual(dmsToDecimal(120,13,14,'E'),120+13/60+14/3600)
        self.assertEqual(dmsToDecimal(30,0,0,'S'),-30)
        for args in [(-30,0,0,'N'),(90,0,1,'N'),(180,1,0,'E'),(1,60,0,'W')]:
            with self.assertRaises(ValueError):dmsToDecimal(*args)
        self.assertEqual(decimalToDms(12+59/60+59.9999/3600,precision=2),(13,0,0,'E'))
        for val,axis in [(-31.5678,'latitude'),(120.234567,'longitude')]:
            d,m,s,h=decimalToDms(val,axis,6);self.assertAlmostEqual(dmsToDecimal(d,m,s,h),val,places=8)

    def test_wind_from_and_physical_constraints(self):
        for wind,label in [(90,'迎风'),(270,'顺风'),(0,'正左侧风'),(180,'正右侧风')]:
            self.assertEqual(classifyRelativeWind(90,wind),label)
        result=solveWindTriangle(90,10,0,3).candidates[0]
        self.assertGreater(result['heading'],0);self.assertLess(result['heading'],90)
        air=destinationPoint(Point(0,0),result['heading'],10)
        ground=destinationPoint(air,180,3)
        self.assertAlmostEqual(azimuthBetweenPoints(Point(0,0),ground),90)
        self.assertEqual(solveWindTriangle(90,2,0,3).status,'no_solution')
        self.assertEqual(solveWindTriangle(90,2,90,3).status,'no_solution')

    def test_courses_and_review_boundaries(self):
        for chapter in ('bearing','geometry','access','wind','math','units','parameters','cases'):
            lesson=build_course(chapter)
            self.assertGreater(len(lesson.steps),0)
            self.assertEqual(len(set(s.id for s in lesson.steps)),len(lesson.steps))
        for shape in SHAPE_KEYS:build_course('geometry',{'shape':shape})
        self.assertNotEqual(len(build_course('geometry',{'shape':'triangle'}).steps),len(build_course('geometry',{'shape':'hexagon'}).steps))
        for key in ('p1','p2','p4'):
            case=case_course(key);self.assertEqual(case.status,'review_candidates');self.assertTrue(case.warnings)
        p2=case_course('p2');self.assertIn('100.0000',next(s.result for s in p2.steps if s.id=='access-result'))
        p4=case_course('p4');self.assertIn('f=5s','；'.join(p4.warnings))

    def test_case_route_preserves_access_through_geometry_and_review(self):
        for key in ('p2','p4'):
            lesson=case_course(key)
            for s in lesson.steps:
                if s.id.startswith('geometry-') or s.id in ('parameters','review'):
                    ids=[o.id for o in s.objects]
                    self.assertIn('H',ids);self.assertIn('R',ids);self.assertIn('HR',ids)
                    self.assertIn('P1',ids);self.assertEqual(len(ids),len(set(ids)))

    def test_component_course_accepts_north_zero_and_signed_angle(self):
        for angle,east in ((0,0),(-90,-100),(360,0)):
            lesson=build_course('math',{'method':'分量','a':100,'b':angle,'c':0})
            self.assertAlmostEqual(lesson.questions[0].answer,east)
        self.assertEqual(build_course('math',{'method':'勾股','a':3,'b':4,'c':0}).status,'unique')


if __name__=='__main__':unittest.main()
