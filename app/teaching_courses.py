"""条件驱动的课程生成；每章的步骤数由真实推导决定。"""
import math
from teaching_math import *
from teaching_models import Lesson, LessonStep, VisualObject, Question

CHAPTERS = [('bearing','航向实验室'),('geometry','八图形解题'),('access','起飞与避让'),
            ('wind','风向实验室'),('math','数学工具'),('units','单位与坐标'),
            ('parameters','任务参数'),('cases','真题分步课')]
SHAPE_KEYS = ['triangle','right','trapezoid','concave','butterfly','hexagon','scan','circle']
SHAPE_NAMES = ['等边三角形','直角三角形','梯形','凹五边形','蝴蝶形','六边形','扫描航线','圆形']
# 字段：键、中文名称、默认值、可选值。所有输入由纯函数再次校验。
FIELDS = {
 'bearing':[('base','基准方向','西',['北','东','南','西']),('toward','偏向方向','南',['北','东','南','西']),('angle','偏角（°）',15,None)],
 'geometry':[('shape','图形','triangle',SHAPE_KEYS),('side','边长 / 周长（m）',100,None),('bearing','首段航向（°）',90,None),('short','梯形短底（m）',200,None),('long','梯形长底（m）',300,None),('angle','梯形底角（°）',60,None),('rows','扫描行数',3,None)],
 'access':[('relation','位置关系','via',['via','midpoint','perpendicular','extension','onroute']),('first_bearing','H→R 航向（°）',315,None),('first_distance','H→R 距离（m）',600,None),('second_bearing','R→P1 航向（°）',135,None),('second_distance','R→P1 距离（m）',500,None),('offset','垂线 / 延长距离（m）',100,None)],
 'wind':[('heading','目标航迹 / 机头（°）',90,None),('wind_from','风的来向（°）',0,None),('advanced','风三角形拓展','基础',['基础','拓展']),('airspeed','空速（m/s）',10,None),('windspeed','风速（m/s）',3,None)],
 'math':[('method','求解条件','SSS',['SSS','SAS','SSA','勾股','分量']),('a','a / 距离（m）',5,None),('b','b / 航向（°）',4,None),('c','c / 已知角（°）',3,None)],
 'units':[('speed','水平速度（km/h）',7.2,None),('distance','水平距离（m）',60,None),('degree','度',120,None),('minute','分',13,None),('second','秒',14,None),('hemisphere','半球','E',['E','W','N','S'])],
 'parameters':[('height','任务高度（m）',90,None),('return_height','返航高度（m）',90,None),('horizontal','水平速度（m/s）',2,None),('vertical','垂直速度（m/s）',2,None),('dwell','停留（s）',3,None),('turn','原题转弯术语','定点转弯',['定点转弯','停止转弯','协调转弯']),('route','执行方式','闭合不循环',['闭合不循环','闭合循环','开放航线']),('loops','循环次数（0 表示未给）',0,None)],
 'cases':[('case','代表题','p2',['p1','p2','p4'])]
}
CHOICE_LABELS = dict(zip(SHAPE_KEYS,SHAPE_NAMES)) | {'via':'H→R→P1','midpoint':'中点','perpendicular':'垂线','extension':'延长线','onroute':'起飞点在航线上','p1':'第 1 页 · 等边三角形','p2':'第 2 页 · 梯形与避让','p4':'第 4 页 · 六边形与高度'}


def defaults(chapter):
    return {k:value for k,_,value,_ in FIELDS[chapter]}


def obj_line(id, a, b, label='', role='task'):
    return VisualObject(id,'line',(a,b),label,role)


def obj_point(id, p, role='task'):
    return VisualObject(id,'point',(p,),id,role)


def step(id,title,explanation,formula='',substitution='',result='',objects=(),**kw):
    return LessonStep(id,title,explanation,formula,substitution,result,tuple(objects),**kw)


