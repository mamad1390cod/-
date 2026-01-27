"""
پیام‌رسان صوتی - نسخه کامل با پنل ادمین
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
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

app = FastAPI(title="Voice Messenger")

# مسیرهای فایل
BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
DATA_FILE = BASE_DIR / "data.json"

# کدهای ویژه
ADMIN_CODE = "1361649093"
SUPPORT_CODE = "13901390"
SUPPORT_TELEGRAM = "https://t.me/Mamad_NOX_YT"

# ========== مدیریت داده‌ها ==========

def load_data() -> dict:
    """بارگذاری داده‌ها از فایل"""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {
        "users": {},
        "groups": {},
        "messages": {},
        "bans": {}
    }

def save_data(data: dict):
    """ذخیره داده‌ها در فایل"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def hash_password(password: str) -> str:
    """هش کردن رمز عبور"""
    return hashlib.sha256(password.encode()).hexdigest()

# بارگذاری اولیه
db = load_data()

# ایجاد اکانت پشتیبانی اگر وجود ندارد
if SUPPORT_CODE not in db["users"]:
    db["users"][SUPPORT_CODE] = {
        "code": SUPPORT_CODE,
        "name": "پشتیبانی",
        "country": "IR",
        "password": hash_password("mamad1390"),
        "created_at": datetime.now().isoformat(),
        "contacts": [],
        "blocked": [],
        "groups": []
    }
    save_data(db)
    print(f"✅ Support account created: {SUPPORT_CODE}")

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
        user = db["users"].get(user_code, {})
        for code in online_users:
            if code != user_code:
                await self.send_to_user(code, {
                    "type": "contact_status",
                    "code": user_code,
                    "online": online,
                    "name": user.get('name', 'کاربر')
                })
    
    async def send_to_user(self, user_code: str, message: dict) -> bool:
        # چک بن
        if is_banned(user_code):
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
        if group_code not in db["groups"]:
            return
        
        group = db["groups"][group_code]
        for member in group.get("members", []):
            member_code = member.get("code") if isinstance(member, dict) else member
            if member_code != exclude:
                await self.send_to_user(member_code, message)
    
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


def is_banned(user_code: str) -> bool:
    """چک کردن وضعیت بن"""
    if user_code not in db["bans"]:
        return False
    
    ban = db["bans"][user_code]
    if ban.get("permanent"):
        return True
    
    if ban.get("until"):
        until = datetime.fromisoformat(ban["until"])
        if datetime.now() < until:
            return True
        else:
            del db["bans"][user_code]
            save_data(db)
            return False
    
    return False


def get_ban_info(user_code: str) -> Optional[dict]:
    """دریافت اطلاعات بن"""
    if user_code not in db["bans"]:
        return None
    
    ban = db["bans"][user_code]
    if ban.get("permanent"):
        return {"type": "permanent", "reason": ban.get("reason", "")}
    
    if ban.get("until"):
        until = datetime.fromisoformat(ban["until"])
        if datetime.now() < until:
            return {"type": "temporary", "until": ban["until"], "reason": ban.get("reason", "")}
    
    return None


# ========== WebSocket ==========

