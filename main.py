"""
پیام‌رسان صوتی - نسخه کامل با تماس گروهی بهبود یافته
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

# ========== کدهای ویژه (قابل تغییر توسط ادمین) ==========
ADMIN_CODE = "1361649093"
SUPPORT_CODE = "13901390"  # کد پشتیبانی - قابل تغییر
SUPPORT_PASSWORD = "mamad1390"  # رمز پشتیبانی - قابل تغییر

# ========== ذخیره‌سازی ==========
db = {
    "users": {},
    "bans": {}
}

def load_db():
    global db
    try:
        if DATA_FILE.exists():
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                db = json.load(f)
            print(f"✅ Loaded {len(db.get('users', {}))} users")
    except Exception as e:
        print(f"⚠️ Load error: {e}")
        db = {"users": {}, "bans": {}}
    
    # اکانت پشتیبانی - همیشه چک و آپدیت شود
    support_hash = hashlib.sha256(SUPPORT_PASSWORD.encode()).hexdigest()
    db.setdefault("users", {})[SUPPORT_CODE] = {
        "code": SUPPORT_CODE,
        "name": "پشتیبانی",
        "country": "IR",
        "password_hash": support_hash,
        "created_at": datetime.now().isoformat()
    }
    save_db()
    print(f"✅ Support account ready: {SUPPORT_CODE} / {SUPPORT_PASSWORD}")

def save_db():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ Save error: {e}")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# ========== آنلاین و تماس ==========
online_users: Dict[str, WebSocket] = {}
user_names: Dict[str, str] = {}
active_calls: Dict[str, dict] = {}  # تماس‌های خصوصی

# تماس‌های گروهی: group_code -> {"members": set(), "starter": str, "active": bool}
group_calls: Dict[str, dict] = {}

# ========== FastAPI ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_db()
    print("🚀 Server started")
    yield
    save_db()
    print("👋 Server stopped")

app = FastAPI(lifespan=lifespan)

# ========== Connection Manager ==========
class ConnectionManager:
    
    async def connect(self, ws: WebSocket, code: str, name: str):
        await ws.accept()
        
        # ذخیره اتصال
        old_ws = online_users.get(code)
        online_users[code] = ws
        user_names[code] = name
        
        print(f"[+] {name} ({code}) connected. Online: {len(online_users)}")
        
        # اطلاع به همه
        await self.broadcast_status(code, True, name)
    
    async def disconnect(self, code: str):
        if code in online_users:
            del online_users[code]
        
        name = user_names.pop(code, "کاربر")
        print(f"[-] {name} ({code}) disconnected. Online: {len(online_users)}")
        
        # پایان تماس
        if code in active_calls:
            other = active_calls[code].get("other")
            del active_calls[code]
            if other and other in active_calls:
                del active_calls[other]
            if other:
                await self.send_to(other, {"type": "call_ended"})
        
        # خروج از تماس گروهی
        for group_code in list(group_calls.keys()):
            members = group_calls[group_code].get("members", set())
            if code in members:
                members.discard(code)
                await self.broadcast_to_call(group_code, {
                    "type": "call_member_left",
                    "code": code
                }, exclude=code)
                
                # اگر تماس خالی شد حذفش کن
                if not members:
                    del group_calls[group_code]
        
        # اطلاع به همه
        await self.broadcast_status(code, False, name)
    
    async def send_to(self, code: str, data: dict) -> bool:
        """ارسال به یک کاربر"""
        if code in online_users:
            try:
                await online_users[code].send_json(data)
                print(f"📤 Sent to {code}: {data.get('type')}")
                return True
            except Exception as e:
                print(f"❌ Send error to {code}: {e}")
                return False
        else:
            print(f"⚠️ User {code} not online")
            return False
    
    async def send_audio(self, code: str, data: bytes) -> bool:
        """ارسال صدا"""
        if code in online_users:
            try:
                await online_users[code].send_bytes(data)
                return True
            except:
                return False
        return False
    
    async def broadcast_status(self, code: str, online: bool, name: str):
        """اطلاع وضعیت به همه"""
        msg = {
            "type": "contact_status",
            "code": code,
            "online": online,
            "name": name
        }
        
        tasks = []
        for user_code, ws in list(online_users.items()):
            if user_code != code:
                try:
                    tasks.append(ws.send_json(msg))
                except:
                    pass
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        print(f"📡 Broadcast status: {name} is {'online' if online else 'offline'}")
    
    async def broadcast_to_call(self, group_code: str, data: dict, exclude: str = None):
        """ارسال به اعضای تماس گروهی"""
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
    
    async def broadcast_to_group_members(self, group_code: str, data: dict, exclude: str = None):
        """ارسال به همه اعضای گروه (نه فقط تماس)"""
        members = get_group_members(group_code)
        tasks = []
        for m in members:
            if m["code"] != exclude and m["code"] in online_users:
                try:
                    tasks.append(online_users[m["code"]].send_json(data))
                except:
                    pass
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

manager = ConnectionManager()

# ========== WebSocket ==========
@app.websocket("/ws/{code}/{name}")
async def websocket_endpoint(ws: WebSocket, code: str, name: str):
    # چک بن
    if code in db.get("bans", {}):
        ban = db["bans"][code]
        if ban.get("is_permanent") or (ban.get("until") and datetime.fromisoformat(ban["until"]) > datetime.now()):
            await ws.accept()
            await ws.send_json({"type": "banned", "reason": ban.get("reason", "")})
            await ws.close()
            return
    
    await manager.connect(ws, code, name)
    
    try:
        while True:
            msg = await ws.receive()
            
            if "bytes" in msg:
                # صدا
                audio = msg["bytes"]
                
                # تماس خصوصی
                if code in active_calls:
                    other = active_calls[code].get("other")
                    if other:
                        await manager.send_audio(other, audio)
                
                # تماس گروهی
                for gc, members in group_calls.items():
                    if code in members:
                        for m in members:
                            if m != code:
                                await manager.send_audio(m, audio)
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
        print(f"[!] Error for {code}: {e}")
        await manager.disconnect(code)

async def handle_message(sender: str, data: dict):
    msg_type = data.get("type")
    sender_name = user_names.get(sender, "کاربر")
    
    print(f"📨 From {sender}: {msg_type}")
    
    # ========== Sync ==========
    if msg_type == "sync":
        # ارسال وضعیت مخاطبین
        contacts = data.get("contacts", [])
        for c in contacts:
            is_online = c in online_users
            c_name = user_names.get(c) or db.get("users", {}).get(c, {}).get("name", "کاربر")
            await manager.send_to(sender, {
                "type": "contact_status",
                "code": c,
                "online": is_online,
                "name": c_name
            })
    
    # ========== پیام خصوصی ==========
    elif msg_type == "message":
        to = data.get("to")
        text = data.get("text", "")[:2000]
        msg_id = data.get("id", str(datetime.now().timestamp()))
        
        if not to or not text:
            return
        
        # ارسال به گیرنده
        sent = await manager.send_to(to, {
            "type": "message",
            "id": msg_id,
            "from": sender,
            "senderName": sender_name,
            "text": text,
            "time": datetime.now().timestamp() * 1000
        })
        
        print(f"💬 Message from {sender} to {to}: {text[:50]}... (sent: {sent})")
    
    # ========== ویرایش پیام ==========
    elif msg_type == "edit_message":
        to = data.get("to")
        msg_id = data.get("id")
        text = data.get("text", "")
        is_group = data.get("isGroup", False)
        
        if is_group:
            # broadcast به گروه
            members = get_group_members(to)
            for m in members:
                if m["code"] != sender:
                    await manager.send_to(m["code"], {
                        "type": "message_edited",
                        "id": msg_id,
                        "text": text,
                        "groupCode": to
                    })
        else:
            await manager.send_to(to, {
                "type": "message_edited",
                "id": msg_id,
                "text": text,
                "from": sender
            })
    
    # ========== حذف پیام ==========
    elif msg_type == "delete_message":
        to = data.get("to")
        msg_id = data.get("id")
        is_group = data.get("isGroup", False)
        
        if is_group:
            members = get_group_members(to)
            for m in members:
                if m["code"] != sender:
                    await manager.send_to(m["code"], {
                        "type": "message_deleted",
                        "id": msg_id,
                        "groupCode": to
                    })
        else:
            await manager.send_to(to, {
                "type": "message_deleted",
                "id": msg_id,
                "from": sender
            })
    
    # ========== پیام گروهی ==========
    elif msg_type == "group_message":
        group_code = data.get("to")
        text = data.get("text", "")[:2000]
        msg_id = data.get("id", str(datetime.now().timestamp()))
        
        members = get_group_members(group_code)
        
        for m in members:
            if m["code"] != sender:
                await manager.send_to(m["code"], {
                    "type": "group_message",
                    "id": msg_id,
                    "groupCode": group_code,
                    "from": sender,
                    "senderName": sender_name,
                    "text": text,
                    "time": datetime.now().timestamp() * 1000
                })
        
        print(f"👪 Group message to {group_code} from {sender}")
    
    # ========== مدیا ==========
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
    
    elif msg_type == "group_media":
        group_code = data.get("to")
        members = get_group_members(group_code)
        
        for m in members:
            if m["code"] != sender:
                await manager.send_to(m["code"], {
                    "type": "media",
                    "id": data.get("id"),
                    "groupCode": group_code,
                    "from": sender,
                    "senderName": sender_name,
                    "mediaType": data.get("mediaType"),
                    "mediaData": data.get("mediaData"),
                    "duration": data.get("duration"),
                    "time": datetime.now().timestamp() * 1000
                })
    
    # ========== مخاطب ==========
    elif msg_type == "add_contact":
        contact_code = data.get("code")
        if contact_code in online_users:
            await manager.send_to(sender, {
                "type": "contact_status",
                "code": contact_code,
                "online": True,
                "name": user_names.get(contact_code, "کاربر")
            })
        elif contact_code in db.get("users", {}):
            await manager.send_to(sender, {
                "type": "user_info",
                "code": contact_code,
                "name": db["users"][contact_code].get("name", "کاربر")
            })
    
    # ========== بلاک ==========
    elif msg_type == "block":
        # اطلاع به کاربر بلاک شده که نمی‌تواند پیام دهد
        pass
    
    # ========== گروه ==========
    elif msg_type == "create_group":
        # گروه در localStorage کلاینت ذخیره می‌شود
        group = data.get("group", {})
        print(f"👪 Group created: {group.get('name')} by {sender}")
    
    elif msg_type == "join_group":
        query = data.get("query", "")
        # اطلاعات گروه را برگردان
        await manager.send_to(sender, {
            "type": "group_info",
            "group": {
                "code": query,
                "name": f"گروه {query}",
                "members": []
            }
        })
    
    elif msg_type == "add_member":
        group_code = data.get("groupCode")
        member_code = data.get("memberCode")
        
        # اطلاع به عضو جدید
        await manager.send_to(member_code, {
            "type": "group_info",
            "group": {
                "code": group_code,
                "name": data.get("groupName", "گروه"),
                "members": data.get("members", [])
            }
        })
    
    elif msg_type == "kick_member":
        member_code = data.get("memberCode")
        group_code = data.get("groupCode")
        
        await manager.send_to(member_code, {
            "type": "kicked",
            "groupCode": group_code,
            "groupName": data.get("groupName", "گروه")
        })
    
    # ========== تماس ==========
    elif msg_type == "call_request":
        to = data.get("to")
        
        # ذخیره تماس
        active_calls[sender] = {"other": to, "status": "ringing"}
        
        # ارسال به گیرنده
        await manager.send_to(to, {
            "type": "incoming_call",
            "callerCode": sender,
            "callerName": sender_name
        })
        
        # اطلاع به تماس‌گیرنده
        await manager.send_to(sender, {
            "type": "call_ringing",
            "to": to
        })
        
        print(f"📞 Call request: {sender} -> {to}")
    
    elif msg_type == "call_accept":
        to = data.get("to")
        
        active_calls[sender] = {"other": to, "status": "connected"}
        active_calls[to] = {"other": sender, "status": "connected"}
        
        await manager.send_to(to, {"type": "call_accepted"})
        
        print(f"📞 Call connected: {sender} <-> {to}")
    
    elif msg_type == "call_reject":
        to = data.get("to")
        
        if sender in active_calls:
            del active_calls[sender]
        if to in active_calls:
            del active_calls[to]
        
        await manager.send_to(to, {"type": "call_rejected"})
        
        print(f"📵 Call rejected: {to} rejected {sender}")
    
    elif msg_type == "call_end":
        to = data.get("to")
        
        if sender in active_calls:
            del active_calls[sender]
        if to in active_calls:
            del active_calls[to]
        
        await manager.send_to(to, {"type": "call_ended"})
        
        print(f"📵 Call ended: {sender} <-> {to}")
    
    # ========== تماس گروهی ==========
    elif msg_type == "group_call":
        group_code = data.get("to")
        
        # چک کنیم آیا تماس گروهی فعال وجود دارد
        if group_code in group_calls and group_calls[group_code].get("active"):
            # تماس فعال هست - به آن ملحق شو
            group_calls[group_code]["members"].add(sender)
            
            # اطلاع به بقیه اعضای تماس
            await manager.broadcast_to_call(group_code, {
                "type": "call_member_joined",
                "code": sender,
                "name": sender_name
            }, exclude=sender)
            
            # ارسال لیست اعضای فعلی به کاربر جدید
            for m in group_calls[group_code]["members"]:
                if m != sender:
                    await manager.send_to(sender, {
                        "type": "call_member_joined",
                        "code": m,
                        "name": user_names.get(m, "کاربر")
                    })
            
            # اطلاع به کاربر که تماس قبول شده
            await manager.send_to(sender, {"type": "call_accepted"})
            
            print(f"📞 {sender_name} joined existing group call: {group_code}")
        else:
            # تماس جدید ایجاد کن
            group_calls[group_code] = {
                "members": {sender},
                "starter": sender,
                "active": True
            }
            
            # اطلاع به همه اعضای گروه (نه فقط تماس)
            members = get_group_members(group_code)
            for m in members:
                if m["code"] != sender and m["code"] in online_users:
                    await manager.send_to(m["code"], {
                        "type": "incoming_call",
                        "callerCode": sender,
                        "callerName": sender_name,
                        "groupCode": group_code,
                        "groupName": data.get("groupName", "گروه"),
                        "isGroup": True
                    })
            
            # به تماس‌گیرنده بگو در حال زنگ زدن
            await manager.send_to(sender, {
                "type": "call_ringing",
                "to": group_code,
                "isGroup": True
            })
            
            print(f"📞 Group call started: {group_code} by {sender}")
    
    elif msg_type == "join_group_call":
        group_code = data.get("to")
        
        if group_code not in group_calls:
            group_calls[group_code] = {
                "members": set(),
                "starter": sender,
                "active": True
            }
        
        group_calls[group_code]["members"].add(sender)
        
        # اطلاع به بقیه اعضای تماس
        await manager.broadcast_to_call(group_code, {
            "type": "call_member_joined",
            "code": sender,
            "name": sender_name
        }, exclude=sender)
        
        # ارسال لیست اعضا به کاربر جدید
        for m in group_calls[group_code]["members"]:
            if m != sender:
                await manager.send_to(sender, {
                    "type": "call_member_joined",
                    "code": m,
                    "name": user_names.get(m, "کاربر")
                })
        
        # اطلاع به شروع‌کننده تماس که کسی جواب داده
        starter = group_calls[group_code].get("starter")
        if starter and starter != sender:
            await manager.send_to(starter, {"type": "call_accepted"})
        
        print(f"📞 {sender_name} joined group call: {group_code}")
    
    elif msg_type == "reject_group_call":
        # رد تماس گروهی - فقط برای این کاربر، تماس ادامه دارد
        group_code = data.get("to")
        print(f"📵 {sender_name} rejected group call: {group_code}")
        # هیچ کاری نمی‌کنیم - تماس برای بقیه ادامه دارد
    
    elif msg_type == "leave_group_call":
        group_code = data.get("to")
        
        if group_code in group_calls:
            group_calls[group_code]["members"].discard(sender)
            
            await manager.broadcast_to_call(group_code, {
                "type": "call_member_left",
                "code": sender
            })
            
            # اگر هیچ‌کس در تماس نمانده، تماس را حذف کن
            if not group_calls[group_code]["members"]:
                del group_calls[group_code]
                print(f"📵 Group call ended: {group_code}")
            else:
                print(f"📵 {sender_name} left group call: {group_code}")

def get_group_members(group_code: str) -> List[dict]:
    """دریافت اعضای گروه - از کلاینت‌ها sync می‌شود"""
    # فعلاً همه آنلاین‌ها را برمی‌گرداند
    # در نسخه بعدی باید از دیتابیس بخوانیم
    return [{"code": c, "name": n} for c, n in user_names.items()]

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
    
    if code in db.get("users", {}):
        raise HTTPException(400, "این کد قبلاً ثبت شده")
    
    db.setdefault("users", {})[code] = {
        "code": code,
        "name": name,
        "country": country,
        "password_hash": hash_password(password),
        "created_at": datetime.now().isoformat()
    }
    save_db()
    
    print(f"✅ New user: {name} ({code})")
    return {"success": True, "code": code}

@app.post("/api/login")
async def login(data: dict):
    code = data.get("code", "")
    password = data.get("password", "")
    
    # ادمین
    if code == ADMIN_CODE:
        return {"success": True, "isAdmin": True}
    
    # چک بن
    if code in db.get("bans", {}):
        ban = db["bans"][code]
        if ban.get("is_permanent"):
            raise HTTPException(403, f"شما بن دائمی شده‌اید: {ban.get('reason', '')}")
        if ban.get("until"):
            until = datetime.fromisoformat(ban["until"])
            if until > datetime.now():
                raise HTTPException(403, f"شما تا {until.strftime('%Y-%m-%d %H:%M')} بن هستید")
            else:
                del db["bans"][code]
                save_db()
    
    # چک کاربر
    user = db.get("users", {}).get(code)
    if not user:
        raise HTTPException(401, "کاربری با این کد وجود ندارد")
    
    if user.get("password_hash") != hash_password(password):
        raise HTTPException(401, "رمز اشتباه است")
    
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
    if admin_key != ADMIN_CODE:
        raise HTTPException(403, "دسترسی ندارید")
    
    users = []
    for code, user in db.get("users", {}).items():
        ban = db.get("bans", {}).get(code)
        users.append({
            "code": code,
            "name": user.get("name", ""),
            "country": user.get("country", ""),
            "online": code in online_users,
            "banned": ban is not None,
            "ban_info": ban
        })
    
    return {
        "users": users,
        "total": len(users),
        "online": len(online_users)
    }

@app.post("/api/admin/ban")
async def admin_ban(admin_key: str = "", user_code: str = "", duration: int = 0, reason: str = ""):
    if admin_key != ADMIN_CODE:
        raise HTTPException(403, "دسترسی ندارید")
    
    ban_data = {
        "reason": reason,
        "banned_at": datetime.now().isoformat()
    }
    
    if duration == 0:
        ban_data["is_permanent"] = True
    else:
        ban_data["until"] = (datetime.now() + timedelta(hours=duration)).isoformat()
    
    db.setdefault("bans", {})[user_code] = ban_data
    save_db()
    
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
    if admin_key != ADMIN_CODE:
        raise HTTPException(403, "دسترسی ندارید")
    
    if user_code in db.get("bans", {}):
        del db["bans"][user_code]
        save_db()
    
    return {"success": True}

@app.get("/")
def home():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE)
    return {"status": "Server running", "index": "not found"}

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "online": len(online_users),
        "users": len(db.get("users", {}))
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)