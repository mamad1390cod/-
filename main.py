"""
پیام‌رسان صوتی - نسخه MySQL
"""

import os
import json
import asyncio
import hashlib
import aiomysql
import aiosqlite
from pathlib import Path
from typing import Dict, Set, Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from collections import defaultdict
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

# ========== تنظیمات ==========
BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
DB_FILE = BASE_DIR / "data.db"

# ========== کدهای ویژه ==========
ADMIN_CODE = "1361649093"
SUPPORT_CODE = "13901390"
SUPPORT_PASSWORD = "mamad1390"

# ========== MySQL ==========
MYSQL_CONFIG = {
    "host": os.environ.get("MYSQLHOST","mysql.railway.internal"),
    "port": int(os.environ.get("MYSQLPORT", 3306)),
    "user": os.environ.get("MYSQLUSER", "root"),
    "password": os.environ.get("MYSQLPASSWORD", "OiqwqvQpDEjXVnXvRPdmhIjlGyYEdhPb"),
    "db": os.environ.get("MYSQLDATABASE", "railway"),
    "charset": "utf8mb4",
    "autocommit": True
}

# یا از URL کامل
MYSQL_URL = os.environ.get("MYSQL_URL", os.environ.get("DATABASE_URL", "mysql://root:OiqwqvQpDEjXVnXvRPdmhIjlGyYEdhPb@mysql.railway.internal:3306/railway"))

pool: Optional[aiomysql.Pool] = None
sqlite_conn: Optional[aiosqlite.Connection] = None

def parse_mysql_url(url: str) -> dict:
    """پارس کردن MySQL URL"""
    # mysql://user:pass@host:port/database
    if url.startswith("mysql://"):
        url = url[8:]
    elif url.startswith("mysql+pymysql://"):
        url = url[16:]
    
    # user:pass@host:port/database
    if "@" in url:
        user_pass, host_db = url.split("@", 1)
        if ":" in user_pass:
            user, password = user_pass.split(":", 1)
        else:
            user, password = user_pass, ""
    else:
        user, password = "root", ""
        host_db = url
    
    if "/" in host_db:
        host_port, database = host_db.split("/", 1)
    else:
        host_port, database = host_db, "messenger"
    
    if ":" in host_port:
        host, port = host_port.split(":", 1)
        port = int(port)
    else:
        host, port = host_port, 3306
    
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "db": database,
        "charset": "utf8mb4",
        "autocommit": True
    }

