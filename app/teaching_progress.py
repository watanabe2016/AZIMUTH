"""2.0 学习记录独立保存，旧八图形掌握记录不删除、不改写。"""
import config as cfg


def load_progress():
    value=cfg.get('teaching_v2',{})
    if not isinstance(value,dict) or value.get('schema_version')!=2:
        return {'schema_version':2,'sessions':{},'completed':[],'attempts':[]}
    return dict(value)


def save_session(chapter,parameters,snapshot):
    value=load_progress()
    sessions=dict(value.get('sessions',{}))
    sessions[chapter]={'parameters':dict(parameters),'snapshot':dict(snapshot)}
    value['sessions']=sessions
    cfg.set_('teaching_v2',value)


def complete(lesson_id):
    value=load_progress()
    done=list(value.get('completed',[]))
    if lesson_id not in done:done.append(lesson_id)
    value['completed']=done
    cfg.set_('teaching_v2',value)


def record_attempt(lesson_id,mode,answers,score):
    from datetime import datetime
    value=load_progress()
    rows=list(value.get('attempts',[]))
    rows.append({'lesson':lesson_id,'mode':mode,'answers':dict(answers),'score':score,'time':datetime.now().isoformat(timespec='seconds')})
    value['attempts']=rows[-200:]
    cfg.set_('teaching_v2',value)