def bearing_course(p):
    angle=finite(p['angle'])
    result=quadrantBearingToAzimuth(p['base'],p['toward'],angle)
    bases={'北':0,'东':90,'南':180,'西':270}
    base=bases[p['base']]
    sign='+' if (bases[p['toward']]-base+180)%360-180>0 else '−'
    reverse=reverseBearing(result)
    visual=lambda v:(VisualObject('compass','compass',value=v,label=f'{v:g}°'),)
    steps=[step('base','先确定基准方向','航向以正北为 0°，顺时针增加。',result=f'{p["base"]} = {base}°',objects=visual(base)),
           step('offset','再沿偏向转动','“偏”表示从基准方向向相邻方向转动，不是直接把偏角当成航向。','航向 = 基准 ± 偏角',f'{base}° {sign} {angle:g}°',f'{result:g}°',visual(result),motion='bearing'),
           step('reverse','最后区分正向与反向','“P2 位于 P1 某方向”是 P1→P2；实际走反向时再加 180°。','反向 = (航向 + 180°) mod 360',f'({result:g} + 180) mod 360',f'{reverse:g}°',visual(reverse),motion='bearing')]
    return Lesson('bearing','航向实验室',tuple(steps),p,(Question(f'{p["base"]}偏{p["toward"]}{angle:g}° 的航向是？',result,'°',explanation=f'{base} {sign} {angle:g} = {result:g}'),Question('该方向的反向航向是？',reverse,'°')))