async def init_db():
    """اتصال به MySQL یا SQLite"""
    global pool, sqlite_conn

    # اول چک کن اگر DATABASE_URL یا MYSQL_URL موجود باشه و معتبر باشه
    db_url = MYSQL_URL
    if db_url and db_url != "mysql://root:OiqwqvQpDEjXVnXvRPdmhIjlGyYEdhPb@mysql.railway.internal:3306/railway":
        try:
            config = parse_mysql_url(db_url)
            print(f"🔌 Connecting to MySQL via URL: {config['host']}:{config['port']}/{config['db']}")

            pool = await aiomysql.create_pool(
                minsize=1,
                maxsize=10,
                **config
            )

            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    await conn.commit()
            
            # ساخت جداول MySQL
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    # جدول کاربران
                    await cur.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            code VARCHAR(20) PRIMARY KEY,
                            name VARCHAR(100) NOT NULL,
                            country VARCHAR(10),
                            password_hash VARCHAR(64) NOT NULL,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """)
                    
# جدول تنظیمات
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key VARCHAR(50) PRIMARY KEY,
                        value VARCHAR(255) NOT NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                
                # جدول بن‌ها
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS bans (
                        user_code VARCHAR(20) PRIMARY KEY,
                        reason TEXT,
                        is_permanent BOOLEAN DEFAULT FALSE,
                        until_time DATETIME,
                        banned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_code) REFERENCES users(code) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                for key, value in default_settings.items():
                    await cur.execute("""
                        INSERT INTO settings (key, value) VALUES (%s, %s)
                        ON DUPLICATE KEY UPDATE value = VALUES(value)
                    """, (key, value))
                
                # اکانت پشتیبانی
                support_code = default_settings["support_code"]
                support_pass = default_settings["support_password"]
                support_hash = hashlib.sha256(support_pass.encode()).hexdigest()
                await cur.execute("""
                    INSERT INTO users (code, name, country, password_hash)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                    name = VALUES(name),
                    password_hash = VALUES(password_hash)
                """, (support_code, "پشتیبانی", "IR", support_hash))
                
                await conn.commit()
            
            print(f"✅ MySQL connected! Support: {SUPPORT_CODE} / {SUPPORT_PASSWORD}")
            return True
            
        except Exception as e:
            print(f"❌ MySQL via URL Error: {e}")
    
    # اگر URL کار نکرد یا موجود نبود، مستقیم به SQLite برو
    print("⚠️ Using SQLite database...")
    
    try:
        sqlite_conn = await aiosqlite.connect(str(DB_FILE))
        await sqlite_conn.execute("PRAGMA journal_mode=WAL")  # برای concurrent بهتر
        
        # ساخت جداول SQLite
        await sqlite_conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                country TEXT,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await sqlite_conn.execute("""
            CREATE TABLE IF NOT EXISTS bans (
                user_code TEXT PRIMARY KEY,
                reason TEXT,
                is_permanent INTEGER DEFAULT 0,
                until_time TEXT,
                banned_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_code) REFERENCES users(code) ON DELETE CASCADE
            )
        """)
        
        await sqlite_conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        
        # تنظیمات پیش‌فرض
        default_settings = {
            "admin_code": "1361649093",
            "support_code": "13901390",
            "support_password": "mamad1390"
        }
        for key, value in default_settings.items():
            await sqlite_conn.execute("""
                INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)
            """, (key, value))
        
        # اکانت پشتیبانی
        support_code = default_settings["support_code"]
        support_pass = default_settings["support_password"]
        support_hash = hashlib.sha256(support_pass.encode()).hexdigest()
        await sqlite_conn.execute("""
            INSERT OR REPLACE INTO users (code, name, country, password_hash)
            VALUES (?, ?, ?, ?)
        """, (support_code, "پشتیبانی", "IR", support_hash))
        
        await sqlite_conn.commit()
        
        print(f"✅ SQLite connected! Support: {support_code} / {support_pass}")
        return True
        
    except Exception as e2:
        print(f"❌ SQLite Error: {e2}")
        return False

async def close_db():
    """بستن اتصال MySQL یا SQLite"""
    global pool, sqlite_conn
    if pool:
        pool.close()
        await pool.wait_closed()
    if sqlite_conn:
        await sqlite_conn.close()

# ========== توابع دیتابیس ==========
async def get_user(code: str) -> Optional[dict]:
    """دریافت کاربر"""
    if pool:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute("SELECT * FROM users WHERE code = %s", (code,))
                    return await cur.fetchone()
        except Exception as e:
            print(f"❌ get_user MySQL error: {e}")
    
    if sqlite_conn:
        try:
            async with sqlite_conn.execute("SELECT * FROM users WHERE code = ?", (code,)) as cur:
                row = await cur.fetchone()
                if row:
                    return {
                        "code": row[0],
                        "name": row[1],
                        "country": row[2],
                        "password_hash": row[3],
                        "created_at": row[4]
                    }
        except Exception as e:
            print(f"❌ get_user SQLite error: {e}")
    
    return None

async def create_user(code: str, name: str, country: str, password: str) -> bool:
    """ایجاد کاربر جدید"""
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    if pool:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        INSERT INTO users (code, name, country, password_hash)
                        VALUES (%s, %s, %s, %s)
                    """, (code, name, country, password_hash))
                    await conn.commit()
                    return True
        except Exception as e:
            print(f"❌ create_user MySQL error: {e}")
    
    if sqlite_conn:
        try:
            await sqlite_conn.execute("""
                INSERT INTO users (code, name, country, password_hash)
                VALUES (?, ?, ?, ?)
            """, (code, name, country, password_hash))
            await sqlite_conn.commit()
            return True
        except Exception as e:
            print(f"❌ create_user SQLite error: {e}")
    
    return False

