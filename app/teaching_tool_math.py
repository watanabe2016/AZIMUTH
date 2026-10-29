"""三个教学工具的纯函数：航向、气象来向、三字母角的双向求解。"""
import re
from teaching_math import finite,normalizeBearing,reverseBearing


WIND_OFFSETS={'顺风':180,'逆风':0,'正左侧风':-90,'正左前风':-45,
              '正左后风':-135,'正右侧风':90,'正右前风':45,'正右后风':135}


def wind_from(heading,mode):
    if mode not in WIND_OFFSETS:raise ValueError('请选择八种来风关系之一')
    return normalizeBearing(finite(heading)+WIND_OFFSETS[mode])


def parse_angle(name):
    text=re.sub(r'[∠\s]','',str(name)).upper()
    parts=text.split('/') if '/' in text else list(text)
    if len(parts)!=3 or len(set(parts))!=3 or any(not re.fullmatch(r'H|P?\d+',p) for p in parts):
        raise ValueError('角名须包含三个不同点，例如 ∠H12；多位编号用 H/12/3')
    first,vertex,last=parts
    return {'name':text,'vertex':vertex,'rays':((vertex,first),(vertex,last))}


def solve_angle(name,known_segment,heading,angle,side=None,target_reverse=False):
    parsed=parse_angle(name);rays=parsed['rays']
    if isinstance(known_segment,str):
        parts=re.split(r'→|->|/',known_segment)
        if len(parts)!=2:raise ValueError('已知航段须写成起点→终点')
        segment=tuple(parts)
    else:segment=tuple(known_segment)
    index=next((i for i,r in enumerate(rays) if segment==r or segment==r[::-1]),None)
    if index is None:raise ValueError('已知航段必须对应夹角的一条射线或其反向')
    value=finite(angle)
    if not 0<value<180:raise ValueError('本工具的夹角须大于 0°、小于 180°')
    if side not in (None,'顺时针','逆时针'):raise ValueError('请选择夹角所在侧')
    reference=normalizeBearing(heading) if segment==rays[index] else reverseBearing(heading)
    branches=('顺时针','逆时针') if side is None else (side,)
    results=[]
    for branch in branches:
        sign=1 if branch=='顺时针' else -1
        target=normalizeBearing(reference+sign*value)
        target_ray=rays[1-index]
        results.append({'side':branch,'reference':reference,'known_ray':rays[index],
                        'target_ray':target_ray,'target':target,'reverse':reverseBearing(target),
                        'result':reverseBearing(target) if target_reverse else target,
                        'sweep':sign*value})
    return parsed,results
