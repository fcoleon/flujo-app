import hmac
import secrets
def post(c,u,path,b,integer_field,text_field,current_user,respond,hashpw,rotate_session,session_response):
    if path=='/api/workspace/update':
        if not u['admin']: raise PermissionError('Solo un administrador del workspace puede cambiar el nombre')
        name=b.get('name')
        if not isinstance(name,str) or not name.strip() or len(name.strip())>120: raise ValueError('El nombre debe tener entre 1 y 120 caracteres')
        c.execute("UPDATE app_meta SET value=? WHERE key='workspace_name'",(name.strip(),))
    elif path=='/api/workspace/block':
        if not u['admin']: raise PermissionError('Solo un administrador del workspace puede bloquear usuarios')
        target=integer_field(b['user_id']); blocked=b.get('blocked')
        if type(blocked) is not bool: raise ValueError('Estado de bloqueo inválido')
        if target==u['id']: raise ValueError('No puedes bloquear tu propia cuenta')
        if not c.execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone(): raise ValueError('Usuario no encontrado')
        c.execute('UPDATE users SET blocked=? WHERE id=?',(int(blocked),target))
    elif path=='/api/profile':
        c.execute('BEGIN IMMEDIATE')
        u=current_user(c)
        if not u: return respond({'error':'Inicia sesión'},401)
        if c.execute('SELECT blocked FROM users WHERE id=?',(u['id'],)).fetchone()['blocked']: return respond({'error':'Has sido bloqueado de este workspace, contacta a tu admin para más información','blocked':True},403)
        name=b.get('name')
        if not isinstance(name,str) or not name.strip() or len(name.strip())>120: raise ValueError('El nombre debe tener entre 1 y 120 caracteres')
        current=b.get('current_password',''); new=b.get('new_password',''); confirmation=b.get('confirm_password','')
        if not all(isinstance(value,str) for value in (current,new,confirmation)): raise ValueError('Contraseña inválida')
        if current or new or confirmation:
            if len(new)<8: raise ValueError('La nueva contraseña debe tener al menos 8 caracteres')
            if new!=confirmation: raise ValueError('Las contraseñas nuevas no coinciden')
            credentials=c.execute('SELECT password,salt FROM users WHERE id=?',(u['id'],)).fetchone()
            if not hmac.compare_digest(credentials['password'],hashpw(current,credentials['salt'])): raise ValueError('La contraseña actual es incorrecta')
            salt=secrets.token_hex(16)
            c.execute('UPDATE users SET password=?,salt=? WHERE id=?',(hashpw(new,salt),salt,u['id']))
        c.execute('UPDATE users SET name=? WHERE id=?',(name.strip(),u['id']))
        if current or new or confirmation:
            token=rotate_session(c,u['id'])
            return session_response(token)
    elif path=='/api/users':
        if not u['admin']: raise PermissionError('Solo el administrador del workspace puede crear usuarios')
        name=text_field(b,'name').strip(); username=text_field(b,'username').strip().lower(); pw=text_field(b,'password')
        if not name or not username or len(pw)<8: raise ValueError('Completa los campos; la contraseña debe tener al menos 8 caracteres')
        s=secrets.token_hex(16); c.execute('INSERT INTO users(name,username,password,salt,admin,must_change_password) VALUES(?,?,?,?,?,1)',(name,username,hashpw(pw,s),s,int(bool(b.get('admin')))))