async def verify_user(code: str, password: str) -> Optional[dict]:
    """تایید رمز کاربر"""
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    if pool:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        "SELECT * FROM users WHERE code = %s AND password_hash = %s",
                        (code, password_hash)
                    )
                    return await cur.fetchone()
        except Exception as e:
            print(f"❌ verify_user MySQL error: {e}")
    
    if sqlite_conn:
        try:
            async with sqlite_conn.execute(
                "SELECT * FROM users WHERE code = ? AND password_hash = ?",
                (code, password_hash)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return {
                        "code": row[0],
                        "name": row[1],
                        "country": row[2],
                        "password_hash": row[3],
                        "created_at": row[4]
                    }
        except Exception as e:
            print(f"❌ verify_user SQLite error: {e}")
    
    return None

async def get_all_users() -> List[dict]:
    """دریافت همه کاربران"""
    if pool:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute("""
                        SELECT u.*, b.reason as ban_reason, b.is_permanent, b.until_time
                        FROM users u
                        LEFT JOIN bans b ON u.code = b.user_code
                        ORDER BY u.created_at DESC
                    """)
                    users = await cur.fetchall()
                    
                    result = []
                    for u in users:
                        is_banned = False
                        if u.get('ban_reason') is not None:
                            if u.get('is_permanent'):
                                is_banned = True
                            elif u.get('until_time') and u['until_time'] > datetime.now():
                                is_banned = True
                        
                        result.append({
                            "code": u['code'],
                            "name": u['name'],
                            "country": u.get('country', ''),
                            "banned": is_banned,
                            "ban_reason": u.get('ban_reason'),
                            "online": u['code'] in online_users
                        })
                    
                    return result
        except Exception as e:
            print(f"❌ get_all_users MySQL error: {e}")
    
    if sqlite_conn:
        try:
            query = """
                SELECT u.code, u.name, u.country, u.created_at,
                       b.reason as ban_reason, b.is_permanent, b.until_time
                FROM users u
                LEFT JOIN bans b ON u.code = b.user_code
                ORDER BY u.created_at DESC
            """
            async with sqlite_conn.execute(query) as cur:
                rows = await cur.fetchall()
                
                result = []
                for row in rows:
                    is_banned = False
                    ban_reason = row[4]
                    if ban_reason is not None:
                        if row[5]:  # is_permanent
                            is_banned = True
                        elif row[6] and datetime.fromisoformat(row[6]) > datetime.now():
                            is_banned = True
                    
                    result.append({
                        "code": row[0],
                        "name": row[1],
                        "country": row[2] or '',
                        "banned": is_banned,
                        "ban_reason": ban_reason,
                        "online": row[0] in online_users
                    })
                
                return result
        except Exception as e:
            print(f"❌ get_all_users SQLite error: {e}")
    
    return []

async def ban_user(code: str, duration: int, reason: str) -> bool:
    """بن کردن کاربر"""
    if pool:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    if duration == 0:
                        # بن دائمی
                        await cur.execute("""
                            INSERT INTO bans (user_code, reason, is_permanent)
                            VALUES (%s, %s, TRUE)
                            ON DUPLICATE KEY UPDATE 
                            reason = VALUES(reason),
                            is_permanent = TRUE,
                            until_time = NULL
                        """, (code, reason))
                    else:
                        # بن موقت
                        until = datetime.now() + timedelta(hours=duration)
                        await cur.execute("""
                            INSERT INTO bans (user_code, reason, is_permanent, until_time)
                            VALUES (%s, %s, FALSE, %s)
                            ON DUPLICATE KEY UPDATE 
                            reason = VALUES(reason),
                            is_permanent = FALSE,
                            until_time = VALUES(until_time)
                        """, (code, reason, until))
                    
                    await conn.commit()
                    return True
        except Exception as e:
            print(f"❌ ban_user MySQL error: {e}")
    
    if sqlite_conn:
        try:
            if duration == 0:
                await sqlite_conn.execute("""
                    INSERT OR REPLACE INTO bans (user_code, reason, is_permanent, until_time)
                    VALUES (?, ?, 1, NULL)
                """, (code, reason))
            else:
                until = (datetime.now() + timedelta(hours=duration)).isoformat()
                await sqlite_conn.execute("""
                    INSERT OR REPLACE INTO bans (user_code, reason, is_permanent, until_time)
                    VALUES (?, ?, 0, ?)
                """, (code, reason, until))
            
            await sqlite_conn.commit()
            return True
        except Exception as e:
            print(f"❌ ban_user SQLite error: {e}")
    
    return False

async def unban_user(code: str) -> bool:
    """آزاد کردن کاربر"""
    if pool:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("DELETE FROM bans WHERE user_code = %s", (code,))
                    await conn.commit()
                    return True
        except Exception as e:
            print(f"❌ unban_user MySQL error: {e}")
    
    if sqlite_conn:
        try:
            await sqlite_conn.execute("DELETE FROM bans WHERE user_code = ?", (code,))
            await sqlite_conn.commit()
            return True
        except Exception as e:
            print(f"❌ unban_user SQLite error: {e}")
    
    return False

async def is_banned(code: str) -> tuple:
    """چک کردن بن کاربر"""
    if pool:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute("SELECT reason, is_permanent, until_time FROM bans WHERE user_code = %s", (code,))
                    ban = await cur.fetchone()
                    if ban:
                        if ban.get('is_permanent'):
                            return True, ban.get('reason', '')
                        elif ban.get('until_time') and ban['until_time'] > datetime.now():
                            return True, ban.get('reason', '')
                    return False, ""
        except Exception as e:
            print(f"❌ is_banned MySQL error: {e}")
    
    if sqlite_conn:
        try:
            async with sqlite_conn.execute("SELECT reason, is_permanent, until_time FROM bans WHERE user_code = ?", (code,)) as cur:
                ban = await cur.fetchone()
                if ban:
                    if ban[1]:  # is_permanent
                        return True, ban[0] or ''
                    elif ban[2]:
                        until = datetime.fromisoformat(ban[2])
                        if until > datetime.now():
                            return True, ban[0] or ''
                return False, ""
        except Exception as e:
            print(f"❌ is_banned SQLite error: {e}")
    
    # Fallback to JSON
    return is_banned_json(code)

async def get_setting(key: str) -> str:
    """دریافت تنظیمات"""
    if pool:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
                    row = await cur.fetchone()
                    return row['value'] if row else ""
        except Exception as e:
            print(f"❌ get_setting MySQL error: {e}")
    
    if sqlite_conn:
        try:
            async with sqlite_conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
                row = await cur.fetchone()
                return row[0] if row else ""
        except Exception as e:
            print(f"❌ get_setting SQLite error: {e}")
    
    return ""

async def set_setting(key: str, value: str) -> bool:
    """تنظیم تنظیمات"""
    if pool:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        INSERT INTO settings (key, value) VALUES (%s, %s)
                        ON DUPLICATE KEY UPDATE value = VALUES(value)
                    """, (key, value))
                    await conn.commit()
                    return True
        except Exception as e:
            print(f"❌ set_setting MySQL error: {e}")
    
    if sqlite_conn:
        try:
            await sqlite_conn.execute("""
                INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)
            """, (key, value))
            await sqlite_conn.commit()
            return True
        except Exception as e:
            print(f"❌ set_setting SQLite error: {e}")
    
    return False

# ========== JSON Fallback ==========
DATA_FILE = BASE_DIR / "data.json"
json_db = {"users": {}, "bans": {}}

def load_json():
    global json_db
    try:
        if DATA_FILE.exists():
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                json_db = json.load(f)
    except:
        json_db = {"users": {}, "bans": {}}
    
    # اکانت پشتیبانی
    support_hash = hashlib.sha256(SUPPORT_PASSWORD.encode()).hexdigest()
    json_db.setdefault("users", {})[SUPPORT_CODE] = {
        "code": SUPPORT_CODE,
        "name": "پشتیبانی",
        "country": "IR",
        "password_hash": support_hash
    }
    save_json()

def save_json():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(json_db, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"❌ Save JSON error: {e}")

def get_user_json(code: str) -> Optional[dict]:
    return json_db.get("users", {}).get(code)

def create_user_json(code: str, name: str, country: str, password_hash: str) -> bool:
    json_db.setdefault("users", {})[code] = {
        "code": code,
        "name": name,
        "country": country,
        "password_hash": password_hash
    }
    save_json()
    return True

def verify_user_json(code: str, password_hash: str) -> Optional[dict]:
    user = json_db.get("users", {}).get(code)
    if user and user.get("password_hash") == password_hash:
        return user
    return None

def get_all_users_json() -> List[dict]:
    result = []
    for code, user in json_db.get("users", {}).items():
        ban = json_db.get("bans", {}).get(code)
        is_banned = False
        if ban:
            if ban.get("is_permanent"):
                is_banned = True
            elif ban.get("until"):
                try:
                    until = datetime.fromisoformat(ban["until"])
                    is_banned = until > datetime.now()
                except:
                    pass
        
        result.append({
            "code": code,
            "name": user.get("name", ""),
            "country": user.get("country", ""),
            "banned": is_banned,
            "online": code in online_users
        })
    return result

def ban_user_json(code: str, duration: int, reason: str) -> bool:
    ban_data = {"reason": reason, "banned_at": datetime.now().isoformat()}
    if duration == 0:
        ban_data["is_permanent"] = True
    else:
        ban_data["until"] = (datetime.now() + timedelta(hours=duration)).isoformat()
    json_db.setdefault("bans", {})[code] = ban_data
    save_json()
    return True

def unban_user_json(code: str) -> bool:
    if code in json_db.get("bans", {}):
        del json_db["bans"][code]
        save_json()
    return True

def is_banned_json(code: str) -> tuple[bool, str]:
    ban = json_db.get("bans", {}).get(code)
    if not ban:
        return False, ""
    
    if ban.get("is_permanent"):
        return True, ban.get("reason", "")
    
    if ban.get("until"):
        try:
            until = datetime.fromisoformat(ban["until"])
            if until > datetime.now():
                return True, ban.get("reason", "")
            else:
                del json_db["bans"][code]
                save_json()
        except:
            pass
    
    return False, ""

# ========== آنلاین و تماس ==========
online_users: Dict[str, WebSocket] = {}
user_names: Dict[str, str] = {}
group_calls: Dict[str, dict] = {}
active_calls: Dict[str, str] = {}  # caller -> receiver

# ========== FastAPI ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    db_ok = await init_db()
    if not db_ok:
        print("⚠️ No database available")
    print("🚀 Server started")
    yield
    await close_db()
    print("👋 Server stopped")

app = FastAPI(lifespan=lifespan)

# ========== Connection Manager ==========
class ConnectionManager:
    
    async def connect(self, ws: WebSocket, code: str, name: str):
        await ws.accept()
        online_users[code] = ws
        user_names[code] = name
        print(f"[+] {name} ({code}) connected. Online: {len(online_users)}")
        await self.broadcast_status(code, True, name)
    
    async def disconnect(self, code: str):
        if code in online_users:
            del online_users[code]
        name = user_names.pop(code, "کاربر")
        print(f"[-] {name} ({code}) disconnected. Online: {len(online_users)}")
        
        # خروج از تماس گروهی
        for group_code in list(group_calls.keys()):
            members = group_calls[group_code].get("members", set())
            if code in members:
                members.discard(code)
                await self.broadcast_to_call(group_code, {
                    "type": "call_member_left",
                    "code": code
                }, exclude=code)
                if not members:
                    del group_calls[group_code]
        
        # خروج از تماس معمولی
        to_remove = []
        for caller, receiver in active_calls.items():
            if caller == code or receiver == code:
                to_remove.append(caller)
        for c in to_remove:
            del active_calls[c]
        
        await self.broadcast_status(code, False, name)
    
    async def send_to(self, code: str, data: dict) -> bool:
        if code in online_users:
            try:
                await online_users[code].send_json(data)
                return True
            except:
                return False
        return False
    
    async def send_audio(self, code: str, data: bytes) -> bool:
        if code in online_users:
            try:
                await online_users[code].send_bytes(data)
                return True
            except:
                return False
        return False
    
    async def broadcast_status(self, code: str, online: bool, name: str):
        msg = {"type": "contact_status", "code": code, "online": online, "name": name}
        tasks = []
        for user_code, ws in list(online_users.items()):
            if user_code != code:
                try:
                    tasks.append(ws.send_json(msg))
                except:
                    pass
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def broadcast_to_call(self, group_code: str, data: dict, exclude: str = None):
        if group_code not in group_calls:
            return
        members = group_calls[group_code].get("members", set())
        tasks = []
        for member in members:
            if member != exclude and member in online_users:
                try:
                    tasks.append(online_users[member].send_json(data))
                except:
                    pass
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

manager = ConnectionManager()

# ========== WebSocket ==========
@app.websocket("/ws/{code}/{name}")
async def websocket_endpoint(ws: WebSocket, code: str, name: str):
    # چک بن
    banned, reason = await is_banned(code)
    if banned:
        await ws.accept()
        await ws.send_json({"type": "banned", "reason": reason})
        await ws.close()
        return
    
    await manager.connect(ws, code, name)
    
    try:
        while True:
            msg = await ws.receive()
            
            if "bytes" in msg:
                # صدا - ارسال به تماس گروهی یا تماس معمولی
                audio_sent = False
                for gc, call_data in list(group_calls.items()):
                    if code in call_data.get("members", set()):
                        for m in call_data["members"]:
                            if m != code:
                                await manager.send_audio(m, msg["bytes"])
                        audio_sent = True
                        break
                
                # اگر در تماس گروهی نبود، چک کن تماس معمولی
                if not audio_sent:
                    for caller, receiver in active_calls.items():
                        if caller == code and receiver in online_users:
                            await manager.send_audio(receiver, msg["bytes"])
                            break
                        elif receiver == code and caller in online_users:
                            await manager.send_audio(caller, msg["bytes"])
                            break
            
            elif "text" in msg:
                try:
                    data = json.loads(msg["text"])
                    await handle_message(code, data)
                except json.JSONDecodeError:
                    pass
    
    except WebSocketDisconnect:
        await manager.disconnect(code)
    except Exception as e:
        print(f"[!] Error: {e}")
        await manager.disconnect(code)

async def handle_message(sender: str, data: dict):
    msg_type = data.get("type")
    sender_name = user_names.get(sender, "کاربر")
    
    if msg_type == "sync":
        contacts = data.get("contacts", [])
        for c in contacts:
            is_online = c in online_users
            c_name = user_names.get(c, "کاربر")
            await manager.send_to(sender, {
                "type": "contact_status",
                "code": c,
                "online": is_online,
                "name": c_name
            })
    
    elif msg_type == "message":
        to = data.get("to")
        await manager.send_to(to, {
            "type": "message",
            "id": data.get("id"),
            "from": sender,
            "senderName": sender_name,
            "text": data.get("text", ""),
            "time": datetime.now().timestamp() * 1000
        })
    
    elif msg_type == "group_message":
        group_code = data.get("to")
        # ارسال به همه آنلاین‌ها (در واقعیت باید به اعضای گروه)
        for user_code in list(online_users.keys()):
            if user_code != sender:
                await manager.send_to(user_code, {
                    "type": "group_message",
                    "id": data.get("id"),
                    "groupCode": group_code,
                    "from": sender,
                    "senderName": sender_name,
                    "text": data.get("text", ""),
                    "time": datetime.now().timestamp() * 1000
                })
    
    elif msg_type == "media":
        to = data.get("to")
        await manager.send_to(to, {
            "type": "media",
            "id": data.get("id"),
            "from": sender,
            "senderName": sender_name,
            "mediaType": data.get("mediaType"),
            "mediaData": data.get("mediaData"),
            "duration": data.get("duration"),
            "time": datetime.now().timestamp() * 1000
        })
    
    elif msg_type == "call_request":
        to = data.get("to")
        active_calls[sender] = to  # فرض caller -> receiver
        await manager.send_to(to, {
            "type": "incoming_call",
            "callerCode": sender,
            "callerName": sender_name
        })
        await manager.send_to(sender, {"type": "call_ringing", "to": to})
    
    elif msg_type == "call_accept":
        to = data.get("to")
        active_calls[to] = sender  # receiver -> caller
        await manager.send_to(to, {"type": "call_accepted"})
    
    elif msg_type == "call_reject":
        to = data.get("to")
        if sender in active_calls:
            del active_calls[sender]
        if to in active_calls:
            del active_calls[to]
        await manager.send_to(to, {"type": "call_rejected"})
    
    elif msg_type == "call_end":
        to = data.get("to")
        if sender in active_calls:
            del active_calls[sender]
        if to in active_calls:
            del active_calls[to]
        await manager.send_to(to, {"type": "call_ended"})
    
    # تماس گروهی
    elif msg_type == "group_call":
        group_code = data.get("to")
        group_name = data.get("groupName", "گروه")
        
        if group_code in group_calls and group_calls[group_code].get("active"):
            # تماس فعال - ملحق شو
            group_calls[group_code]["members"].add(sender)
            await manager.broadcast_to_call(group_code, {
                "type": "call_member_joined",
                "code": sender,
                "name": sender_name
            }, exclude=sender)
            
            for m in group_calls[group_code]["members"]:
                if m != sender:
                    await manager.send_to(sender, {
                        "type": "call_member_joined",
                        "code": m,
                        "name": user_names.get(m, "کاربر")
                    })
            
            await manager.send_to(sender, {"type": "call_accepted"})
        else:
            # تماس جدید
            group_calls[group_code] = {
                "members": {sender},
                "starter": sender,
                "active": True
            }
            
            # ارسال به همه آنلاین‌ها (باید به اعضای گروه باشد)
            for user_code in list(online_users.keys()):
                if user_code != sender:
                    await manager.send_to(user_code, {
                        "type": "incoming_call",
                        "callerCode": sender,
                        "callerName": sender_name,
                        "groupCode": group_code,
                        "groupName": group_name,
                        "isGroup": True
                    })
            
            await manager.send_to(sender, {"type": "call_ringing", "isGroup": True})
            # برای تماس گروهی، starter مستقیم accepted می‌شه
            await manager.send_to(sender, {"type": "call_accepted"})
    
    elif msg_type == "join_group_call":
        group_code = data.get("to")
        
        if group_code not in group_calls:
            group_calls[group_code] = {"members": set(), "active": True}
        
        group_calls[group_code]["members"].add(sender)
        
        await manager.broadcast_to_call(group_code, {
            "type": "call_member_joined",
            "code": sender,
            "name": sender_name
        }, exclude=sender)
        
        for m in group_calls[group_code]["members"]:
            if m != sender:
                await manager.send_to(sender, {
                    "type": "call_member_joined",
                    "code": m,
                    "name": user_names.get(m, "کاربر")
                })
        
        starter = group_calls[group_code].get("starter")
        if starter and starter != sender:
            await manager.send_to(starter, {"type": "call_accepted"})
    
    elif msg_type == "leave_group_call":
        group_code = data.get("to")
        if group_code in group_calls:
            group_calls[group_code]["members"].discard(sender)
            await manager.broadcast_to_call(group_code, {
                "type": "call_member_left",
                "code": sender
            })
            if not group_calls[group_code]["members"]:
                del group_calls[group_code]
    
    elif msg_type == "add_member":
        group_code = data.get("groupCode")
        member_code = data.get("memberCode")
        if group_code in group_calls and group_calls[group_code].get("active"):
            group_calls[group_code]["members"].add(member_code)
            # اطلاع به عضو جدید
            await manager.send_to(member_code, {
                "type": "added_to_group_call",
                "groupCode": group_code
            })
            # اطلاع به دیگران
            await manager.broadcast_to_call(group_code, {
                "type": "call_member_joined",
                "code": member_code,
                "name": user_names.get(member_code, "کاربر")
            }, exclude=member_code)
    
    elif msg_type == "kick_member":
        group_code = data.get("groupCode")
        member_code = data.get("memberCode")
        if group_code in group_calls and member_code in group_calls[group_code]["members"]:
            group_calls[group_code]["members"].discard(member_code)
            await manager.send_to(member_code, {
                "type": "kicked_from_group_call",
                "groupCode": group_code
            })
            await manager.broadcast_to_call(group_code, {
                "type": "call_member_left",
                "code": member_code,
                "name": user_names.get(member_code, "کاربر")
            })

# ========== API ==========
@app.post("/api/register")
async def register(data: dict):
    code = data.get("code")
    name = data.get("name", "")[:50]
    country = data.get("country", "")
    password = data.get("password", "")
    
    if not code or not name or not password:
        raise HTTPException(400, "اطلاعات ناقص است")
    
    if len(password) < 4:
        raise HTTPException(400, "رمز حداقل ۴ کاراکتر")
    
    # چک تکراری
    existing = await get_user(code)
    if existing:
        raise HTTPException(400, "این کد قبلاً ثبت شده")
    
    success = await create_user(code, name, country, password)
    if not success:
        raise HTTPException(500, "خطا در ثبت‌نام")
    
    print(f"✅ New user: {name} ({code})")
    return {"success": True, "code": code}

@app.post("/api/login")
async def login(data: dict):
    code = data.get("code", "")
    password = data.get("password", "")
    
    # ادمین
    admin_code = await get_setting("admin_code")
    if code == admin_code:
        return {"success": True, "isAdmin": True}
    
    # چک بن
    banned, reason = await is_banned(code)
    if banned:
        raise HTTPException(403, f"شما بن شده‌اید: {reason}")
    
    # چک کاربر
    user = await verify_user(code, password)
    if not user:
        raise HTTPException(401, "کد یا رمز اشتباه است")
    
    return {
        "success": True,
        "user": {
            "code": user["code"],
            "name": user["name"],
            "country": user.get("country", "")
        }
    }

@app.get("/api/admin/users")
async def admin_users(admin_key: str = ""):
    admin_code = await get_setting("admin_code")
    if admin_key != admin_code:
        raise HTTPException(403, "دسترسی ندارید")
    
    users = await get_all_users()
    return {
        "users": users,
        "total": len(users),
        "online": len(online_users)
    }

@app.post("/api/admin/ban")
async def admin_ban(admin_key: str = "", user_code: str = "", duration: int = 0, reason: str = ""):
    admin_code = await get_setting("admin_code")
    if admin_key != admin_code:
        raise HTTPException(403, "دسترسی ندارید")
    
    await ban_user(user_code, duration, reason)
    
    # قطع اتصال
    if user_code in online_users:
        try:
            await online_users[user_code].send_json({"type": "banned", "reason": reason})
            await online_users[user_code].close()
        except:
            pass
    
    return {"success": True}

@app.post("/api/admin/unban")
async def admin_unban(admin_key: str = "", user_code: str = ""):
    admin_code = await get_setting("admin_code")
    if admin_key != admin_code:
        raise HTTPException(403, "دسترسی ندارید")
    
    await unban_user(user_code)
    
    # اگر کاربر آنلاین است، اتصال را قطع کن تا دوباره چک بن شود
    if user_code in online_users:
        try:
            await online_users[user_code].close()
        except:
            pass
    
    return {"success": True}

@app.get("/api/admin/settings")
async def get_admin_settings(admin_key: str = ""):
    admin_code = await get_setting("admin_code")
    if admin_key != admin_code:
        raise HTTPException(403, "دسترسی ندارید")
    
    settings = {}
    if pool:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT `key`, value FROM settings")
                    rows = await cur.fetchall()
                    for row in rows:
                        settings[row[0]] = row[1]
        except Exception as e:
            print(f"❌ get_admin_settings MySQL error: {e}")
    
    if sqlite_conn:
        async with sqlite_conn.execute("SELECT key, value FROM settings") as cur:
            async for row in cur:
                settings[row[0]] = row[1]
    return {"settings": settings}

@app.post("/api/admin/settings")
async def set_admin_settings(admin_key: str = "", settings: dict = {}):
    admin_code = await get_setting("admin_code")
    if admin_key != admin_code:
        raise HTTPException(403, "دسترسی ندارید")
    
    for key, value in settings.items():
        await set_setting(key, value)
    
    # اگر support_code یا password تغییر کرد، کاربر پشتیبانی رو آپدیت کن
    support_code = await get_setting("support_code")
    support_pass = await get_setting("support_password")
    if support_code and support_pass:
        support_hash = hashlib.sha256(support_pass.encode()).hexdigest()
        
        if pool:
            try:
                async with pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute("""
                            UPDATE users SET password_hash = %s WHERE code = %s
                        """, (support_hash, support_code))
                        await conn.commit()
            except:
                pass
        if sqlite_conn:
            try:
                await sqlite_conn.execute("""
                    INSERT OR REPLACE INTO users (code, name, country, password_hash)
                    VALUES (?, ?, ?, ?)
                """, (support_code, "پشتیبانی", "IR", support_hash))
                await sqlite_conn.commit()
            except:
                pass
    
    return {"success": True}

@app.post("/api/admin/change_code")
async def admin_change_code(admin_key: str = "", old_code: str = "", new_code: str = ""):
    admin_code = await get_setting("admin_code")
    if admin_key != admin_code:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    if not old_code or not new_code or len(new_code) != 8 or not new_code.isdigit():
        raise HTTPException(status_code=400, detail="Invalid codes")
    
    # چک کن کد جدید منحصر به فرد باشد
    existing = await get_user(new_code)
    if existing:
        return {"success": False, "error": "کد جدید تکراری است"}
    
    # تغییر کد
    if pool:
        try:
            async with pool.acquire() as conn:
                await conn.execute("UPDATE users SET code = ? WHERE code = ?", (new_code, old_code))
                await conn.commit()
        except Exception as e:
            print(f"❌ change_code MySQL error: {e}")
            return {"success": False, "error": "خطای دیتابیس"}
    
    if sqlite_conn:
        try:
            await sqlite_conn.execute("UPDATE users SET code = ? WHERE code = ?", (new_code, old_code))
            await sqlite_conn.commit()
        except Exception as e:
            print(f"❌ change_code SQLite error: {e}")
            return {"success": False, "error": "خطای دیتابیس"}
    
    # اگر کاربر آنلاین است، اتصال را قطع کن تا با کد جدید وارد شود
    if old_code in online_users:
        try:
            await online_users[old_code].close()
        except:
            pass
        del online_users[old_code]
        if old_code in user_names:
            del user_names[old_code]
    
    return {"success": True}

@app.get("/")
def home():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE)
    return {"status": "Server running", "index": "not found"}

@app.get("/health")
async def health():
    db_type = "mysql" if pool else ("sqlite" if sqlite_conn else "none")
    return {
        "status": "ok",
        "online": len(online_users),
        "db": db_type
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)