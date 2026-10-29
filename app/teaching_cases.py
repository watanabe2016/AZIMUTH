"""已逐页核验的三道代表题。复核条件保留，不升级为未经验证的标准答案。"""
from dataclasses import replace
from teaching_courses import defaults, geometry_course, access_course, step
from teaching_models import Lesson, Question, VisualObject
from teaching_math import *

CASES = {
 'p1':{'page':1,'name':'5.26 山西02 · 等边三角形','conditions':['三边60m；H→P2=60m；∠32H=25°','高度90m；水平7.2km/h；垂直2m/s','定点转弯；停留3s；闭合不循环','P1：E80.0877°，N32.5077°'], 'warnings':['原图 H 在三角形内部，与 H→P2=边长60m及非沿边夹角不一致。不得按图的像素位置生成 H 坐标。','SE 罗盘标记与页面坐标朝向需复核；不能假定图片上方必为正北。']},
 'p2':{'page':2,'name':'5.26 湖北01 · 梯形与避让','conditions':['P2P3=200m；P1P4=300m；∠1=∠2=90°；∠4=60°','H→R：NW600m；R→P1：SE500m；R高度330m','P2 位于 P3 北偏西20°：P3→P2=340°，反向160°','任务高度300m；水平3m/s；垂直2m/s；停止转弯；停留5s','闭合循环；P4：E120°13′14″，N30°35′32″'], 'warnings':['原题未给循环次数，不自动补次数。局部位移使用平面模型，不冒称精确球面经纬度答案。']},
 'p4':{'page':4,'name':'5.25 河北04 · 正六边形','conditions':['正六边形边长300m；P1→P2=西偏南15°=255°','H→R：NE300m；P1→R：NW300m，接入须反向为SE','R高度110m；P1/P2/P3高度80m；P4/P5/P6高度50m','水平3m/s；垂直2m/s；停止转弯','P2：N38°25′17″，E113°12′17″；正文停留g，赋值f=5s'], 'warnings':['正文写 g，赋值写 f=5s，疑似变量笔误。未经确认不把 f 自动代入 g。','循环次数与完成后的具体动作需按原题复核，不能自行添加。']}
}


def case_course(key):
    if key not in CASES:raise ValueError('未知代表题')
    case=CASES[key]
    prefix=[step('source','先核对原题条件','右侧“查看原题”保留 PDF 原图；课程结论仅覆盖已确认条件。',result='\n'.join(case['conditions']),objects=(VisualObject('original','image',value=case['page']),),known_conditions=tuple(case['conditions']),warnings=tuple(case['warnings']))]
    questions=[]
    if key=='p1':
        p=defaults('geometry')|{'shape':'triangle','side':60,'bearing':90}
        geometry=geometry_course(p)
        prefix += [replace(s,id='geometry-'+s.id,warnings=s.warnings+('此处为正北参考教学图；原题方向参考待复核。',)) for s in geometry.steps[:2]]
        prefix += [step('speed','先解可确定的速度','原题单位换算不依赖 H 的位置。','v=7.2/3.6',result='2 m/s'),step('branches','保留夹角两侧候选','∠32H=25° 给角度大小，图内位置存在矛盾，不能强选一个 H。',result='完整坐标解：待复核',warnings=tuple(case['warnings']))]
        questions=[Question('水平速度换成 m/s 是多少？',2,'m/s'),Question('等边三角形凸点转向量是多少？',120,'°')]
    else:
        access=defaults('access')
        if key=='p4':access.update(first_bearing=45,first_distance=300,second_bearing=135,second_distance=300)
        ac=access_course(access)
        prefix += [replace(s,id='access-'+s.id) for s in ac.steps]
        p=defaults('geometry')
        if key=='p2':p.update(shape='trapezoid',side=200,short=200,long=300,angle=60,bearing=250)
        else:p.update(shape='hexagon',side=300,bearing=255)
        geo=geometry_course(p)
        # 真题图形叠加在已算出的 P1 上，避免误把局部图的原点当成起飞点。
        r=destinationPoint(Point(0,0),access['first_bearing'],access['first_distance'])
        start=destinationPoint(r,access['second_bearing'],access['second_distance'])
        initial=solvePolygon(p['shape'],p['side'],p['bearing'],short=p['short'],long=p['long'],angle=p['angle']).candidates[0]['points'][0]
        for s in geo.steps:
            # 保留已完成的起飞与避让路径；P1 使用几何图内同一数学位置的对象。
            objects=[o for o in ac.steps[-1].objects if o.id not in ('P1','resultant')]
            for obj in s.objects:
                shifted=tuple(Point(pt.x-initial.x+start.x,pt.y-initial.y+start.y) for pt in obj.points)
                objects.append(replace(obj,points=shifted))
            prefix.append(replace(s,id='geometry-'+s.id,objects=tuple(objects)))
        questions=list(ac.questions)+list(geo.questions)
    final_objects=prefix[-1].objects
    prefix += [step('parameters','最后逐项核对任务参数','几何计算可确定，不代表全部任务参数已经核验。',result='\n'.join(case['conditions'][-3:]),objects=final_objects,warnings=tuple(case['warnings'])),
               step('review','结论边界','确认项可用于练习；待复核项不会被标成标准答案。',result='几何与参数分开验收',objects=final_objects,warnings=tuple(case['warnings']))]
    return Lesson('cases:'+key,case['name'],tuple(prefix),{'case':key},tuple(questions),'review_candidates',tuple(case['warnings']),f'更新日期5.26.pdf · 物理第 {case["page"]} 页')