def geometry_course(p):
    result=solvePolygon(p['shape'],p['side'],p['bearing'],short=p['short'],long=p['long'],angle=p['angle'],rows=p['rows'])
    if result.status!='unique':raise ValueError('；'.join(result.warnings))
    shape=result.candidates[0]
    title=SHAPE_NAMES[SHAPE_KEYS.index(p['shape'])]
    warnings=[]
    if p['shape'] in ('concave','butterfly'):
        warnings=['此图是明确尺寸的教学示例；仅凭“五边形/蝴蝶形”名称不能推出任意真题的全部边长。']
    if p['shape']=='circle':
        r=shape['radius']
        circle=VisualObject('circle','circle',(Point(0,0),),f'r={r:.3f}m',value=r)
        steps=[step('radius','周长换半径','圆弧长度与直线弦长不同。','r=L/(2π)',f'{p["side"]:g}/(2π)',f'{r:.4f} m',(circle,)),
               step('tangent','沿圆周的瞬时航向','机头沿切线，径向角与切向航向相差 90°。','切向 = 径向 + 90°',result='北侧点向东：90°',objects=(circle,VisualObject('aircraft','plane',(Point(0,r),),value=90))),
               step('circle_route','完整圆周演示','此为连接顺序示意；进度与匀速时间线性对应。',result=f'周长 {shape["perimeter"]:.3f} m',objects=(circle,),motion='circle',duration=5200)]
        return Lesson('geometry:circle',title,tuple(steps),p,(Question('此圆的半径是多少？',r,'m'),))
    points=shape['points']
    labels=[obj_point(f'P{i+1}',point) for i,point in enumerate(points)]
    outlines=[obj_line(f'edge{i}',a,b,'','muted') for i,(a,b) in enumerate(zip(points,points[1:]+(points[:1] if shape['closed'] else [])))]
    objects=labels+outlines
    steps=[step('known','先读图形与约束','点的连接顺序、闭合状态、边长和角度分别建模；禁止从图片像素量距离。',result=f'{len(points)} 个顶点 · {len(outlines)} 条航段 · '+('闭合' if shape['closed'] else '开放'),objects=objects,warnings=tuple(warnings))]
    if p['shape']=='trapezoid':
        a,b,angle=finite(p['short']),finite(p['long']),finite(p['angle'])
        h=(b-a)*math.tan(math.radians(angle))
        foot=destinationPoint(points[1],shape['headings'][1],b)
        helper=obj_line('helper',points[3],foot,'垂线','helper')
        objects += [helper]
        steps += [step('helper','先补垂线','长底减短底得到直角三角形的一条直角边。','差值=b−a',f'{b:g}−{a:g}',f'{b-a:g} m',objects,highlights=('helper',),motion='line',duration=1800),
                  step('height','再算高度与斜腰','辅助三角形中使用正切与余弦。','h=(b−a)tan α；斜腰=(b−a)/cos α',f'({b:g}−{a:g})tan {angle:g}°',f'高度 {h:.4f} m；斜腰 {(b-a)/math.cos(math.radians(angle)):.4f} m',objects)]
    elif p['shape']=='right':
        steps += [step('pythagoras','核对 3:4:5','37°/53°是课件近似值；内部保持精确计算。','(3k)²+(4k)²=(5k)²',result='锐角 ≈36.8699°、53.1301°',objects=objects)]
    elif p['shape'] in ('triangle','hexagon'):
        n=len(points)
        steps += [step('angles','内角转换为航向转角','正多边形各顶点转向量为 360°/n，不能把内角直接加到航向。','内角=(n−2)×180°/n；转角=360°/n',f'n={n}',f'内角 {180-360/n:g}°；转向 {360/n:g}°',objects)]
    elif p['shape']=='concave':
        steps += [step('concavity','凹顶点的内角大于 180°','凹点转向符号与凸点不同；小夹角不能冒充内角。',result='；'.join(f'P{i+1}: {v:.1f}°' for i,v in enumerate(shape['interior'])),objects=objects)]
    elif p['shape']=='butterfly':
        steps += [step('crossing','交叉处不新增航点','蝴蝶形按有序路线演示，交叉线不是简单多边形。',result='内角和公式不适用于此自交路线',objects=objects)]
    elif p['shape']=='scan':
        steps += [step('open','扫描末段不自动回首点','往返扫描是开放路线，返航需要另列条件。',result=f'{len(outlines)} 条航段；没有首尾闭合边',objects=objects)]
    pairs=list(zip(points,points[1:]+(points[:1] if shape['closed'] else [])))
    for i,(a,b) in enumerate(pairs):
        line=obj_line(f'active{i}',a,b,f'{shape["lengths"][i]:.3f}m · {shape["headings"][i]:.2f}°')
        objects=objects+[line]
        steps.append(step(f'segment{i}',f'推导航段 P{i+1}→P{(i+1)%len(points)+1}','先确定出发点与到达点，再由東/北分量反算航向。','θ=atan2(Δ东, Δ北)',f'Δ东={b.x-a.x:.3f}；Δ北={b.y-a.y:.3f}',f'航向 {shape["headings"][i]:.3f}° · 长度 {shape["lengths"][i]:.3f} m',objects,highlights=(line.id,),motion='line',duration=2000))
    steps.append(step('verify','校验路线','闭合与边长约束在数学坐标中核验；图上缩放不改变计算。',result=f'总航线长度 {shape["perimeter"]:.4f} m',objects=objects))
    return Lesson('geometry:'+p['shape'],title,tuple(steps),p,(Question('首段航向是多少？',shape['headings'][0],'°'),Question('总航线长度是多少？',shape['perimeter'],'m',.1)),warnings=tuple(warnings))


