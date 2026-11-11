import hashlib
import secrets
from flask import jsonify, make_response, request


def hashpw(p,s): return hashlib.pbkdf2_hmac('sha256',p.encode(),bytes.fromhex(s),260000).hex()

def current_user(c):
    return c.execute('SELECT u.id,u.name,u.username,u.admin FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=?',(request.cookies.get('session',''),)).fetchone()

def session_response(token):
    response=make_response(jsonify({'ok':True}))
    response.set_cookie('session',token,httponly=True,samesite='Strict',secure=request.is_secure,path='/')
    return response

def rotate_session(c,user_id):
    c.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
    token=secrets.token_urlsafe(32)
    c.execute('INSERT INTO sessions VALUES(?,?)',(token,user_id))
    return token
