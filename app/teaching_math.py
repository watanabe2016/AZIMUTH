"""教学专用纯数学；米制东/北坐标，正北零度、顺时针。不写入判分数据。"""
from dataclasses import dataclass, field
import math

EPS = 1e-9


def finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('请输入有限数值')
    return value


def positive(value, zero=False):
    value = finite(value)
    if value < 0 or (not zero and value == 0):
        raise ValueError('数值必须大于零' if not zero else '数值不能小于零')
    return value


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def __post_init__(self):
        finite(self.x)
        finite(self.y)


@dataclass
class Solution:
    status: str
    candidates: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    residual: float = 0.0


def normalizeBearing(angle):
    return finite(angle) % 360


def reverseBearing(angle):
    return normalizeBearing(finite(angle) + 180)


def quadrantBearingToAzimuth(base, toward, angle):
    names = {'北': 0, '东': 90, '南': 180, '西': 270,
             'N': 0, 'E': 90, 'S': 180, 'W': 270}
    if base not in names or toward not in names:
        raise ValueError('基准与偏向须为北、东、南、西')
    a, b = names[base], names[toward]
    turn = (b - a + 180) % 360 - 180
    if abs(turn) != 90:
        raise ValueError('请选择相邻的基准与偏向')
    angle = finite(angle)
    if not 0 <= angle <= 90:
        raise ValueError('偏角须在 0～90° 之间')
    return normalizeBearing(a + math.copysign(angle, turn))


def destinationPoint(origin, bearing, distance):
    distance = positive(distance, True)
    angle = math.radians(normalizeBearing(bearing))
    return Point(origin.x + distance * math.sin(angle),
                 origin.y + distance * math.cos(angle))


def distanceBetween(a, b):
    return math.hypot(b.x - a.x, b.y - a.y)


def azimuthBetweenPoints(a, b):
    if distanceBetween(a, b) < EPS:
        raise ValueError('同一点之间没有确定航向')
    return normalizeBearing(math.degrees(math.atan2(b.x-a.x, b.y-a.y)))


def interiorToExteriorAngle(interior, orientation=1):
    interior = finite(interior)
    if not 0 < interior < 360 or interior == 180:
        raise ValueError('内角必须在 0～360° 内且不能退化为直线')
    if orientation not in (-1, 1):
        raise ValueError('转向方向须为 +1 或 -1')
    return orientation * (180 - interior)


def solveTriangle(**known):
    """边 a/b/c 对角 A/B/C，支持 SSS、SAS、ASA/AAS 与 SSA 双解。"""
    if set(known) - set('abcABC'):
        raise ValueError('未知三角形条件')
    values = {k: positive(v) for k, v in known.items() if v is not None}
    if any(v >= 180 for k, v in values.items() if k.isupper()):
        return Solution('no_solution', warnings=['三角形内角须小于 180°'])
    sides = {k: v for k, v in values.items() if k.islower()}
    angles = {k: v for k, v in values.items() if k.isupper()}
    candidates = []
    formula = ''
    if len(sides) == 3:
        a, b, c = (sides[k] for k in 'abc')
        if min(a+b-c, a+c-b, b+c-a) <= EPS:
            return Solution('no_solution', warnings=['不满足严格三角不等式'])
        d = dict(sides)
        for key in 'ABC':
            opposite = d[key.lower()]
            others = [d[k] for k in 'abc' if k != key.lower()]
            d[key] = math.degrees(math.acos(max(-1, min(1, (sum(v*v for v in others)-opposite**2)/(2*others[0]*others[1])))))
        candidates.append(d)
        formula = '余弦定理：cos A = (b²+c²−a²)/(2bc)'
    elif len(angles) >= 2 and sides:
        d = dict(values)
        if len(angles) == 2:
            missing = next(k for k in 'ABC' if k not in angles)
            d[missing] = 180 - sum(angles.values())
        if min(d[k] for k in 'ABC') <= EPS or abs(sum(d[k] for k in 'ABC')-180) > 1e-6:
            return Solution('no_solution', warnings=['内角和必须为 180°'])
        key = next(iter(sides))
        ratio = d[key] / math.sin(math.radians(d[key.upper()]))
        for k in 'abc':
            d[k] = ratio * math.sin(math.radians(d[k.upper()]))
        candidates.append(d)
        formula = '正弦定理：a/sin A = b/sin B = c/sin C'
    elif len(sides) >= 2 and angles:
        key = next(iter(angles))
        other = [k for k in 'abc' if k != key.lower()]
        if all(k in sides for k in other):
            d = dict(values)
            d[key.lower()] = math.sqrt(max(0, sum(sides[k]**2 for k in other)-2*sides[other[0]]*sides[other[1]]*math.cos(math.radians(d[key]))))
            result = solveTriangle(**{k:d[k] for k in 'abc'})
            candidates = result.candidates
            formula = '余弦定理：a² = b²+c²−2bc cos A'
        elif key.lower() in sides:
            other_side = next(k for k in sides if k != key.lower())
            sine = sides[other_side] * math.sin(math.radians(values[key])) / sides[key.lower()]
            if sine <= 1 + EPS:
                first = math.degrees(math.asin(min(1, sine)))
                for angle in sorted(set((round(first, 12), round(180-first, 12)))):
                    third = 180-values[key]-angle
                    if third > EPS:
                        remaining = next(k for k in 'ABC' if k not in (key, other_side.upper()))
                        result = solveTriangle(**{key:values[key], other_side.upper():angle, remaining:third, key.lower():sides[key.lower()]})
                        candidates += result.candidates
            formula = 'SSA：sin B = b sin A/a；检查 B 与 180°−B 两个分支'
    if not candidates:
        enough = len(sides) >= 2 and bool(angles)
        return Solution('no_solution' if enough else 'insufficient', warnings=['条件无解' if enough else '至少需要三个独立条件，其中一个为边长'])
    accepted = []
    residual = 0.0
    for d in candidates:
        error = max(abs(d[k]-v)/max(1, abs(v)) for k,v in values.items())
        if error <= 1e-7:
            accepted.append(d)
            residual = max(residual, error)
    return Solution('unique' if len(accepted)==1 else 'multi' if accepted else 'no_solution', accepted, [formula], residual=residual)


