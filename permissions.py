def role(c,u,p):
    if u['admin']: return 'admin'
    r=c.execute('SELECT role FROM members WHERE project_id=? AND user_id=?',(p,u['id'])).fetchone(); return r['role'] if r else None
def task_projects(c,tid):
    return {r['project_id'] for r in c.execute('''WITH RECURSIVE ancestors(id) AS (
        SELECT ? UNION SELECT t.parent_id FROM tasks t JOIN ancestors a ON t.id=a.id WHERE t.parent_id IS NOT NULL
    ) SELECT DISTINCT p.project_id FROM placements p JOIN ancestors a ON p.task_id=a.id''',(tid,))}
def can_task(c,u,tid,write=False):
    task=c.execute('SELECT parent_id,assignee_id FROM tasks WHERE id=?',(tid,)).fetchone()
    if not task: return False
    if u['admin']: return True
    # Project membership includes inherited projects and takes precedence over assignment.
    projects=task_projects(c,tid)
    if projects:
        return any(role(c,u,p) in (('admin','editor') if write else ('admin','editor','viewer')) for p in projects)
    if task['assignee_id']==u['id']: return True
    return bool(task['parent_id'] and can_task(c,u,task['parent_id'],write))

