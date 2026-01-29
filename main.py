"""
پیام‌رسان صوتی - نسخه ساده با JSON
بدون نیاز به دیتابیس - فقط فایل JSON
"""

import os
import json
import asyncio
import hashlib
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
DATA_FILE = BASE_DIR / "data.json"

# کدهای ویژه
ADMIN_CODE = "1361649093"
SUPPORT_CODE = "13901390"
SUPPORT_PASSWORD = "mamad1390"

# ========== ذخیره‌سازی JSON ==========
json_data = {
    "users": {},
    "bans": {}
}

def load_json_data():
    """بارگذاری داده‌ها از فایل JSON"""
    global json_data
    try:
        if DATA_FILE.exists():
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
                print(f"✅ Data loaded: {len(json_data.get('users', {}))} users")
    except Exception as e:
        print(f"⚠️ Error loading data: {e}")
        json_data = {"users": {}, "bans": {}}
    
    # اضافه کردن اکانت پشتیبانی اگر وجود ندارد
    if SUPPORT_CODE not in json_data.get("users", {}):
        json_data.setdefault("users", {})[SUPPORT_CODE] = {
            "code": SUPPORT_CODE,
            "name": "پشتیبانی",
            "country": "IR",
            "password_hash": hashlib.sha256(SUPPORT_PASSWORD.encode()).hexdigest(),
            "created_at": datetime.now().isoformat()
        }
        save_json_data()
        print(f"✅ Support account created: {SUPPORT_CODE}")

def save_json_data():
    """ذخیره داده‌ها در فایل JSON"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        print(f"💾 Data saved: {len(json_data.get('users', {}))} users")
    except Exception as e:
        print(f"❌ Error saving data: {e}")

# ========== توابع دیتابیس (JSON) ==========

async def init_db():
    """بارگذاری داده‌ها"""
    load_json_data()
    print("✅ JSON storage ready")


async def close_db():
    """ذخیره نهایی داده‌ها"""
    save_json_data()
    print("✅ Data saved on shutdown")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """مدیریت چرخه حیات اپلیکیشن"""
    await init_db()
    yield
    await close_db()


app = FastAPI(title="Voice Messenger", lifespan=lifespan)


def hash_password(password: str) -> str:
    """هش کردن رمز عبور"""
    return hashlib.sha256(password.encode()).hexdigest()


# ========== توابع دیتابیس (JSON) ==========

async def get_user(code: str) -> Optional[dict]:
    """دریافت اطلاعات کاربر"""
    return json_data.get("users", {}).get(code)


async def create_user(code: str, name: str, country: str, password: str) -> bool:
    """ایجاد کاربر جدید"""
    if code in json_data.get("users", {}):
        return False
    
    json_data.setdefault("users", {})[code] = {
        "code": code,
        "name": name,
        "country": country,
        "password_hash": hash_password(password),
        "created_at": datetime.now().isoformat()
    }
    save_json_data()
    return True


async def verify_user(code: str, password: str) -> Optional[dict]:
    """تایید کاربر با رمز"""
    user = json_data.get("users", {}).get(code)
    if user and user.get("password_hash") == hash_password(password):
        return user
    return None


async def is_banned(code: str) -> Optional[dict]:
    """بررسی بن بودن کاربر"""
    ban = json_data.get("bans", {}).get(code)
    if not ban:
        return None
    
    if ban.get('is_permanent'):
        return {"type": "permanent", "reason": ban.get('reason', '')}
    
    until = ban.get('until_date')
    if until:
        until_dt = datetime.fromisoformat(until) if isinstance(until, str) else until
        if datetime.now() < until_dt:
            return {"type": "temporary", "until": str(until), "reason": ban.get('reason', '')}
        else:
            del json_data["bans"][code]
            save_json_data()
            return None
    
    return None


async def is_blocked(user_code: str, target_code: str) -> bool:
    """بررسی بلاک بودن - از localStorage کلاینت استفاده می‌شود"""
    return False


async def get_group_members(group_code: str) -> List[dict]:
    """دریافت اعضای گروه"""
    members = []
    if db_pool:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT u.code, u.name FROM group_members gm
                JOIN users u ON gm.user_code = u.code
                WHERE gm.group_code = $1
            """, group_code)
            members = [dict(row) for row in rows]
    return members