def access_course(p):
    h=Point(0,0)
    r=destinationPoint(h,p['first_bearing'],p['first_distance'])
    end=destinationPoint(r,p['second_bearing'],p['second_distance'])
    relation=p['relation']
    explanation='“位于 R 的某方向”从 R 出发；“P1→R”给出的是反向，实际接入须转换。'
    if relation=='midpoint':
        end=Point((h.x+r.x)/2,(h.y+r.y)/2)
        explanation='中点分别取东、北坐标的算术平均。'
    elif relation=='perpendicular':
        mid=Point(r.x/2,r.y/2)
        end=destinationPoint(mid,normalizeBearing(p['first_bearing']+90),p['offset'])
        explanation='先求中点，再沿原航段右法向偏移指定距离。左侧分支需要另外声明。'
    elif relation=='extension':
        end=destinationPoint(r,p['first_bearing'],p['offset'])
        explanation='“越过 R 延长”沿 H→R 相同方向继续；反向延长须另作声明。'
    elif relation=='onroute':
        end=destinationPoint(h,p['first_bearing'],positive(p['first_distance'])/2)
        explanation='此示例声明起飞点位于航段中点；只说“在航线上”仍不足以唯一定位。'
    direct=distanceBetween(h,end)
    bearing=azimuthBetweenPoints(h,end) if direct>EPS else None
    base=[obj_point('H',h,'takeoff')]
    one=base+[obj_point('R',r,'avoidance'),obj_line('HR',h,r,f'{finite(p["first_distance"]):g}m','avoidance')]
    two=one+[obj_point('P1',end),obj_line('RP1',r,end,'接入任务点')]
    final=two+[obj_line('resultant',h,end,'合成位移','helper')]
    steps=[step('roles','先区分三个角色','H 为起飞点，R 为避让/过渡点，P1 为任务点。角色由题目明示关系决定。',objects=base),
           step('first','定位避让点 R','东分量 = d sin θ，北分量 = d cos θ。','R=H+(d sin θ,d cos θ)',f'd={finite(p["first_distance"]):g}m；θ={normalizeBearing(p["first_bearing"]):g}°',f'R=({r.x:.4f}, {r.y:.4f}) m',one,motion='line',highlights=('HR',),duration=2200),
           step('second','按位置语句定位 P1',explanation,'分量相加 / 按关系求点',result=f'P1=({end.x:.4f}, {end.y:.4f}) m',objects=two,motion='line',highlights=('RP1',),duration=2200),
           step('result','核对合成位移','航线 H→R→P1 的长度，与 H→P1 的直线距离不是同一个量。','距离=√(Δ东²+Δ北²)',result=f'直线 {direct:.4f}m；'+(f'航向 {bearing:.3f}°' if bearing is not None else '两点重合，方向未定义'),objects=final)]
    return Lesson('access:'+relation,'起飞与避让',tuple(steps),p,(Question('H 与 P1 的直线距离是多少？',direct,'m',.1),))


def wind_course(p):
    heading,wind=normalizeBearing(p['heading']),normalizeBearing(p['wind_from'])
    classify=classifyRelativeWind(heading,wind)
    origin=Point(0,0)
    track=obj_line('track',origin,destinationPoint(origin,heading,100),'目标航迹','task')
    source=destinationPoint(origin,wind,90)
    breeze=obj_line('wind',source,origin,'风从这里吹来','wind')
    nose=VisualObject('aircraft','plane',(origin,),value=heading,label='机头')
    steps=[step('track','先看机头 / 航迹','基础来风判断以机头左右为准；无风偏计算时，不虚构修正角。',result=f'航向 {heading:g}°',objects=(track,nose)),
           step('from','再看风的来向','气象风向是来向；实际风矢量的去向须反转 180°。','去向=(来向+180°) mod 360',f'({wind:g}+180) mod 360',f'风去向 {reverseBearing(wind):g}°',(track,breeze,nose),motion='line',highlights=('wind',)),
           step('classify','站在飞机上判断左右','顺时针相对角为右侧，逆时针为左侧；斜侧风另分前、后。',result=classify,objects=(track,breeze,nose))]
    questions=(Question('风矢量的去向航向是？',reverseBearing(wind),'°'),)
    status='unique';warnings=[]
    if p['advanced']=='拓展':
        solution=solveWindTriangle(heading,p['airspeed'],wind,p['windspeed'])
        status=solution.status;warnings=solution.warnings
        if solution.candidates:
            d=solution.candidates[0]
            air=destinationPoint(origin,d['heading'],finite(p['airspeed']))
            ground=destinationPoint(air,reverseBearing(wind),finite(p['windspeed']))
            visuals=(obj_line('air',origin,air,'空速','helper'),obj_line('windvector',air,ground,'风速','wind'),obj_line('ground',origin,ground,'地速'))
            steps.append(step('triangle','拓展：风三角形','此为数学拓展；保持目标航迹时，机头方向与地面航迹可以不同。','空速矢量 + 风矢量 = 地速矢量',result=f'机头 {d["heading"]:.3f}°；地速 {d["groundspeed"]:.3f}m/s；修正 {d["correction"]:.3f}°',objects=visuals))
            questions+=(Question('保持目标航迹时的地速是多少？',d['groundspeed'],'m/s'),)
        else:
            steps.append(step('impossible','检查是否可保持航迹','不满足物理条件时，不绘制伪造的可行航向。',result='；'.join(warnings),objects=(track,breeze)))
    return Lesson('wind','风向实验室',tuple(steps),p,questions,status,tuple(warnings))


