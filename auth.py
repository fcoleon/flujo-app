import hashlib
import secrets
import time
from flask import jsonify, make_response, request

SESSION_SECONDS = 90 * 24 * 60 * 60

def hashpw(p,s): return hashlib.pbkdf2_hmac('sha256',p.encode(),bytes.fromhex(s),260000).hex()

def current_user(c):
    return c.execute('SELECT u.id,u.name,u.username,u.admin FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires_at>?',(request.cookies.get('session',''),int(time.time()))).fetchone()

def session_response(token):
    response=make_response(jsonify({'ok':True}))
    response.set_cookie('session',token,max_age=SESSION_SECONDS,httponly=True,samesite='Strict',secure=request.is_secure,path='/')
    return response

def create_session(c,user_id):
    now=int(time.time())
    c.execute('DELETE FROM sessions WHERE expires_at<=?',(now,))
    token=secrets.token_urlsafe(32)
    c.execute('INSERT INTO sessions(token,user_id,expires_at) VALUES(?,?,?)',(token,user_id,now+SESSION_SECONDS))
    return token

def rotate_session(c,user_id):
    c.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
    return create_session(c,user_id)
