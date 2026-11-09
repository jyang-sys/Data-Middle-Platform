"""Server-side sessions and line-level business scopes."""
import hashlib
import hmac
import secrets
import sqlite3
import time
import os
from contextvars import ContextVar

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import JSONResponse

router = APIRouter(prefix='/api/auth', tags=['账号与权限'])
principal = ContextVar('principal', default=None)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_users (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
    password_hash TEXT NOT NULL, role TEXT NOT NULL, group_id INTEGER REFERENCES business_groups(id),
    employee_id INTEGER REFERENCES employees(id), active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS app_sessions (token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES app_users(id),expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts (identity TEXT PRIMARY KEY,failed INTEGER NOT NULL,window_start REAL NOT NULL);
'''


def initialize_storage():
    from app.main import database
    with database() as db:
        db.executescript(SCHEMA)


def password_hash(value):
    salt = secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', value.encode(), salt.encode(), 210000).hex()


def check_password(value, stored):
    salt, expected = stored.split(':')
    actual = hashlib.pbkdf2_hmac('sha256', value.encode(), salt.encode(), 210000).hex()
    return hmac.compare_digest(actual, expected)


def privileged():
    user = principal.get()
    return user is None or user['role'] in ('admin', 'owner')


def administrator():
    user = principal.get()
    return user is None or user['role'] == 'admin'


def visible_sale(owner):
    user = principal.get()
    if privileged():
        return True
    return owner['group_id'] == user['group_id'] if user['role'] == 'manager' else owner['employee_id'] == user['employee_id']


def visible_stock(group_id):
    user = principal.get()
    return privileged() or (group_id is not None and user['group_id'] == group_id)


async def authorize(request, call_next):
    from app.main import database
    path = request.url.path
    if not path.startswith('/api/') or path in ('/api/health', '/api/auth/session', '/api/auth/setup', '/api/auth/login'):
        return await call_next(request)
    with database() as db:
        configured = db.execute('SELECT 1 FROM app_users LIMIT 1').fetchone()
        mode = db.execute("SELECT value FROM app_metadata WHERE key='data_mode'").fetchone()
        if mode and mode['value'] == 'synthetic' and request.method == 'POST' and (path.startswith('/api/base/sync/') or path == '/api/sales/sync'):
            return JSONResponse({'detail': '本地演示数据库禁止同步真实 Base 数据。'}, status_code=409)
        token = request.cookies.get('mp_session', '')
        user = db.execute('''SELECT u.* FROM app_sessions s JOIN app_users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>? AND u.active=1''', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone() if token else None
    if configured and not user:
        return JSONResponse({'detail': '请先登录。'}, status_code=401)
    if not configured and os.environ.get('APP_REQUIRE_LOGIN') == '1':
        return JSONResponse({'detail': '请先设置管理员账号。'}, status_code=401)
    value = dict(user) if user else None
    context = principal.set(value)
    try:
        if not administrator():
            read = request.method in ('GET', 'HEAD')
            allowed = path.startswith('/api/reports/') and '/mappings' not in path
            allowed |= path in ('/api/catalog/products', '/api/stock-dashboard/options', '/api/stock-dashboard/dataset', '/api/allocations/view', '/api/management/groups', '/api/management/employees')
            allowed |= path.startswith('/api/auth/')
            if not read and path != '/api/auth/logout':
                allowed = False
            if not allowed:
                return JSONResponse({'detail': '当前账号无权访问此数据或执行修改。'}, status_code=403)
        return await call_next(request)
    finally:
        principal.reset(context)


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=200)

    @model_validator(mode='after')
    def normalize_username(self):
        self.username = self.username.strip()
        if not self.username:
            raise ValueError('账号不能为空')
        return self


class UserInput(Credentials):
    id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(pattern='^(admin|owner|manager|operator)$')
    group_id: int | None = Field(default=None, gt=0)
    employee_id: int | None = Field(default=None, gt=0)
    active: bool = True

    @model_validator(mode='after')
    def valid(self):
        self.username, self.name = self.username.strip(), self.name.strip()
        if not self.username or not self.name or (self.role in ('manager', 'operator') and self.group_id is None) or (self.role == 'operator' and self.employee_id is None):
            raise ValueError('请填写账号、姓名，以及角色对应的小组和人员')
        return self


def save_user(db, body):
    from app.management import reference
    reference(db, 'business_groups', body.group_id)
    employee = reference(db, 'employees', body.employee_id)
    if employee and employee['group_id'] != body.group_id:
        raise HTTPException(422, '账号人员与小组不一致。')
    old = db.execute('SELECT * FROM app_users WHERE id=?', (body.id,)).fetchone()
    if body.id and not old:
        raise HTTPException(404, '账号不存在。')
    if old and old['role'] == 'admin' and old['active'] and (body.role != 'admin' or not body.active):
        if not db.execute("SELECT 1 FROM app_users WHERE role='admin' AND active=1 AND id!=?", (body.id,)).fetchone():
            raise HTTPException(409, '不能停用最后一个管理员。')
    values = (body.username, body.name, password_hash(body.password), body.role, body.group_id, body.employee_id, int(body.active))
    try:
        if old:
            db.execute('UPDATE app_users SET username=?,name=?,password_hash=?,role=?,group_id=?,employee_id=?,active=? WHERE id=?', (*values, body.id))
            db.execute('DELETE FROM app_sessions WHERE user_id=?', (body.id,))
            return body.id
        return db.execute('INSERT INTO app_users(username,name,password_hash,role,group_id,employee_id,active) VALUES(?,?,?,?,?,?,?)', values).lastrowid
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, '账号已存在。') from error


@router.get('/session')
def session(request: Request):
    from app.main import database
    with database() as db:
        configured = bool(db.execute('SELECT 1 FROM app_users LIMIT 1').fetchone())
        metadata = db.execute("SELECT value FROM app_metadata WHERE key='data_mode'").fetchone()
        token = request.cookies.get('mp_session', '')
        user = db.execute('''SELECT u.id,u.username,u.name,u.role,u.group_id,u.employee_id FROM app_users u
            JOIN app_sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1''', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone() if token else None
    return {'configured': configured, 'user': dict(user) if user else None, 'data_mode': metadata['value'] if metadata else 'base'}


@router.post('/setup', status_code=201)
def setup(body: Credentials):
    from app.main import database
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM app_users LIMIT 1').fetchone():
            raise HTTPException(409, '管理员已配置，请登录。')
        identity = save_user(db, UserInput(**body.model_dump(), name='系统管理员', role='admin'))
    return {'id': identity}


@router.post('/login')
def login(body: Credentials, request: Request, response: Response):
    from app.main import database
    identity = (request.client.host if request.client else '') + ':' + body.username.casefold()
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        attempt = db.execute('SELECT * FROM login_attempts WHERE identity=?', (identity,)).fetchone()
        if attempt and time.time()-attempt['window_start'] < 900 and attempt['failed'] >= 10:
            raise HTTPException(429, '尝试次数过多，请 15 分钟后重试。')
        user = db.execute('SELECT * FROM app_users WHERE username=? AND active=1', (body.username,)).fetchone()
        stored = user['password_hash'] if user else '00000000000000000000000000000000:' + '0'*64
        valid = check_password(body.password, stored) and user is not None
        if not valid:
            start = attempt['window_start'] if attempt and time.time()-attempt['window_start'] < 900 else time.time()
            count = attempt['failed']+1 if attempt and start == attempt['window_start'] else 1
            db.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)', (identity, count, start))
        else:
            db.execute('DELETE FROM login_attempts WHERE identity=?', (identity,))
            db.execute('DELETE FROM app_sessions WHERE expires_at<=?', (time.time(),))
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO app_sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), user['id'], time.time()+86400))
    if not valid:
        raise HTTPException(401, '账号或密码不正确。')
    response.set_cookie('mp_session', token, httponly=True, samesite='strict', secure=request.url.scheme == 'https', max_age=86400)
    return {'name': user['name'], 'role': user['role']}


@router.post('/logout')
def logout(request: Request, response: Response):
    from app.main import database
    with database() as db:
        db.execute('DELETE FROM app_sessions WHERE token_hash=?', (hashlib.sha256(request.cookies.get('mp_session', '').encode()).hexdigest(),))
    response.delete_cookie('mp_session')
    return {'ok': True}


@router.get('/users')
def users():
    if not administrator():
        raise HTTPException(403, '需要管理员权限。')
    from app.main import database
    with database() as db:
        return [dict(r) for r in db.execute('SELECT id,username,name,role,group_id,employee_id,active FROM app_users ORDER BY id')]


@router.put('/users')
def upsert_user(body: UserInput):
    if not administrator():
        raise HTTPException(403, '需要管理员权限。')
    from app.main import database
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        identity = save_user(db, body)
    return {'id': identity}