def math_course(p):
    method=p['method']
    a=positive(p['a'])
    b=finite(p['b']) if method=='分量' else positive(p['b'])
    c=finite(p['c']) if method in ('分量','勾股') else positive(p['c'])
    if method=='勾股':
        length=math.hypot(a,b)
        solution=solveTriangle(a=a,b=b,c=length)
    elif method=='分量':
        point=destinationPoint(Point(0,0),b,a)
        steps=[step('components','距离拆成东、北分量','航向从北开始；因此东分量用 sin，北分量用 cos。','东=d sin θ；北=d cos θ',f'd={a:g}；θ={b:g}°',f'东 {point.x:.4f}m；北 {point.y:.4f}m',(obj_line('vector',Point(0,0),point),obj_point('P',point)))]
        return Lesson('math','分量分解',tuple(steps),p,(Question('东向分量是多少？',point.x,'m'),))
    else:
        known={'a':a,'b':b,'c':c} if method=='SSS' else {'b':a,'c':b,'A':c} if method=='SAS' else {'a':a,'b':b,'A':c}
        solution=solveTriangle(**known)
    steps=[step('conditions','先检查独立条件','SSS：三条边；SAS：两边和夹角；SSA：两边及非夹角，可出现两个解。',result=f'输入 {method}：{a:g}、{b:g}、{c:g}')]
    for i,d in enumerate(solution.candidates):
        # c 为底边，A 是底边与 b 的夹角。
        points=(Point(0,0),Point(d['c'],0),Point(d['b']*math.cos(math.radians(d['A'])),d['b']*math.sin(math.radians(d['A']))))
        visuals=tuple(obj_line(f'tri{i}-{j}',points[j],points[(j+1)%3],f'{distanceBetween(points[j],points[(j+1)%3]):.3f}m') for j in range(3))
        steps.append(step(f'candidate{i}',f'候选解 {i+1}', '逐候选检查角度和与输入残差。',solution.steps[0],result='；'.join(f'{k}={d[k]:.4f}'+('°' if k.isupper() else 'm') for k in 'abcABC'),objects=visuals))
    states={'unique':'唯一解','multi':'有两个解，须补充位置关系','insufficient':'条件不足','no_solution':'无解'}
    steps.append(step('validation','验证结果','未通过约束的候选不作为答案。',result=states.get(solution.status,solution.status)+'；'+'；'.join(solution.warnings),objects=steps[-1].objects))
    questions=(Question('候选解的个数是多少？',len(solution.candidates),'个'),)
    return Lesson('math','数学工具 · '+method,tuple(steps),p,questions,solution.status,tuple(solution.warnings))