# ========== متغیرهای آنلاین ==========

online_users: Dict[str, WebSocket] = {}
user_names: Dict[str, str] = {}
active_calls: Dict[str, dict] = {}
group_calls: Dict[str, Set[str]] = {}
pending_messages: Dict[str, List[dict]] = defaultdict(list)


class ConnectionManager:
    
    async def connect(self, websocket: WebSocket, user_code: str, user_name: str):
        await websocket.accept()
        online_users[user_code] = websocket
        user_names[user_code] = user_name
        print(f"[+] {user_name} ({user_code}) connected. Online: {len(online_users)}")
        
        # ارسال پیام‌های معلق
        if user_code in pending_messages:
            for msg in pending_messages[user_code]:
                try:
                    await websocket.send_json(msg)
                except:
                    pass
            del pending_messages[user_code]
        
        # اطلاع به مخاطبین
        await self.notify_status(user_code, True)
    
    async def disconnect(self, user_code: str):
        if user_code in online_users:
            del online_users[user_code]
        if user_code in user_names:
            name = user_names[user_code]
            del user_names[user_code]
            print(f"[-] {name} ({user_code}) disconnected. Online: {len(online_users)}")
        
        # پایان تماس‌ها
        if user_code in active_calls:
            call = active_calls[user_code]
            other = call.get('other')
            if other:
                del active_calls[user_code]
                if other in active_calls:
                    del active_calls[other]
                await self.send_to_user(other, {"type": "call_ended"})
        
        # خروج از تماس گروهی
        for group_code, members in list(group_calls.items()):
            if user_code in members:
                members.discard(user_code)
                await self.broadcast_to_group_call(group_code, {
                    "type": "call_member_left",
                    "code": user_code
                }, exclude=user_code)
                if not members:
                    del group_calls[group_code]
        
        await self.notify_status(user_code, False)
    
    async def notify_status(self, user_code: str, online: bool):
        user = await get_user(user_code)
        user_name = user['name'] if user else 'کاربر'
        for code in online_users:
            if code != user_code:
                await self.send_to_user(code, {
                    "type": "contact_status",
                    "code": user_code,
                    "online": online,
                    "name": user_name
                })
    
    async def send_to_user(self, user_code: str, message: dict) -> bool:
        # چک بن
        if await is_banned(user_code):
            return False
        
        if user_code in online_users:
            try:
                await online_users[user_code].send_json(message)
                return True
            except:
                return False
        else:
            if message.get('type') in ['message', 'group_message', 'media', 'incoming_call']:
                pending_messages[user_code].append(message)
            return False
    
    async def send_audio_to_user(self, user_code: str, audio_data: bytes) -> bool:
        if user_code in online_users:
            try:
                await online_users[user_code].send_bytes(audio_data)
                return True
            except:
                return False
        return False
    
    async def broadcast_to_group(self, group_code: str, message: dict, exclude: str = None):
        members = await get_group_members(group_code)
        for member in members:
            if member['code'] != exclude:
                await self.send_to_user(member['code'], message)
    
    async def broadcast_audio_to_group(self, group_code: str, audio_data: bytes, exclude: str = None):
        if group_code not in group_calls:
            return
        
        for member_code in group_calls[group_code]:
            if member_code != exclude:
                await self.send_audio_to_user(member_code, audio_data)
    
    async def broadcast_to_group_call(self, group_code: str, message: dict, exclude: str = None):
        if group_code not in group_calls:
            return
        
        for member_code in group_calls[group_code]:
            if member_code != exclude:
                await self.send_to_user(member_code, message)


