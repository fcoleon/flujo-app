import re
from datetime import datetime, timezone
from statistics import median

def metric_event(c, task_id, event, actor_id, now):
    row=c.execute('SELECT t.created_at,t.type_id,t.assignee_id,ty.name,assignee.name AS assignee_name FROM tasks t JOIN task_types ty ON ty.id=t.type_id LEFT JOIN users assignee ON assignee.id=t.assignee_id WHERE t.id=?',(task_id,)).fetchone()
    if not row: return
    actor=c.execute('SELECT name FROM users WHERE id=?',(actor_id,)).fetchone()
    timestamp=now()
    for project in c.execute('SELECT project_id FROM placements WHERE task_id=?',(task_id,)).fetchall():
        c.execute('INSERT INTO metric_events(project_id,task_id,event,occurred_at,task_created_at,type_id,type_name,actor_id,actor_name,assignee_id,assignee_name,identity_recorded) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)',(project['project_id'],task_id,event,timestamp,row['created_at'],row['type_id'],row['name'],actor_id,actor['name'] if actor else None,row['assignee_id'],row['assignee_name']))

def analytics(c, project_id, month, type_filter, zone):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month): raise ValueError('Mes inválido')
    year,mon=map(int,month.split('-'))
    if year<2 or year>9998: raise ValueError('Mes inválido')
    start=datetime(year,mon,1,tzinfo=zone)
    end=datetime(year+1,1,1,tzinfo=zone) if mon==12 else datetime(year,mon+1,1,tzinfo=zone)
    rows=c.execute('SELECT * FROM metric_events WHERE project_id=? AND occurred_at>=? AND occurred_at<? ORDER BY occurred_at,id',(project_id,start.astimezone(timezone.utc).isoformat(),end.astimezone(timezone.utc).isoformat())).fetchall()
    type_rows=c.execute('SELECT id,name FROM task_types WHERE project_id=? ORDER BY is_base DESC,name',(project_id,)).fetchall()
    types={r['id']:r['name'] for r in type_rows}
    for r in c.execute('SELECT type_id,type_name FROM metric_events WHERE project_id=? ORDER BY id',(project_id,)):
        types.setdefault(r['type_id'],r['type_name'])
    if type_filter is not None: rows=[row for row in rows if row['type_id']==type_filter]
    weeks=[{'label':f'{day}–{min(day+6,(end-start).days)}','created':0,'completed':0} for day in range(1,(end-start).days+1,7)]
    totals={'created':0,'completed':0,'reopened':0};durations=[];people={}
    def person(key,name):
        if key not in people: people[key]={'name':name,'created':0,'completed':0,'durations':[]}
        elif name and key.startswith('user:'): people[key]['name']=name
        return people[key]
    for row in rows:
        event=row['event'];totals[event]+=1
        stamp=datetime.fromisoformat(row['occurred_at']);day=stamp.astimezone(zone).day
        if event in ('created','completed'): weeks[(day-1)//7][event]+=1
        if event=='created':
            if not row['identity_recorded']: p=person('unknown','Sin datos históricos')
            elif row['actor_id'] is None: p=person('unknown','Sin datos históricos')
            else: p=person('user:'+str(row['actor_id']),row['actor_name'] or 'Usuario eliminado')
            p['created']+=1
        if event=='completed':
            if not row['identity_recorded']: p=person('unknown','Sin datos históricos')
            elif row['assignee_id'] is None: p=person('unassigned','Sin responsable')
            else: p=person('user:'+str(row['assignee_id']),row['assignee_name'] or 'Usuario eliminado')
            p['completed']+=1
            try:
                created=datetime.fromisoformat(row['task_created_at'])
                if created.tzinfo is None: created=created.replace(tzinfo=timezone.utc)
                elapsed=(stamp-created).total_seconds()/86400
                if elapsed>=0: durations.append(elapsed);p['durations'].append(elapsed)
            except (ValueError,TypeError): pass
    buckets=[{'label':'Hasta 1 día','count':0},{'label':'1–3 días','count':0},{'label':'3–7 días','count':0},{'label':'Más de 7 días','count':0}]
    for days in durations: buckets[0 if days<=1 else 1 if days<=3 else 2 if days<=7 else 3]['count']+=1
    people_rows=[]
    for key,p in people.items():
        times=p.pop('durations');p['average_days']=sum(times)/len(times) if times else None;p['duration_count']=len(times);p['special']=not key.startswith('user:');people_rows.append(p)
    people_rows.sort(key=lambda p:(p['special'],p['name'].casefold()))
    return {'people':people_rows,'identity_started_at':c.execute("SELECT value FROM app_meta WHERE key='metrics_identity_started_at'").fetchone()['value'],'month':month,'timezone':str(zone),'started_at':c.execute("SELECT value FROM app_meta WHERE key='metrics_started_at'").fetchone()['value'],'types':[{'id':k,'name':v} for k,v in types.items()],'totals':totals,'average_days':sum(durations)/len(durations) if durations else None,'median_days':median(durations) if durations else None,'duration_count':len(durations),'weeks':weeks,'durations':buckets}