def units_course(p):
    speed=convertSpeed(p['speed'])
    time=travelTime(p['distance'],speed)
    coord=dmsToDecimal(p['degree'],p['minute'],p['second'],p['hemisphere'])
    axis='latitude' if p['hemisphere'] in ('N','S') else 'longitude'
    d,m,s,hem=decimalToDms(coord,axis)
    steps=[step('speed','速度先统一单位','水平速度用于水平航段；不能把垂直速度代入。','m/s = km/h ÷ 3.6',f'{finite(p["speed"]):g}÷3.6',f'{speed:.4f} m/s'),
           step('time','再算距离与时间','速度必须大于零；停留和转弯耗时另计。','t=d/v',f'{finite(p["distance"]):g}/{speed:.4f}',f'{time:.4f} s = {time/60:.4f} min'),
           step('decimal','度分秒转换十进制度','西经、南纬为负；分钟、秒须各小于60。','|坐标|=度+分/60+秒/3600',f'{abs(finite(p["degree"])):g}+{finite(p["minute"]):g}/60+{finite(p["second"]):g}/3600',f'{coord:.8f}°'),
           step('carry','反向转换并检查进位','显示精度与计算精度独立；秒四舍五入到60时向分进位。',result=f'{hem} {d}°{m}′{s:.2f}″；米制位移不能直接加到经纬度上')]
    return Lesson('units','单位与坐标',tuple(steps),p,(Question('换算后的速度是多少？',speed,'m/s'),Question('水平航段用时是多少？',time,'s'),Question('十进制度是多少？',coord,'°',1e-6)))


def parameters_course(p):
    for key in ('height','return_height','dwell'):positive(p[key],True)
    for key in ('horizontal','vertical'):positive(p[key])
    loops=int(finite(p['loops']))
    if loops<0 or loops!=finite(p['loops']):raise ValueError('循环次数须为非负整数')
    rise=abs(finite(p['return_height'])-finite(p['height']))/finite(p['vertical'])
    warnings=[]
    if p['route']=='闭合循环' and not loops:warnings.append('题目未给循环次数，不能自行填写。')
    warnings.append('原题转弯术语原样保留；具体地面站的选项名称须核对，不能直接猜映射。')
    visual=(VisualObject('task-height','altitude',label='任务高度',value=finite(p['height'])),VisualObject('return-height','altitude',label='返航高度',value=finite(p['return_height'])))
    steps=[step('height','高度参照先确认','任务高度、避让高度、返航高度分别设置；山高与航点高度可能使用不同参照。',result=f'任务 {finite(p["height"]):g}m；返航 {finite(p["return_height"]):g}m'),
           step('speed','水平与垂直分开','爬升/下降时间使用高度差和垂直速度。','t垂直=|高度差|/垂直速度',f'|{p["return_height"]}−{p["height"]}|/{p["vertical"]}',f'{rise:.3f}s；水平速度 {p["horizontal"]}m/s',visual,motion='altitude',duration=2200),
           step('turn','转弯与停留逐项映射','停留时间不是转弯时间；参数名称需核对当前地面站。',result=f'{p["turn"]}；停留 {p["dwell"]}s',warnings=tuple(warnings[-1:])),
           step('sequence','核对连接顺序与终止动作','闭合只表示末点与首点连接；是否循环、循环次数、完成后返航是独立条件。',result=f'H→R（若有）→任务点→'+('下一圈' if p['route']=='闭合循环' else '终止 / 按题返航'),warnings=tuple(warnings))]
    return Lesson('parameters','任务参数',tuple(steps),p,(Question('此高度差的垂直运动时间是多少？',rise,'s'),),warnings=tuple(warnings))


def build_course(chapter, parameters=None):
    p=defaults(chapter)
    if parameters:p.update(parameters)
    if chapter=='cases':
        from teaching_cases import case_course
        return case_course(p['case'])
    return {'bearing':bearing_course,'geometry':geometry_course,'access':access_course,'wind':wind_course,'math':math_course,'units':units_course,'parameters':parameters_course}[chapter](p)