@app.websocket("/ws/{user_code}/{user_name}")
async def websocket_endpoint(websocket: WebSocket, user_code: str, user_name: str):
    # چک بن
    ban_info = get_ban_info(user_code)
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
    user = db["users"].get(user_code, {})
    user_name = user.get("name", "کاربر")
    
    # ========== ثبت‌نام ==========
    if msg_type == "register":
        name = data.get("name", "")[:30]
        country = data.get("country", "")
        password = data.get("password", "")
        
        if not name or not password:
            await manager.send_to_user(user_code, {"type": "register_error", "error": "نام و رمز الزامی است"})
            return
        
        db["users"][user_code] = {
            "code": user_code,
            "name": name,
            "country": country,
            "password": hash_password(password),
            "created_at": datetime.now().isoformat(),
            "contacts": [],
            "blocked": [],
            "groups": []
        }
        save_data(db)
        
        await manager.send_to_user(user_code, {"type": "register_success"})
    
    # ========== ورود ==========
    elif msg_type == "login":
        code = data.get("code", "")
        password = data.get("password", "")
        
        if code not in db["users"]:
            await manager.send_to_user(user_code, {"type": "login_error", "error": "کاربر یافت نشد"})
            return
        
        user_data = db["users"][code]
        if user_data.get("password") != hash_password(password):
            await manager.send_to_user(user_code, {"type": "login_error", "error": "رمز اشتباه است"})
            return
        
        await manager.send_to_user(user_code, {
            "type": "login_success",
            "user": {
                "code": code,
                "name": user_data["name"],
                "country": user_data.get("country", "")
            }
        })
    
    # ========== همگام‌سازی ==========
    elif msg_type == "sync":
        contacts = data.get("contacts", [])
        groups = data.get("groups", [])
        
        for code in contacts:
            is_online = code in online_users
            contact_user = db["users"].get(code, {})
            await manager.send_to_user(user_code, {
                "type": "contact_status",
                "code": code,
                "online": is_online,
                "name": contact_user.get('name', 'کاربر')
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
        to_user = db["users"].get(to_code, {})
        if user_code in to_user.get("blocked", []):
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
        chat_key = f"chat_{min(user_code, to_code)}_{max(user_code, to_code)}"
        if chat_key not in db["messages"]:
            db["messages"][chat_key] = []
        db["messages"][chat_key].append(msg_data)
        save_data(db)
        
        await manager.send_to_user(to_code, msg_data)
    
    # ========== ویرایش پیام ==========
    elif msg_type == "edit_message":
        to_code = data.get("to")
        msg_id = data.get("id")
        new_text = data.get("text", "")[:2000]
        is_group = data.get("isGroup", False)
        
        if not msg_id or not new_text:
            return
        
        if is_group:
            chat_key = f"group_{to_code}"
            await manager.broadcast_to_group(to_code, {
                "type": "message_edited",
                "id": msg_id,
                "text": new_text,
                "groupCode": to_code
            }, exclude=user_code)
        else:
            chat_key = f"chat_{min(user_code, to_code)}_{max(user_code, to_code)}"
            await manager.send_to_user(to_code, {
                "type": "message_edited",
                "id": msg_id,
                "text": new_text,
                "from": user_code
            })
        
        # ذخیره ویرایش
        if chat_key in db["messages"]:
            for msg in db["messages"][chat_key]:
                if msg.get("id") == msg_id:
                    msg["text"] = new_text
                    msg["edited"] = True
                    break
            save_data(db)
    
    # ========== حذف پیام ==========
    elif msg_type == "delete_message":
        to_code = data.get("to")
        msg_id = data.get("id")
        is_group = data.get("isGroup", False)
        
        if not msg_id:
            return
        
        if is_group:
            chat_key = f"group_{to_code}"
            await manager.broadcast_to_group(to_code, {
                "type": "message_deleted",
                "id": msg_id,
                "groupCode": to_code
            }, exclude=user_code)
        else:
            chat_key = f"chat_{min(user_code, to_code)}_{max(user_code, to_code)}"
            await manager.send_to_user(to_code, {
                "type": "message_deleted",
                "id": msg_id,
                "from": user_code
            })
        
        # حذف از دیتابیس
        if chat_key in db["messages"]:
            db["messages"][chat_key] = [m for m in db["messages"][chat_key] if m.get("id") != msg_id]
            save_data(db)
    
    # ========== پیام گروهی ==========
    elif msg_type == "group_message":
        group_code = data.get("to")
        text = data.get("text", "")[:2000]
        msg_id = data.get("id", str(datetime.now().timestamp()))
        
        if not group_code or not text or group_code not in db["groups"]:
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
        
        # ذخیره
        chat_key = f"group_{group_code}"
        if chat_key not in db["messages"]:
            db["messages"][chat_key] = []
        db["messages"][chat_key].append(msg_data)
        save_data(db)
        
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
        to_user = db["users"].get(to_code, {})
        if user_code in to_user.get("blocked", []):
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
        
        if not group_code or not media_type or not media_data or group_code not in db["groups"]:
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
            contact_user = db["users"].get(code, {})
            await manager.send_to_user(user_code, {
                "type": "contact_status",
                "code": code,
                "online": True,
                "name": contact_user.get('name', 'کاربر')
            })
        elif code and code in db["users"]:
            await manager.send_to_user(user_code, {
                "type": "user_info",
                "code": code,
                "name": db["users"][code].get('name', 'کاربر')
            })
    
    # ========== بلاک ==========
    elif msg_type == "block":
        code = data.get("code")
        if code and user_code in db["users"]:
            if "blocked" not in db["users"][user_code]:
                db["users"][user_code]["blocked"] = []
            if code not in db["users"][user_code]["blocked"]:
                db["users"][user_code]["blocked"].append(code)
                save_data(db)
    
    elif msg_type == "unblock":
        code = data.get("code")
        if code and user_code in db["users"]:
            if code in db["users"][user_code].get("blocked", []):
                db["users"][user_code]["blocked"].remove(code)
                save_data(db)
    
    # ========== گروه ==========
    elif msg_type == "create_group":
        group_data = data.get("group", {})
        group_code = group_data.get("code")
        group_name = group_data.get("name")
        
        if not group_code or not group_name:
            return
        
        db["groups"][group_code] = {
            "code": group_code,
            "name": group_name,
            "owner": user_code,
            "members": [{"code": user_code, "name": user_name, "isOwner": True}],
            "created_at": datetime.now().isoformat()
        }
        save_data(db)
        print(f"[+] Group created: {group_name} ({group_code})")
    
    elif msg_type == "join_group":
        query = data.get("query", "")
        found = None
        
        for g in db["groups"].values():
            if g["code"] == query or g["name"].lower() == query.lower():
                found = g
                break
        
        if found:
            member_codes = [m.get("code") for m in found["members"]]
            if user_code not in member_codes:
                found["members"].append({"code": user_code, "name": user_name, "isOwner": False})
                save_data(db)
                
                await manager.broadcast_to_group(found["code"], {
                    "type": "group_updated",
                    "group": found
                })
            
            await manager.send_to_user(user_code, {
                "type": "group_info",
                "group": found
            })
        else:
            await manager.send_to_user(user_code, {
                "type": "group_info",
                "error": "گروه یافت نشد"
            })
    
    elif msg_type == "leave_group":
        group_code = data.get("groupCode")
        if group_code in db["groups"]:
            group = db["groups"][group_code]
            group["members"] = [m for m in group["members"] if m.get("code") != user_code]
            
            if not group["members"]:
                del db["groups"][group_code]
            else:
                await manager.broadcast_to_group(group_code, {
                    "type": "group_updated",
                    "group": group
                })
            save_data(db)
    
    elif msg_type == "add_member":
        group_code = data.get("groupCode")
        member_code = data.get("memberCode")
        
        if group_code not in db["groups"]:
            return
        
        group = db["groups"][group_code]
        if group["owner"] != user_code:
            return
        
        member_codes = [m.get("code") for m in group["members"]]
        if member_code in member_codes:
            return
        
        member_name = db["users"].get(member_code, {}).get('name', f'کاربر')
        group["members"].append({"code": member_code, "name": member_name, "isOwner": False})
        save_data(db)
        
        await manager.broadcast_to_group(group_code, {
            "type": "group_updated",
            "group": group
        })
        
        await manager.send_to_user(member_code, {
            "type": "group_info",
            "group": group
        })
    
    elif msg_type == "kick_member":
        group_code = data.get("groupCode")
        member_code = data.get("memberCode")
        
        if group_code not in db["groups"]:
            return
        
        group = db["groups"][group_code]
        if group["owner"] != user_code:
            return
        
        group["members"] = [m for m in group["members"] if m.get("code") != member_code]
        save_data(db)
        
        await manager.send_to_user(member_code, {
            "type": "kicked",
            "groupCode": group_code,
            "groupName": group["name"]
        })
        
        await manager.broadcast_to_group(group_code, {
            "type": "group_updated",
            "group": group
        })
    
    # ========== تماس ==========
    elif msg_type == "call_request":
        to_code = data.get("to")
        if not to_code:
            return
        
        to_user = db["users"].get(to_code, {})
        if user_code in to_user.get("blocked", []):
            await manager.send_to_user(user_code, {"type": "call_rejected"})
            return
        
        await manager.send_to_user(to_code, {
            "type": "incoming_call",
            "callerCode": user_code,
            "callerName": user_name
        })
    
    elif msg_type == "call_accept":
        to_code = data.get("to")
        if not to_code:
            return
        
        active_calls[user_code] = {"other": to_code}
        active_calls[to_code] = {"other": user_code}
        
        await manager.send_to_user(to_code, {"type": "call_accepted"})
        print(f"[📞] Call: {user_code} <-> {to_code}")
    
    elif msg_type == "call_reject":
        to_code = data.get("to")
        if to_code:
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
        if group_code not in db["groups"]:
            return
        
        group = db["groups"][group_code]
        
        if group_code not in group_calls:
            group_calls[group_code] = set()
        
        group_calls[group_code].add(user_code)
        
        for member in group["members"]:
            member_code = member.get("code")
            if member_code != user_code:
                await manager.send_to_user(member_code, {
                    "type": "group_call_started",
                    "groupCode": group_code,
                    "groupName": group["name"],
                    "callerName": user_name,
                    "isGroup": True
                })
        
        print(f"[📞] Group call started: {group['name']}")
    
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
    
    # ========== پشتیبانی ==========
    elif msg_type == "get_support":
        await manager.send_to_user(user_code, {
            "type": "support_info",
            "code": SUPPORT_CODE,
            "telegram": SUPPORT_TELEGRAM
        })


# ========== API ادمین ==========

@app.get("/api/admin/users")
async def get_all_users(admin_key: str = ""):
    if admin_key != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    users = []
    for code, user in db["users"].items():
        users.append({
            "code": code,
            "name": user.get("name", ""),
            "country": user.get("country", ""),
            "created_at": user.get("created_at", ""),
            "online": code in online_users,
            "banned": is_banned(code),
            "ban_info": get_ban_info(code)
        })
    
    return {"users": users, "total": len(users), "online": len(online_users)}


@app.post("/api/admin/ban")
async def ban_user(admin_key: str = "", user_code: str = "", duration: int = 0, reason: str = ""):
    if admin_key != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if not user_code:
        raise HTTPException(status_code=400, detail="User code required")
    
    if duration == 0:
        # بن دائمی
        db["bans"][user_code] = {
            "permanent": True,
            "reason": reason,
            "banned_at": datetime.now().isoformat()
        }
    else:
        # بن موقت
        until = datetime.now() + timedelta(hours=duration)
        db["bans"][user_code] = {
            "permanent": False,
            "until": until.isoformat(),
            "reason": reason,
            "banned_at": datetime.now().isoformat()
        }
    
    save_data(db)
    
    # قطع اتصال کاربر
    if user_code in online_users:
        try:
            await online_users[user_code].send_json({
                "type": "banned",
                "info": get_ban_info(user_code)
            })
            await online_users[user_code].close()
        except:
            pass
    
    return {"success": True, "message": f"User {user_code} banned"}


@app.post("/api/admin/unban")
async def unban_user(admin_key: str = "", user_code: str = ""):
    if admin_key != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if user_code in db["bans"]:
        del db["bans"][user_code]
        save_data(db)
    
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
        "online": len(online_users),
        "groups": len(db["groups"]),
        "users": len(db["users"]),
        "calls": len(active_calls) // 2,
        "group_calls": len(group_calls)
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Server starting on port {port}")
    print(f"📁 Data file: {DATA_FILE}")
    uvicorn.run(app, host="0.0.0.0", port=port)