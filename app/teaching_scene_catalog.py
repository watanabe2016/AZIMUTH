"""课件六章与 50 个教学场景；所有几何、公式和画布共享同一数学快照。"""
from dataclasses import dataclass
import math
from teaching_math import (Point,destinationPoint,azimuthBetweenPoints,normalizeBearing,
    reverseBearing,quadrantBearingToAzimuth,dmsToDecimal,positive,finite,solvePolygon)
from teaching_models import Lesson,LessonStep,VisualObject
from teaching_tool_math import wind_from,solve_angle,WIND_OFFSETS

CHAPTERS=(('basics','什么是地面站'),('shapes','图形初理解'),('headings','航向角教学'),
          ('lengths','边长与夹角'),('takeoff','起飞点计算'),('settings','题目条件设置'))

@dataclass(frozen=True)
class Scene:
    number:int
    chapter:str
    title:str
    kind:str
    pages:tuple
    objective:str

_GROUPS=(
('basics',('系统构成','航点逐个生成','航向与距离生成航点','航线组成图形','预规划解题流程'),
 ('system','waypoints','destination','polygon','flow'),(2,3,4)),
('shapes',('等边三角形','三角形外角','等腰直角三角形','旋转后的直角三角形','梯形构建','梯形辅助线','五边形连接顺序','蝴蝶形交叉航线','正六边形','六边形内外角','往返扫描','圆心半径与圆周'),
 ('triangle','exterior','isosceles','rotate','trapezoid','trapezoid_helper','concave','butterfly','hexagon','hex_angles','scan','circle'),(5,6,7)),
('headings',('360° 罗盘','八方位依次点亮','北偏东','东偏北','西偏南','航向加法','跨零航向减法','反向航向','A→B 与 B→A','罗盘与航线联动'),
 ('compass360','directions','north_east','east_north','west_south','add','subtract','reverse','swap','compass_route'),(7,11,12,13)),
('lengths',('三角形内角和','勾股定理','等边边长联动','三角形辅助线','夹角命名识别','顺时针夹角加法','逆时针夹角减法','夹角与反向航向'),
 ('angle_sum','pythagoras','resize_triangle','triangle_helper','angle_name','angle_clockwise','angle_counter','angle_reverse'),(7,8,9,10)),
('takeoff',('起飞点生成首航点','起飞点相对位置','位于、在、到','起飞点与航点重合','度分秒转十进制度','简单避让点定位'),
 ('takeoff_destination','relative','semantics','coincident','dms','avoidance'),(14,15,16,17,18,19)),
('settings',('航线与返航高度','水平速度与时间','垂直速度','转弯与停留','闭合与循环'),
 ('altitude','horizontal','vertical','dwell','loops'),(20,21,22)),
('tools',('航向交互工具','风向关系工具','夹角动态推导','夹角双向求解'),
 ('heading_tool','wind_tool','angle_tool','angle_bidirectional'),()),
)
SCENES=[]
for chapter,titles,kinds,pages in _GROUPS:
    for title,kind in zip(titles,kinds):
        SCENES.append(Scene(len(SCENES)+1,chapter,title,kind,pages,title))
SCENES=tuple(SCENES)

def scene_by_number(number):
    if not 1<=int(number)<=50:raise ValueError('场景编号须在 1～50')
    return SCENES[int(number)-1]

def line(key,a,b,label='',role='task'):
    return VisualObject(key,'line',(a,b),label,role)
def point(key,p,role='task'):
    return VisualObject(key,'point',(p,),key,role)
def text(key,p,label,role='muted'):
    return VisualObject(key,'text',(p,),label,role)
def arc(key,center,heading,sweep,radius=30):
    return VisualObject(key,'arc',(center,destinationPoint(center,heading,radius)),f'{abs(sweep):g}°','helper',sweep)
def compass(value,key='compass',role='task',turn=None):
    # turn 在 label 中承载明确教学旋转量，避免跨零时绕远。
    return VisualObject(key,'compass',label='' if turn is None else str(turn),role=role,value=value)

DEFAULTS={'heading':90.,'angle':45.,'side':100.,'distance':50.,'wind_mode':'正左侧风',
 'angle_name':'H12','known_index':0,'turn_side':'顺时针','target_reverse':False,
 'horizontal':3.,'vertical':2.,'height':90.,'return_height':120.,'dwell':3.,
 'degree':25.,'minute':5.,'second':35.6,'loops':2,
 'avoid_heading':315.,'avoid_distance':600.,'exit_distance':500.}

