import hashlib


def mcp_token_hash(token): return hashlib.sha256(token.encode()).hexdigest()


def mcp_tokens(c,uid):
    return [dict(r) for r in c.execute('SELECT id,name,created_at,last_used_at,revoked_at FROM mcp_tokens WHERE user_id=? ORDER BY id DESC',(uid,))]


def mcp_token_user(c,token):
    """Resuelve un token Bearer a su usuario y motivo de rechazo."""
    digest=mcp_token_hash(token)
    row=c.execute('''SELECT u.id,u.name,u.username,u.admin,u.blocked,u.must_change_password
                     FROM mcp_tokens t JOIN users u ON u.id=t.user_id
                     WHERE t.token_hash=? AND t.revoked_at IS NULL''',(digest,)).fetchone()
    if not row: return None,'invalid'
    u={'id':row['id'],'name':row['name'],'username':row['username'],'admin':row['admin']}
    if row['blocked']: return u,'blocked'
    if row['must_change_password']: return u,'password'
    return u,None