manager = ConnectionManager()


# ========== WebSocket ==========

@app.websocket("/ws/{user_code}/{user_name}")
async def websocket_endpoint(websocket: WebSocket, user_code: str, user_name: str):
    # چک بن
    ban_info = await is_banned(user_code)
    if ban_info:
        await websocket.accept()
        await websocket.send_json({"type": "banned", "info": ban_info})
        await websocket.close()
        return
    
    await manager.connect(websocket, user_code, user_name)
    
    try:
        while True:
            message = await websocket.receive()
            
            if "bytes" in message:
                audio_data = message["bytes"]
                
                if user_code in active_calls:
                    call = active_calls[user_code]
                    other = call.get('other')
                    if other:
                        await manager.send_audio_to_user(other, audio_data)
                
                for group_code, members in group_calls.items():
                    if user_code in members:
                        await manager.broadcast_audio_to_group(group_code, audio_data, exclude=user_code)
                        break
            
            elif "text" in message:
                try:
                    data = json.loads(message["text"])
                    await handle_message(user_code, data)
                except json.JSONDecodeError:
                    pass
                    
    except WebSocketDisconnect:
        await manager.disconnect(user_code)
    except Exception as e:
        print(f"[!] Error for {user_code}: {e}")
        await manager.disconnect(user_code)