def defaults(number):
    p=dict(DEFAULTS)
    if number in (32,33,34,35,49,50):p['known_index']=1
    if number in (20,21):p['angle']=30
    if number==22:p['angle']=15
    if number==24:p.update(heading=20,angle=45)
    if number==25:p['heading']=255
    return p

def regular_points(n,side,heading=90):
    """按课件 1→2→3 的北侧构型构造，航向依次减外转角。"""
    result=[Point(0,0)]
    for i in range(n-1):result.append(destinationPoint(result[-1],heading-i*360/n,side))
    return result

def build_scene(number,parameters=None):
    scene=scene_by_number(number);p=defaults(number)
    if parameters:p.update(parameters)
    for key in ('heading','angle','side','distance','horizontal','vertical','height','return_height','dwell','degree','minute','second'):
        p[key]=finite(p[key])
    positive(p['side']);positive(p['distance']);positive(p['horizontal']);positive(p['vertical'])
    positive(p['height'],True);positive(p['return_height'],True);positive(p['dwell'],True)
    p['heading']=normalizeBearing(p['heading'])
    stages=[];warnings=[];origin=Point(0,0)
    def add(title,explanation,objects=(),formula='',result='',motion='reveal',highlights=(),duration=1100,substitution=''):
        stages.append(LessonStep(f'{number:02d}-{len(stages)}',title,explanation,formula,substitution,result,
            tuple(objects),tuple(highlights),duration,motion))
    def path(points,closed=True,labels=None,roles=None):
        objects=[]
        labels=labels or [str(i+1) for i in range(len(points))]
        roles=roles or ['task']*len(points)
        for i,v in enumerate(points):
            objects.append(point(labels[i],v,roles[i]))
            add('定位 '+labels[i],'先确认编号，再按顺序连接。',objects,result=f'航点 {labels[i]}')
        pairs=list(zip(points,points[1:]+(points[:1] if closed else [])))
        for i,(a,b) in enumerate(pairs):
            key=f'edge{i}';heading=azimuthBetweenPoints(a,b);length=math.hypot(b.x-a.x,b.y-a.y)
            objects.append(line(key,a,b,f'{length:g} m · {heading:.1f}°'))
            add(f'连接 {labels[i]}→{labels[(i+1)%len(labels)]}','由方向与距离生成下一点。',objects,
                '东=d sin θ；北=d cos θ',f'{length:.2f} m · {heading:.2f}°','line',(key,))
        return objects
    k=scene.kind
    if k=='system':
        # 这是关系示意图，节点用画布布局坐标排布，不代表实际米制位置。
        locations=[Point(-150,-90),Point(0,90),Point(150,-90)]
        names=['地面控制端','无人机','任务航线'];objects=[]
        for i,(loc,name) in enumerate(zip(locations,names)):
            objects.append(VisualObject(f'node{i}','node',(loc,),name))
            if i:objects.append(line(f'info{i}',locations[i-1],loc,'任务 / 状态','wind'))
            add(name,'地面控制端用于监控、操纵、规划和数据分析。',objects,motion='informationflow',highlights=(f'info{i}',),result=name)
    elif k=='flow':
        names=['理解题目','确定起点','计算航向','生成航点','设置参数','检查任务'];objects=[]
        locations=(Point(-160,85),Point(0,85),Point(160,85),
                   Point(160,-85),Point(0,-85),Point(-160,-85))
        previous=None
        for i,name in enumerate(names):
            loc=locations[i]
            objects.append(VisualObject(str(i),'node',(loc,),name,'wind'))
            if previous is not None:objects.append(line(f'flow{i}',previous,loc,'','muted'))
            add(name,'按顺序完成预规划；课件中的六分钟为原课件描述，实际要求以考试现场为准。',objects,result=f'{i+1}/6 · {name}')
            previous=loc
    elif k in ('waypoints','destination','takeoff_destination'):
        a=destinationPoint(origin,p['heading'],p['distance'])
        b=destinationPoint(a,p['heading']+120,p['distance'])
        if k=='waypoints':path([origin,a,b],False,['P1','P2','P3'])
        else:
            name='H' if k=='takeoff_destination' else 'P1'
            base=[point(name,origin,'takeoff'),line('north',origin,Point(0,p['distance']),'北','muted')]
            add('确认起点与北向','从给定点出发，正北为 0°。',base)
            base.append(line('direction',origin,a,f'{p["heading"]:g}°','helper'))
            add('先确定航向','航向从北顺时针计量。',base,'θ=给定航向',f'{p["heading"]:g}°','direction',('direction',))
            base.extend([line('distance',origin,a,f'{p["distance"]:g} m'),point('1' if name=='H' else 'P2',a)])
            add('沿方向生成航点','屏幕缩放不改变数学距离。',base,'新点=起点+(d sin θ,d cos θ)',f'东 {a.x:.2f} m / 北 {a.y:.2f} m','line',('distance',))
    elif k in ('polygon','triangle','isosceles','trapezoid','concave','butterfly','hexagon','scan'):
        shape={'polygon':'triangle','triangle':'triangle','isosceles':'right'}.get(k,k)
        if k=='isosceles':pts=[origin,Point(p['side'],0),Point(0,p['side'])]
        elif shape in ('triangle','hexagon'):pts=regular_points(3 if shape=='triangle' else 6,p['side'],p['heading'])
        else:pts=solvePolygon(shape,p['side'],p['heading']).candidates[0]['points']
        objects=path(pts,k!='scan')
        if k in ('triangle','hexagon','isosceles'):
            for i,v in enumerate(pts):
                previous=azimuthBetweenPoints(v,pts[(i-1)%len(pts)])
                following=azimuthBetweenPoints(v,pts[(i+1)%len(pts)])
                sweep=(following-previous+180)%360-180
                objects.append(arc(f'angle{i}',v,previous,sweep,p['side']*.2))
                add(f'查看顶点 {i+1} 的内角','角弧从当前顶点的两条射线量取。',objects,result=f'{abs(sweep):g}°',highlights=(f'angle{i}',))
        if k=='butterfly':warnings.append('交叉处不是新增航点；自交路线不能套简单多边形内角和公式。')
        if k=='concave':warnings.append('五边形形状为明确给定的教学示例；名称本身不足以求解任意五边形。')
    elif k in ('exterior','hex_angles','angle_sum'):
        n=6 if k=='hex_angles' else 3
        pts=regular_points(n,p['side'])
        objects=[point(str(i+1),v) for i,v in enumerate(pts)]+[line(f'e{i}',a,pts[(i+1)%n]) for i,a in enumerate(pts)]
        if k=='angle_sum':
            for i,v in enumerate(pts):
                a=azimuthBetweenPoints(v,pts[(i-1)%n]);b=azimuthBetweenPoints(v,pts[(i+1)%n]);sweep=(b-a+180)%360-180
                objects.append(arc(f'a{i}',v,a,sweep,p['side']*.2));add(f'内角 {i+1}','三个内角之和为 180°。',objects,'A+B+C=180°',f'{(i+1)*60}°',highlights=(f'a{i}',))
            # 平移三段扇形至共同中心，最终组成半圆。
            joined=[arc(f'joined{i}',origin,90+i*60,60,60) for i in range(3)]
            add('三个角组成平角','三段 60° 角弧共同组成 180°。',joined,'60°+60°+60°=180°','180°',highlights=tuple(o.id for o in joined))
        else:
            v=pts[1];incoming=azimuthBetweenPoints(pts[0],v);outgoing=azimuthBetweenPoints(v,pts[2])
            interior=180-360/n;turn=(outgoing-incoming+180)%360-180
            add('先量内角','内角由顶点指向相邻两点的射线组成。',objects+[arc('inside',v,reverseBearing(incoming),interior,p['side']*.2)],result=f'内角 {interior:g}°',highlights=('inside',))
            extended=destinationPoint(v,incoming,p['side']*.6);objects+=[line('extend',v,extended,'入航延长线','helper')]
            add('延长入航方向','沿实际进入顶点的方向继续延长。',objects,motion='line',highlights=('extend',))
            add('查看外转角','转弯角是入航方向到出航方向的转量。',objects+[arc('outside',v,incoming,turn,p['side']*.28)],'转角=180°−内角',f'{abs(turn):g}° · 逆时针',highlights=('outside',))
    elif k in ('rotate','resize_triangle'):
        pts=[origin,Point(100,0),Point(0,100)] if k=='rotate' else regular_points(3,100)
        objects=[VisualObject('moving','polygon',tuple(pts),'旋转不改变边长与内角' if k=='rotate' else '三条边同步缩放',value=p['heading'] if k=='rotate' else p['side']/100)]
        add('原始图形','先查看未变化的边长和角度。',[VisualObject('moving','polygon',tuple(pts),'原始图形',value=0 if k=='rotate' else 1)],result='边长与角度由模型计算')
        add('整体旋转' if k=='rotate' else '改变边长','旋转改变绝对航向；等比缩放保持内角。',objects,'旋转矩阵' if k=='rotate' else '各边×相同比例',f'{p["heading"]:g}°' if k=='rotate' else f'边长 {p["side"]:g} m',motion=k,highlights=('moving',),duration=1400)
    elif k in ('trapezoid_helper','triangle_helper','pythagoras'):
        pts=[origin,Point(80,0),Point(0,60)] if k=='pythagoras' else [Point(-100,0),Point(100,0),Point(0,math.sqrt(3)*100)]
        if k=='trapezoid_helper':pts=[Point(0,100*math.sqrt(3)),origin,Point(200,0),Point(300,100*math.sqrt(3))]
        objects=[point(str(i+1),v) for i,v in enumerate(pts)]+[line(f'e{i}',a,pts[(i+1)%len(pts)]) for i,a in enumerate(pts)]
        add('读取已知边','先确定三角形的直角或梯形的平行底边。',objects)
        if k=='pythagoras':
            for i,(a,b) in enumerate(zip(pts,pts[1:]+pts[:1])):
                dx=b.x-a.x;dy=b.y-a.y;c=Point(b.x+dy,b.y-dx);d=Point(a.x+dy,a.y-dx)
                objects.append(VisualObject(f'square{i}','square',(a,b,c,d),f'{math.hypot(dx,dy):g}²','helper'))
                add('边上建立正方形','用正方形面积表示该边长的平方。',objects,'a²+b²=c²','60²+80²=100²',highlights=(f'square{i}',))
            add('核对面积关系','3600+6400=10000；同一比例也得到 3²+4²=5²。',objects,'a²+b²=c²','3:4:5 的锐角约 36.8699° / 53.1301°')
        else:
            a=pts[3] if k=='trapezoid_helper' else pts[2];b=Point(a.x,0)
            objects.append(line('height',a,b,'高 / 垂线','helper'))
            add('生成垂直辅助线','先补出直角三角形，再求边长。',objects,'h=100 tan 60°' if k=='trapezoid_helper' else 'h=√3×a/2',f'h={a.y:.4f} m','line',('height',))
            if k=='trapezoid_helper':add('求斜腰','长短底差为 100 m。',objects,'斜腰=100/cos 60°','200 m')
            else:add('识别三角形比例','高线平分顶角并平分底边，得到 30°、60°、90°。',objects,'对 30° : 对 60° : 斜边 = 1 : √3 : 2','1 : √3 : 2')
    elif k=='circle':
        r=p['side']/math.tau
        add('先确定圆心','半径从圆心指向圆周。',[point('O',origin)])
        objects=[point('O',origin),line('radius',origin,Point(r,0),f'r={r:.2f} m','helper')]
        add('生成半径','圆周长与直线距离不同。',objects,'r=L/(2π)',f'r={r:.4f} m','line',('radius',))
        objects.append(VisualObject('circle','circle',(origin,),f'L={p["side"]:g} m',value=r))
        add('沿圆周生成轨迹','圆周运动采用线性相位，速度不因缓动改变。',objects,'L=2πr',f'{p["side"]:g} m','circle',('circle',),duration=4000)
    elif k in ('compass360','directions','north_east','east_north','west_south','add','subtract','reverse','swap','compass_route','heading_tool'):
        angle=p['angle'];heading=p['heading']
        if k=='compass360':
            add('正北为零','航空航向以正北为 0°，顺时针增加。',[compass(0)],result='0°')
            add('顺时针完整一圈','360° 与 0° 表示同一个方向。',[compass(360,turn=360)],'θ mod 360','360° ≡ 0°','bearing',duration=3400)
        elif k=='directions':
            for value,name in zip(range(0,360,45),('N 北','NE 东北','E 东','SE 东南','S 南','SW 西南','W 西','NW 西北')):
                add(name,'八方位每次相差 45°。',[compass(value)],result=f'{name} = {value}°',motion='bearing')
        else:
            if k in ('north_east','east_north','west_south'):
                base,toward={'north_east':('北','东'),'east_north':('东','北'),'west_south':('西','南')}[k]
                start={'北':0,'东':90,'西':270}[base];target=quadrantBearingToAzimuth(base,toward,angle)
                delta=(target-start+180)%360-180;formula=f'{start}° {"+" if delta>=0 else "−"} {angle:g}°'
            elif k in ('reverse','swap'):start=heading;delta=180;target=reverseBearing(start);formula=f'({start:g}°+180°) mod 360'
            elif k=='subtract':start=heading;delta=-angle;target=normalizeBearing(start+delta);formula=f'({start:g}°−{angle:g}°) mod 360'
            elif k=='add':start=heading;delta=angle;target=normalizeBearing(start+delta);formula=f'({start:g}°+{angle:g}°) mod 360'
            else:start=0;target=heading;delta=(target-start+180)%360-180;formula='正北 0°，顺时针航向'
            add('确定参考方向','先看方向，再看角度；跨零采用明确转向。',[compass(start)],result=f'{start:g}°')
            objects=[compass(target,turn=delta)]
            if k=='compass_route':objects.extend([line('map',Point(-100,-100),destinationPoint(Point(-100,-100),target,80),'航线同步')])
            if k=='swap':objects.extend([point('A',origin),point('B',destinationPoint(origin,start,100)),line('AB',destinationPoint(origin,start,100),origin,'B→A')])
            add('转向与计算同步','旋转中显示当前角度，终点显示计算结果。',objects,formula,f'{target:g}°','bearing',duration=1400)
            if k=='heading_tool':add('查看反向','交换起终点后航向差 180°。',[compass(reverseBearing(target),turn=180)],'(θ+180°) mod 360',f'{reverseBearing(target):g}°','bearing')
    elif k in ('angle_name','angle_clockwise','angle_counter','angle_reverse','angle_tool','angle_bidirectional'):
        name=p['angle_name'];from teaching_tool_math import parse_angle
        parsed=parse_angle(name);known_index=int(p['known_index'])
        if known_index not in (0,1,2,3):raise ValueError('请选择夹角射线或其反向航段')
        known=parsed['rays'][known_index%2]
        if known_index>=2:known=known[::-1]
        side=None if p['turn_side']=='未指定（两解）' else p['turn_side']
        if k=='angle_clockwise':side='顺时针'
        if k=='angle_counter':side='逆时针'
        parsed,branches=solve_angle(name,known,p['heading'],p['angle'],side,p['target_reverse'])
        reference=branches[0]['reference'];vertex=origin
        endpoint=destinationPoint(vertex,reference,100)
        labels=[parsed['vertex'],branches[0]['known_ray'][1],branches[0]['target_ray'][1]]
        chosen=branches[0];other=destinationPoint(vertex,chosen['target'],100)
        skeleton=[point(labels[0],vertex),point(labels[1],endpoint),point(labels[2],other),line('base',endpoint,other,'','muted')]
        add('生成三点图形','这是角关系示意；未给边长时不虚构距离。',skeleton)
        add('中间字是顶点',f'∠{name} 的顶点为 {parsed["vertex"]}。',skeleton,result=f'顶点 {parsed["vertex"]}')
        known_label='→'.join(chosen['known_ray'])
        objects=skeleton+[line('known',vertex,endpoint,known_label),line('unknown',vertex,other,'→'.join(chosen['target_ray']))]
        add('高亮两条射线','两条射线均从角的顶点出发。',objects,result='；'.join('→'.join(r) for r in parsed['rays']),motion='line',highlights=('known','unknown'))
        objects.append(line('north',vertex,Point(0,135),'北向参考','muted'))
        add('显示已知航向','航向角以北向参考线顺时针计量。',objects,'θ已知=给定航向',f'{reference:g}°')
        branch_visuals=[]
        for j,b in enumerate(branches):
            endpoint2=destinationPoint(vertex,b['target'],100)
            ray=line(f'branch{j}',vertex,endpoint2,'→'.join(b['target_ray']),'wind')
            angleobj=arc(f'arc{j}',vertex,reference,b['sweep'],42+j*12)
            branch_base=[point(labels[0],vertex),point(labels[1],endpoint),point(labels[2],endpoint2),
                         line('base',endpoint,endpoint2,'','muted'),line('known',vertex,endpoint,known_label),
                         line('north',vertex,Point(0,135),'北向参考','muted')]
            shown=branch_base+[ray,angleobj]
            branch_visuals.extend([ray,angleobj,point(labels[2]+'（'+b['side']+'）',endpoint2,'wind')])
            add(b['side']+'推导','夹角所在侧决定加法或减法。',shown,
                f'({reference:g}° {"+" if b["sweep"]>0 else "−"} {p["angle"]:g}°) mod 360',f'{b["target"]:g}°',highlights=(ray.id,angleobj.id))
            if k in ('angle_reverse','angle_bidirectional','angle_tool') or p['target_reverse']:
                rev=line(f'reverse{j}',endpoint2,vertex,'→'.join(b['target_ray'][::-1]),'wind')
                add('翻转方向求反向','起点与终点交换，需要再加 180°。',shown+[rev],f'({b["target"]:g}°+180°) mod 360',f'{b["reverse"]:g}°','line',(rev.id,))
        if len(branches)==2:
            warnings.append('未给夹角所在侧，存在顺时针、逆时针两种解；不能猜出唯一答案。')
            add('并列核对两种解','同一已知射线两侧都有满足夹角的目标射线。',
                [point(labels[0],vertex),point(labels[1],endpoint),line('known',vertex,endpoint,'已知射线')]+branch_visuals,
                'θ未知=(θ已知±夹角) mod 360','；'.join(f'{b["side"]} {b["result"]:g}°' for b in branches))
        # 命名课、单向运算、反向课各有独立目标，避免重复整套工具推导凑数。
        if k=='angle_name':
            stages=stages[:3]
            add('按角名定位夹角','中间字为顶点，首尾两个字分别决定两条射线。',
                skeleton+[line('known',vertex,endpoint,known_label),line('unknown',vertex,other,'→'.join(chosen['target_ray'])),arc('named',vertex,reference,chosen['sweep'],42)],
                f'∠{name}：顶点 {parsed["vertex"]}',f'{p["angle"]:g}°',highlights=('named',))
        elif k in ('angle_clockwise','angle_counter'):
            stages=[];delta=chosen['sweep']
            add('先标已知航向','从北向参考线量取已知方向。',[compass(reference)],result=f'{reference:g}°')
            add('顺时针加角' if delta>0 else '逆时针减角','正北为零，顺时针角度增加，逆时针角度减小。',
                [compass(chosen['target'],turn=delta)],f'{reference:g}° {"+" if delta>0 else "−"} {p["angle"]:g}°',f'{chosen["target"]:g}°','bearing',duration=1400)
            add('与航线射线核对','把罗盘结果映射回同一顶点的射线。',shown,result=f'{chosen["target"]:g}°')
        elif k=='angle_reverse':
            stages=[stages[3],stages[4],stages[5]]
        if k in ('angle_tool','angle_bidirectional'):
            # 工具将题干解析、北向参考、求解和反向分成十个可停留的教学节点。
            stages=[]
            triangle=[line('outline1',vertex,endpoint,'','muted'),line('outline2',endpoint,other,'','muted'),line('outline3',other,vertex,'','muted')]
            add('生成关系三角形','此图只表达方向与夹角；未给边长时不推定实际距离。',triangle,motion='line',highlights=('outline1','outline2','outline3'))
            add('显示航点名称','按题目角名对应三个航点。',triangle+skeleton[:3])
            given=arc('given',vertex,reference,chosen['sweep'],42)
            add('标记题目给定角',f'∠{name} = {p["angle"]:g}°。',triangle+skeleton[:3]+[given],result=f'{p["angle"]:g}°',highlights=('given',))
            highlighted=[point(labels[0],vertex,'wind'),point(labels[1],endpoint),point(labels[2],other)]
            add('高亮角的顶点',f'中间字 {parsed["vertex"]} 是顶点；首尾字决定两条射线。',triangle+highlighted,result=f'顶点 {parsed["vertex"]}',highlights=(labels[0],))
            rays=[line('known',vertex,endpoint,known_label),line('unknown',vertex,other,'→'.join(chosen['target_ray']))]
            add('高亮两条射线','两条射线从同一个顶点出发。',highlighted+rays,result='；'.join('→'.join(r) for r in parsed['rays']),motion='line',highlights=('known','unknown'))
            known_formula=f'({p["heading"]:g}°+180°) mod 360' if known_index>=2 else '使用题目给定航向'
            add('显示已知航向','若给定航段指向顶点，先取反向，才能作为角的参考射线。',highlighted+rays,known_formula,f'{known_label} = {reference:g}°')
            north=line('north',vertex,Point(0,135),'北向参考','muted')
            add('生成北向参考线','正北为 0°，顺时针航向增加。',highlighted+rays+[north],motion='line',highlights=('north',))
            for j,b in enumerate(branches):
                endpoint2=destinationPoint(vertex,b['target'],100)
                ray=line(f'branch{j}',vertex,endpoint2,'→'.join(b['target_ray']),'wind')
                angleobj=arc(f'arc{j}',vertex,reference,b['sweep'],42+j*12)
                shown=[point(labels[0],vertex,'wind'),point(labels[1],endpoint),point(labels[2],endpoint2),line('base',endpoint,endpoint2,'','muted'),rays[0],north,ray,angleobj]
                add('演示'+b['side']+'加减角度','圆弧、射线和航点同步旋转；同一帧使用同一角度。',shown,
                    f'({reference:g}° {"+" if b["sweep"]>0 else "−"} {p["angle"]:g}°) mod 360',f'{b["target"]:g}°',highlights=(ray.id,angleobj.id),duration=1400)
                add('得出未知航向','先确认出发点与到达点，再读取结果。',shown,result=f'{"→".join(b["target_ray"])} = {b["target"]:g}°')
                reverse=line(f'reverse{j}',endpoint2,vertex,'→'.join(b['target_ray'][::-1]),'wind')
                add('翻转方向计算反向','交换起终点之后加 180°，并归一化至 0～360°。',shown+[reverse],
                    f'({b["target"]:g}°+180°) mod 360',f'{"→".join(b["target_ray"][::-1])} = {b["reverse"]:g}°','line',(reverse.id,))
            if len(branches)==2:
                add('并列核对两种解','条件不足时保留两条合法目标射线，不猜唯一答案。',
                    [point(labels[0],vertex),point(labels[1],endpoint),rays[0]]+branch_visuals,
                    'θ未知=(θ已知±夹角) mod 360','；'.join(f'{b["side"]} {b["result"]:g}°' for b in branches))
        if k=='angle_bidirectional':
            end=chosen['target'];rev=chosen['reverse']
            add('罗盘核对两个方向','两种方向共用一条直线，箭头和航向均相反。',
                [compass(end)],'正向 θ',f'{end:g}°')
            add('完整反向验证','反向需要把方向明确翻转 180°，不是负号替换。',
                [compass(rev,turn=180)],'(θ+180°) mod 360',f'{rev:g}°','bearing',duration=1400)
    elif k=='wind_tool':
        heading=p['heading'];wind=wind_from(heading,p['wind_mode']);to=reverseBearing(wind)
        add('固定飞机航向','左右以沿航向前进时的机体为准。',[compass(heading)],result=f'航向 {heading:g}°')
        objects=[compass(heading),VisualObject('wind','windcompass',label=p['wind_mode'],role='wind',value=wind)]
        add('旋转到气象来向','气象风向表示风从哪里来。飞机航向保持不变。',objects,f'WindFrom=({heading:g}°{WIND_OFFSETS[p["wind_mode"]]:+g}°) mod 360',f'来向 {wind:g}°','wind',('wind',),duration=1400)
        add('显示实际吹向','风的运动方向与来向相反。',objects,'WindTo=(WindFrom+180°) mod 360',f'吹向 {to:g}°')
    elif k in ('relative','semantics','coincident','avoidance'):
        if k=='coincident':
            add('起飞点与指定航点重合','同点之间没有确定航向。',[point('H=1',origin,'takeoff')],result='同坐标；从下一航段开始')
            path([origin,Point(100,0),Point(100,80)],False,['H=1','2','3'])
        elif k=='avoidance':
            heading=normalizeBearing(finite(p['avoid_heading']));distance=positive(p['avoid_distance']);exit_distance=positive(p['exit_distance'])
            r=destinationPoint(origin,heading,distance);end=destinationPoint(r,reverseBearing(heading),exit_distance)
            straight=abs(distance-exit_distance);direction=heading if distance>=exit_distance else reverseBearing(heading)
            path([origin,r,end],False,['H','1（避让 / 过渡）','2（任务首点）'],['takeoff','avoidance','task'])
            add('核对位移和实际航线','题干涉及避让点、过渡点或空域限制时，本示例将 1 点作为避让 / 过渡点。',
                [point('H',origin,'takeoff'),point('1',r,'avoidance'),point('2',end),line('result',origin,end,f'{direction:g}° · {straight:g} m','helper')],
                f'{distance:g} m − {exit_distance:g} m = {distance-exit_distance:g} m',f'直线距离 {straight:g} m；经避让点航程 {distance+exit_distance:g} m','line',('result',))
        else:
            h=Point(0,-p['distance']);one=origin
            objects=[point('1',one),point('H',h,'takeoff')]
            add('选定参考点','“H 位于 1 点南方”以 1 点为参考。',objects,result=f'H 在 1 点南方 {p["distance"]:g} m')
            add('从参考点指向被描述点','位于 / 在描述位置；方向箭头从参考对象出发。',objects+[line('relative',one,h,'1→H：180°')],result='1→H = 180°',motion='line',highlights=('relative',))
            add('到表示有向航段','“H 到 1”直接指定出发 H、到达 1；它与 1→H 相反。',objects+[line('travel',h,one,'H→1：0°')],'反向=(180°+180°) mod 360','H→1 = 0°','line',('travel',))
            if k=='semantics':add('替换描述，先识别起终点','“H 到 3 为 180°”表示 3 在 H 南方；不是 H 在 3 南方。',
                [point('H',origin,'takeoff'),point('3',Point(0,-p['distance'])),line('H3',origin,Point(0,-p['distance']),'H→3：180°')],result='先确定参考对象，避免颠倒方向',motion='line',highlights=('H3',))
    elif k=='dms':
        value=dmsToDecimal(p['degree'],p['minute'],p['second'])
        values=[p['degree'],p['minute']/60,p['second']/3600];objects=[]
        for i,(name,v) in enumerate(zip(('度','分 ÷ 60','秒 ÷ 3600'),values)):
            objects.append(VisualObject(f'token{i}','token',(Point((i-1)*95,0),),f'{name}\n{v:.6f}°','helper'))
            add('拆解 '+name,'度、分、秒先分别换成度，再相加。',objects,'DD=度+分/60+秒/3600',f'累计 {sum(values[:i+1]):.8f}°',highlights=(f'token{i}',))
        add('合并为十进制度','经纬度不是米制位移；西经、南纬使用负号。',objects,'DD=度+分/60+秒/3600',f'{value:.8f}°')
    elif k in ('altitude','vertical'):
        h=p['height'];ret=p['return_height'];time=abs(ret-h)/p['vertical']
        bars=[VisualObject('height','altitude',label='任务高度',value=h),VisualObject('return','altitude',label='返航高度',value=ret)]
        add('确认高度参照','任务高度与返航高度分别设置。',bars,result=f'{h:g} m / {ret:g} m')
        add('变化高度与时间同步','竖直速度用于爬升 / 下降，不能代入水平距离。',bars,'t=|高度差|/垂直速度',f'|{ret:g}−{h:g}|/{p["vertical"]:g} = {time:.2f} s','altitude',('return',),duration=2000)
        if k=='altitude':
            warnings.append('原 PPT 第21页山高100m与60/80m航点高度存在参照歧义；此处不推定可安全越山。')
            add('核查山高条件','山高与航点高度必须使用同一参照，才能判断净空。',bars,result='原题高度参照待核实')
    elif k=='horizontal':
        d=p['distance'];v=p['horizontal']
        tracks=[line('fast',Point(0,35),Point(d,35),f'{v:g} m/s'),line('slow',Point(0,-35),Point(d,-35),f'{v/2:g} m/s','helper')]
        add('相同距离、不同速度','比较速度快慢时保持两条航线长度相同。',tracks,result=f'距离 {d:g} m')
        add('时间线性推进','上轨到达用时是下轨的一半；运动采用真实时间比例。',tracks,'t=d/v',f'{d/v:.2f} s / {d/(v/2):.2f} s','speed_compare',('fast','slow'),duration=4000)
        add('课件单位换算','10.8 km/h ÷ 3.6 = 3 m/s；7.2 km/h ÷ 3.6 = 2 m/s。',tracks,'m/s=km/h÷3.6','10分钟=600秒，距离=速度×600')
    elif k=='dwell':
        pts=[Point(-100,0),origin,Point(50,80)];objects=[point(str(i+1),a) for i,a in enumerate(pts)]+[line('in',pts[0],pts[1]),line('out',pts[1],pts[2])]
        add('通过航点','仅示意连接顺序，不作为真实飞行转弯半径。',objects,motion='through',duration=3000)
        add('停止与指定停留','停留时间独立于转弯模式；演示中的点会在 2 点停止。',objects,'停留时间=题目给定',f'{p["dwell"]:g} s','dwell',duration=4000)
        warnings.append('转弯术语以原课件和实际地面站选项定义为准；示意不模拟真实动力学。')
    elif k=='loops':
        pts=regular_points(3,100);objects=[line(f'loop{i}',a,pts[(i+1)%3]) for i,a in enumerate(pts)]
        add('先看闭合','末点连接首点，只说明路线闭合。',objects,result='闭合不等于循环',motion='closed_once',duration=3600)
        loops=int(p['loops'])
        if loops!=p['loops'] or not 1<=loops<=10:raise ValueError('示例循环次数须为1～10的整数')
        add('再看循环次数','循环是重复执行同一条闭合路线；次数须题目明确给出。',objects,'总航程=单圈长度×圈数',f'{loops} 圈','loop_route',duration=3600*loops)
    else:raise ValueError('未定义的教学场景')
    source=('PPT 第 '+ '、'.join(map(str,scene.pages))+' 页') if scene.pages else '教学交互工具'
    if number==41:source='避让点教学示例；参考题卡位置关系'
    return Lesson(f'scene:{number:02d}',scene.title,tuple(stages),p,warnings=tuple(warnings),source=source,version=3)