def polygonArea(points):
    return sum(a.x*b.y-b.x*a.y for a,b in zip(points, points[1:]+points[:1]))/2


def solvePolygon(kind, side=100, bearing=90, **options):
    """精确构造声明的图形；自交路线、开放扫描与圆形独立处理。"""
    side = positive(side)
    if kind in ('triangle', 'hexagon'):
        n = 3 if kind == 'triangle' else 6
        points = [Point(0, 0)]
        heading = normalizeBearing(bearing)
        for i in range(n-1):
            points.append(destinationPoint(points[-1], heading+i*360/n, side))
    elif kind == 'right':
        # 3:4:5 精确比例，角度仅在显示时取近似。
        points = [Point(0, 0), Point(side*4/5, 0), Point(0, side*3/5)]
    elif kind == 'trapezoid':
        a = positive(options.get('short', side))
        b = positive(options.get('long', side*1.5))
        angle = finite(options.get('angle', 60))
        if b <= a or not 0 < angle < 90:
            return Solution('no_solution', warnings=['长底必须大于短底，底角须在 0～90° 内'])
        h = (b-a)*math.tan(math.radians(angle))
        points = [Point(0,h), Point(0,0), Point(a,0), Point(b,h)]
    elif kind == 'concave':
        points = [Point(0,0),Point(0,side),Point(side,side),Point(side*.5,side*.5),Point(side,0)]
    elif kind == 'butterfly':
        points = [Point(0,0),Point(side,side),Point(side,0),Point(0,side)]
    elif kind == 'scan':
        rows = int(options.get('rows', 3))
        if not 2 <= rows <= 10:
            raise ValueError('扫描行数须在 2～10 之间')
        points = []
        for i in range(rows):
            y = i*side/(rows-1)
            points += [Point(0 if i%2==0 else side,y),Point(side if i%2==0 else 0,y)]
    elif kind == 'circle':
        radius = side/(2*math.pi)
        return Solution('unique',[{'kind':kind,'radius':radius,'perimeter':side,'points':[], 'closed':True}],['圆周长 L=2πr；航向取切线，不取径向'])
    else:
        return Solution('insufficient',warnings=['请声明图形类型与约束'])
    if kind not in ('triangle','hexagon'):
        # 将默认向东的首段旋转到用户声明的航向。
        first = azimuthBetweenPoints(points[0],points[1])
        delta = math.radians(normalizeBearing(bearing)-first)
        points = [Point(p.x*math.cos(delta)+p.y*math.sin(delta),-p.x*math.sin(delta)+p.y*math.cos(delta)) for p in points]
    closed = kind != 'scan'
    pairs = list(zip(points, points[1:]+(points[:1] if closed else [])))
    lengths = [distanceBetween(a,b) for a,b in pairs]
    headings = [azimuthBetweenPoints(a,b) for a,b in pairs]
    turns = [(headings[(i+1)%len(headings)]-h+180)%360-180 for i,h in enumerate(headings)] if closed else []
    interior = []
    if kind != 'butterfly' and closed:
        orientation = -1 if polygonArea(points)>0 else 1
        interior = [180-orientation*turns[(i-1)%len(turns)] for i in range(len(points))]
    return Solution('unique',[{'kind':kind,'points':points,'lengths':lengths,'headings':headings,'turns':turns,'interior':interior,'closed':closed,'perimeter':sum(lengths)}],['东/北坐标精确构造；屏幕缩放不参与计算'])