async def handle_message(user_code: str, data: dict):
    msg_type = data.get("type")
    user = await get_user(user_code)
    user_name = user['name'] if user else 'کاربر'
    
    # ========== همگام‌سازی ==========
    if msg_type == "sync":
        contacts = data.get("contacts", [])
        for code in contacts:
            is_online = code in online_users
            contact_user = await get_user(code)
            await manager.send_to_user(user_code, {
                "type": "contact_status",
                "code": code,
                "online": is_online,
                "name": contact_user['name'] if contact_user else 'کاربر'
            })
        
        await manager.notify_status(user_code, True)
    
    # ========== پیام خصوصی ==========
    elif msg_type == "message":
        to_code = data.get("to")
        text = data.get("text", "")[:2000]
        msg_id = data.get("id", str(datetime.now().timestamp()))
        
        if not to_code or not text:
            return
        
        # چک بلاک
        if await is_blocked(user_code, to_code):
            return
        
        msg_data = {
            "type": "message",
            "id": msg_id,
            "from": user_code,
            "senderName": user_name,
            "text": text,
            "time": datetime.now().timestamp() * 1000
        }
        
        # ذخیره پیام
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO messages (id, from_code, to_code, content)
                    VALUES ($1, $2, $3, $4)
                """, msg_id, user_code, to_code, text)
        
        await manager.send_to_user(to_code, msg_data)
    
    # ========== ویرایش پیام ==========
    elif msg_type == "edit_message":
        to_code = data.get("to")
        msg_id = data.get("id")
        new_text = data.get("text", "")[:2000]
        is_group = data.get("isGroup", False)
        
        if not msg_id or not new_text:
            return
        
        # آپدیت در دیتابیس
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    UPDATE messages SET content = $1, is_edited = TRUE
                    WHERE id = $2 AND from_code = $3
                """, new_text, msg_id, user_code)
        
        if is_group:
            await manager.broadcast_to_group(to_code, {
                "type": "message_edited",
                "id": msg_id,
                "text": new_text,
                "groupCode": to_code
            }, exclude=user_code)
        else:
            await manager.send_to_user(to_code, {
                "type": "message_edited",
                "id": msg_id,
                "text": new_text,
                "from": user_code
            })
    
    # ========== حذف پیام ==========
    elif msg_type == "delete_message":
        to_code = data.get("to")
        msg_id = data.get("id")
        is_group = data.get("isGroup", False)
        
        if not msg_id:
            return
        
        # حذف از دیتابیس
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    DELETE FROM messages WHERE id = $1 AND from_code = $2
                """, msg_id, user_code)
        
        if is_group:
            await manager.broadcast_to_group(to_code, {
                "type": "message_deleted",
                "id": msg_id,
                "groupCode": to_code
            }, exclude=user_code)
        else:
            await manager.send_to_user(to_code, {
                "type": "message_deleted",
                "id": msg_id,
                "from": user_code
            })
    
    # ========== پیام گروهی ==========
    elif msg_type == "group_message":
        group_code = data.get("to")
        text = data.get("text", "")[:2000]
        msg_id = data.get("id", str(datetime.now().timestamp()))
        
        if not group_code or not text:
            return
        
        msg_data = {
            "type": "group_message",
            "id": msg_id,
            "groupCode": group_code,
            "from": user_code,
            "senderName": user_name,
            "text": text,
            "time": datetime.now().timestamp() * 1000
        }
        
        # ذخیره پیام
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO messages (id, from_code, group_code, content)
                    VALUES ($1, $2, $3, $4)
                """, msg_id, user_code, group_code, text)
        
        await manager.broadcast_to_group(group_code, msg_data, exclude=user_code)
    
    # ========== مدیا ==========
    elif msg_type == "media":
        to_code = data.get("to")
        media_type = data.get("mediaType")
        media_data = data.get("mediaData")
        duration = data.get("duration")
        msg_id = data.get("id", str(datetime.now().timestamp()))
        
        if not to_code or not media_type or not media_data:
            return
        
        # چک بلاک
        if await is_blocked(user_code, to_code):
            return
        
        await manager.send_to_user(to_code, {
            "type": "media",
            "id": msg_id,
            "from": user_code,
            "senderName": user_name,
            "mediaType": media_type,
            "mediaData": media_data,
            "duration": duration,
            "time": datetime.now().timestamp() * 1000
        })
    
    elif msg_type == "group_media":
        group_code = data.get("to")
        media_type = data.get("mediaType")
        media_data = data.get("mediaData")
        duration = data.get("duration")
        msg_id = data.get("id", str(datetime.now().timestamp()))
        
        if not group_code or not media_type or not media_data:
            return
        
        await manager.broadcast_to_group(group_code, {
            "type": "media",
            "id": msg_id,
            "groupCode": group_code,
            "from": user_code,
            "senderName": user_name,
            "mediaType": media_type,
            "mediaData": media_data,
            "duration": duration,
            "time": datetime.now().timestamp() * 1000
        }, exclude=user_code)
    
    # ========== مخاطب ==========
    elif msg_type == "add_contact":
        code = data.get("code")
        if code and code in online_users:
            contact_user = await get_user(code)
            await manager.send_to_user(user_code, {
                "type": "contact_status",
                "code": code,
                "online": True,
                "name": contact_user['name'] if contact_user else 'کاربر'
            })
        elif code:
            contact_user = await get_user(code)
            if contact_user:
                await manager.send_to_user(user_code, {
                    "type": "user_info",
                    "code": code,
                    "name": contact_user['name']
                })
    
    # ========== گروه ==========
    elif msg_type == "create_group":
        group_data = data.get("group", {})
        group_code = group_data.get("code")
        group_name = group_data.get("name")
        
        if not group_code or not group_name:
            return
        
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO groups (code, name, owner_code)
                    VALUES ($1, $2, $3)
                """, group_code, group_name, user_code)
                
                await conn.execute("""
                    INSERT INTO group_members (group_code, user_code, is_owner)
                    VALUES ($1, $2, TRUE)
                """, group_code, user_code)
        
        print(f"[+] Group created: {group_name} ({group_code})")
    
    elif msg_type == "join_group":
        query = data.get("query", "")
        
        if db_pool:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT * FROM groups WHERE code = $1 OR LOWER(name) = LOWER($2)
                """, query, query)
                
                if row:
                    group = dict(row)
                    # چک عضویت قبلی
                    existing = await conn.fetchval("""
                        SELECT 1 FROM group_members 
                        WHERE group_code = $1 AND user_code = $2
                    """, group['code'], user_code)
                    
                    if not existing:
                        await conn.execute("""
                            INSERT INTO group_members (group_code, user_code, is_owner)
                            VALUES ($1, $2, FALSE)
                        """, group['code'], user_code)
                    
                    members = await get_group_members(group['code'])
                    
                    await manager.send_to_user(user_code, {
                        "type": "group_info",
                        "group": {
                            "code": group['code'],
                            "name": group['name'],
                            "members": members,
                            "isOwner": group['owner_code'] == user_code
                        }
                    })
                    
                    # اطلاع به بقیه
                    await manager.broadcast_to_group(group['code'], {
                        "type": "group_updated",
                        "group": {
                            "code": group['code'],
                            "name": group['name'],
                            "members": members
                        }
                    }, exclude=user_code)
                else:
                    await manager.send_to_user(user_code, {
                        "type": "group_info",
                        "error": "گروه یافت نشد"
                    })
    
    elif msg_type == "leave_group":
        group_code = data.get("groupCode")
        
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    DELETE FROM group_members 
                    WHERE group_code = $1 AND user_code = $2
                """, group_code, user_code)
                
                # چک اگر گروه خالی شد
                count = await conn.fetchval("""
                    SELECT COUNT(*) FROM group_members WHERE group_code = $1
                """, group_code)
                
                if count == 0:
                    await conn.execute("DELETE FROM groups WHERE code = $1", group_code)
                else:
                    members = await get_group_members(group_code)
                    await manager.broadcast_to_group(group_code, {
                        "type": "group_updated",
                        "group": {"code": group_code, "members": members}
                    })
    
    elif msg_type == "add_member":
        group_code = data.get("groupCode")
        member_code = data.get("memberCode")
        
        if db_pool:
            async with db_pool.acquire() as conn:
                # چک مالک بودن
                is_owner = await conn.fetchval("""
                    SELECT 1 FROM group_members 
                    WHERE group_code = $1 AND user_code = $2 AND is_owner = TRUE
                """, group_code, user_code)
                
                if not is_owner:
                    return
                
                # چک وجود کاربر
                target_user = await get_user(member_code)
                if not target_user:
                    return
                
                # اضافه کردن
                try:
                    await conn.execute("""
                        INSERT INTO group_members (group_code, user_code, is_owner)
                        VALUES ($1, $2, FALSE)
                    """, group_code, member_code)
                except:
                    return
                
                group = await conn.fetchrow("SELECT * FROM groups WHERE code = $1", group_code)
                members = await get_group_members(group_code)
                
                # اطلاع به عضو جدید
                await manager.send_to_user(member_code, {
                    "type": "group_info",
                    "group": {
                        "code": group_code,
                        "name": group['name'],
                        "members": members,
                        "isOwner": False
                    }
                })
                
                # اطلاع به بقیه
                await manager.broadcast_to_group(group_code, {
                    "type": "group_updated",
                    "group": {"code": group_code, "members": members}
                })
    
    elif msg_type == "kick_member":
        group_code = data.get("groupCode")
        member_code = data.get("memberCode")
        
        if db_pool:
            async with db_pool.acquire() as conn:
                # چک مالک بودن
                is_owner = await conn.fetchval("""
                    SELECT 1 FROM group_members 
                    WHERE group_code = $1 AND user_code = $2 AND is_owner = TRUE
                """, group_code, user_code)
                
                if not is_owner:
                    return
                
                await conn.execute("""
                    DELETE FROM group_members 
                    WHERE group_code = $1 AND user_code = $2
                """, group_code, member_code)
                
                group = await conn.fetchrow("SELECT * FROM groups WHERE code = $1", group_code)
                members = await get_group_members(group_code)
                
                await manager.send_to_user(member_code, {
                    "type": "kicked",
                    "groupCode": group_code,
                    "groupName": group['name']
                })
                
                await manager.broadcast_to_group(group_code, {
                    "type": "group_updated",
                    "group": {"code": group_code, "members": members}
                })
    
    # ========== تماس ==========
    elif msg_type == "call_request":
        to_code = data.get("to")
        if not to_code:
            return
        
        # چک بلاک
        if await is_blocked(user_code, to_code):
            await manager.send_to_user(user_code, {"type": "call_rejected"})
            return
        
        # ذخیره تماس pending
        active_calls[user_code] = {"other": to_code, "status": "ringing"}
        
        await manager.send_to_user(to_code, {
            "type": "incoming_call",
            "callerCode": user_code,
            "callerName": user_name
        })
        
        # ارسال وضعیت زنگ به تماس‌گیرنده
        await manager.send_to_user(user_code, {
            "type": "call_ringing",
            "to": to_code
        })
    
    elif msg_type == "call_accept":
        to_code = data.get("to")
        if not to_code:
            return
        
        active_calls[user_code] = {"other": to_code, "status": "connected"}
        active_calls[to_code] = {"other": user_code, "status": "connected"}
        
        await manager.send_to_user(to_code, {"type": "call_accepted"})
        print(f"[📞] Call: {user_code} <-> {to_code}")
    
    elif msg_type == "call_reject":
        to_code = data.get("to")
        if to_code:
            if user_code in active_calls:
                del active_calls[user_code]
            if to_code in active_calls:
                del active_calls[to_code]
            await manager.send_to_user(to_code, {"type": "call_rejected"})
    
    elif msg_type == "call_end":
        to_code = data.get("to")
        if to_code:
            if user_code in active_calls:
                del active_calls[user_code]
            if to_code in active_calls:
                del active_calls[to_code]
            await manager.send_to_user(to_code, {"type": "call_ended"})
            print(f"[📵] Call ended: {user_code} <-> {to_code}")
    
    # ========== تماس گروهی ==========
    elif msg_type == "group_call":
        group_code = data.get("to")
        
        members = await get_group_members(group_code)
        if not members:
            return
        
        if group_code not in group_calls:
            group_calls[group_code] = set()
        
        group_calls[group_code].add(user_code)
        
        # ذخیره اسم گروه
        if db_pool:
            async with db_pool.acquire() as conn:
                group = await conn.fetchrow("SELECT name FROM groups WHERE code = $1", group_code)
                group_name = group['name'] if group else 'گروه'
        else:
            group_name = 'گروه'
        
        for member in members:
            if member['code'] != user_code:
                await manager.send_to_user(member['code'], {
                    "type": "group_call_started",
                    "groupCode": group_code,
                    "groupName": group_name,
                    "callerName": user_name,
                    "isGroup": True
                })
        
        print(f"[📞] Group call started: {group_name}")
    
    elif msg_type == "join_group_call":
        group_code = data.get("to")
        if group_code not in group_calls:
            return
        
        group_calls[group_code].add(user_code)
        
        await manager.broadcast_to_group_call(group_code, {
            "type": "call_member_joined",
            "code": user_code,
            "name": user_name
        }, exclude=user_code)
        
        for member_code in group_calls[group_code]:
            if member_code != user_code:
                member_name = user_names.get(member_code, 'کاربر')
                await manager.send_to_user(user_code, {
                    "type": "call_member_joined",
                    "code": member_code,
                    "name": member_name
                })
    
    elif msg_type == "leave_group_call":
        group_code = data.get("to")
        if group_code in group_calls:
            group_calls[group_code].discard(user_code)
            
            await manager.broadcast_to_group_call(group_code, {
                "type": "call_member_left",
                "code": user_code
            })
            
            if not group_calls[group_code]:
                del group_calls[group_code]


# ========== API احراز هویت ==========

@app.post("/api/register")
async def register(data: dict):
    """ثبت‌نام کاربر جدید"""
    code = data.get("code")
    name = data.get("name", "")[:50]
    country = data.get("country", "")
    password = data.get("password", "")
    
    if not code or not name or not password:
        raise HTTPException(400, "اطلاعات ناقص است")
    
    if len(password) < 4:
        raise HTTPException(400, "رمز باید حداقل ۴ کاراکتر باشد")
    
    # چک کد تکراری
    existing = await get_user(code)
    if existing:
        raise HTTPException(400, "این کد قبلاً ثبت شده")
    
    success = await create_user(code, name, country, password)
    if not success:
        raise HTTPException(500, "خطا در ثبت‌نام")
    
    return {"success": True, "code": code}


@app.post("/api/login")
async def login(data: dict):
    """ورود کاربر"""
    code = data.get("code", "")
    password = data.get("password", "")
    
    # چک ادمین
    if code == ADMIN_CODE:
        return {"success": True, "isAdmin": True}
    
    # چک بن
    ban_info = await is_banned(code)
    if ban_info:
        raise HTTPException(403, f"شما مسدود شده‌اید: {ban_info.get('reason', '')}")
    
    user = await verify_user(code, password)
    if not user:
        raise HTTPException(401, "کد یا رمز اشتباه است")
    
    return {
        "success": True,
        "user": {
            "code": user['code'],
            "name": user['name'],
            "country": user.get('country', '')
        }
    }


# ========== API ادمین ==========

@app.get("/api/admin/users")
async def get_all_users(admin_key: str = ""):
    if admin_key != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    users = []
    for code, user in json_data.get("users", {}).items():
        ban = await is_banned(code)
        users.append({
            "code": code,
            "name": user.get('name', 'Unknown'),
            "country": user.get('country', ''),
            "created_at": user.get('created_at', ''),
            "online": code in online_users,
            "banned": ban is not None,
            "ban_info": ban
        })
    
    return {"users": users, "total": len(users), "online": len(online_users)}


@app.post("/api/admin/ban")
async def ban_user(admin_key: str = "", user_code: str = "", duration: int = 0, reason: str = ""):
    if admin_key != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if not user_code:
        raise HTTPException(status_code=400, detail="User code required")
    
    ban_data = {
        "is_permanent": duration == 0,
        "reason": reason,
        "banned_at": datetime.now().isoformat()
    }
    
    if duration > 0:
        ban_data["until_date"] = (datetime.now() + timedelta(hours=duration)).isoformat()
    
    json_data.setdefault("bans", {})[user_code] = ban_data
    save_json_data()
    
    # قطع اتصال کاربر
    if user_code in online_users:
        try:
            await online_users[user_code].send_json({
                "type": "banned",
                "info": await is_banned(user_code)
            })
            await online_users[user_code].close()
        except:
            pass
    
    return {"success": True, "message": f"User {user_code} banned"}


@app.post("/api/admin/unban")
async def unban_user(admin_key: str = "", user_code: str = ""):
    if admin_key != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if user_code in json_data.get("bans", {}):
        del json_data["bans"][user_code]
        save_json_data()
    
    return {"success": True, "message": f"User {user_code} unbanned"}


# ========== HTTP ==========

@app.get("/")
def home():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE)
    return JSONResponse({"status": "Server is running but index.html not found"})


@app.get("/manifest.json")
async def get_manifest():
    return JSONResponse({
        "name": "پیام‌رسان صوتی",
        "short_name": "پیام‌رسان",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "icons": [{"src": "/icon.png", "sizes": "192x192", "type": "image/png"}]
    })


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "database": "connected" if db_pool else "not connected",
        "online": len(online_users),
        "calls": len(active_calls) // 2,
        "group_calls": len(group_calls)
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Server starting on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)