def convertSpeed(value, source='km/h', target='m/s'):
    factors = {'m/s':1,'km/h':1/3.6}
    if source not in factors or target not in factors:
        raise ValueError('速度单位仅支持 m/s 与 km/h')
    return positive(value, True)*factors[source]/factors[target]


def convertDistance(value, source='m', target='km'):
    factors = {'m':1,'km':1000}
    if source not in factors or target not in factors:
        raise ValueError('距离单位仅支持 m 与 km')
    return positive(value, True)*factors[source]/factors[target]


def travelTime(distance, speed):
    return positive(distance,True)/positive(speed)


def convertTime(value,source='s',target='min'):
    factors={'s':1,'min':60,'h':3600}
    if source not in factors or target not in factors:raise ValueError('时间单位仅支持 s、min、h')
    return positive(value,True)*factors[source]/factors[target]


def dmsToDecimal(degrees, minutes, seconds, hemisphere='E'):
    d,m,s = finite(degrees), finite(minutes), finite(seconds)
    if hemisphere not in ('E','W','N','S') or not 0<=m<60 or not 0<=s<60:
        raise ValueError('请检查半球与分、秒范围')
    if d < 0 and hemisphere in ('E','N'):
        raise ValueError('负号与东/北半球冲突')
    magnitude = abs(d)+m/60+s/3600
    bound = 180 if hemisphere in ('E','W') else 90
    if magnitude > bound:
        raise ValueError('经纬度超出范围')
    return magnitude*(-1 if hemisphere in ('W','S') else 1)


def decimalToDms(value, axis='longitude', precision=2):
    value = finite(value)
    if axis not in ('longitude','latitude') or abs(value) > (180 if axis=='longitude' else 90):
        raise ValueError('经纬度超出范围')
    precision = int(precision)
    if not 0 <= precision <= 8:
        raise ValueError('显示精度须在 0～8 位之间')
    total = round(abs(value)*3600,precision)
    degrees = int(total//3600)
    minutes = int((total-degrees*3600)//60)
    seconds = round(total-degrees*3600-minutes*60, precision)
    hemisphere = ('W' if value<0 else 'E') if axis=='longitude' else ('S' if value<0 else 'N')
    return degrees, minutes, seconds, hemisphere


def classifyRelativeWind(heading, wind_from):
    delta = (normalizeBearing(wind_from)-normalizeBearing(heading)+180)%360-180
    if abs(delta)<EPS:return '迎风'
    if abs(abs(delta)-180)<EPS:return '顺风'
    if abs(delta+90)<EPS:return '正左侧风'
    if abs(delta-90)<EPS:return '正右侧风'
    return ('左' if delta<0 else '右')+('前侧风' if abs(delta)<90 else '后侧风')


def solveWindTriangle(track, airspeed, wind_from, windspeed):
    airspeed, windspeed = positive(airspeed), positive(windspeed,True)
    delta = math.radians(reverseBearing(wind_from)-normalizeBearing(track))
    cross = windspeed*math.sin(delta)
    along = windspeed*math.cos(delta)
    if abs(cross)>airspeed+EPS:
        return Solution('no_solution',warnings=['侧风分量超过空速，无法保持目标航迹'])
    correction = -math.degrees(math.asin(max(-1,min(1,cross/airspeed))))
    ground = math.sqrt(max(0,airspeed**2-cross**2))+along
    if ground <= EPS:return Solution('no_solution',warnings=['沿目标航迹的地速不为正'])
    return Solution('unique',[{'heading':normalizeBearing(track+correction),'groundspeed':ground,'correction':correction}],['空速矢量 + 风的去向矢量 = 地速矢量'])
