"""
بیسیم - سرور چت صوتی
Walkie-Talkie Voice Chat Server
"""

import os
import json
import asyncio
from typing import Dict
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from collections import defaultdict

app = FastAPI(title="Walkie-Talkie Voice Chat")


app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"


# ذخیره کانال‌ها و کاربران
# channels[channel_code] = {user_id: {"ws": websocket, "name": name}}
channels: Dict[str, Dict[str, dict]] = defaultdict(dict)


class ConnectionManager:
    """مدیریت اتصالات WebSocket"""
    
    async def connect(self, websocket: WebSocket, channel: str, user_id: str, user_name: str):
        """اتصال کاربر به کانال"""
        await websocket.accept()
        
        channels[channel][user_id] = {
            "ws": websocket,
            "name": user_name
        }
        
        # ارسال لیست کاربران به کاربر جدید
        users_list = [
            {"id": uid, "name": data["name"]} 
            for uid, data in channels[channel].items()
        ]
        await websocket.send_json({
            "type": "users",
            "users": users_list
        })
        
        # اعلام ورود به بقیه
        await self.broadcast_json(channel, {
            "type": "join",
            "user": {"id": user_id, "name": user_name}
        }, exclude=user_id)
        
        print(f"[+] User {user_name} ({user_id}) joined channel {channel}. Total: {len(channels[channel])}")
    
    async def disconnect(self, channel: str, user_id: str):
        """قطع اتصال کاربر"""
        user_name = "Unknown"
        if channel in channels and user_id in channels[channel]:
            user_name = channels[channel][user_id].get("name", "Unknown")
            del channels[channel][user_id]
            
            # حذف کانال خالی
            if not channels[channel]:
                del channels[channel]
                print(f"[-] Channel {channel} removed (empty)")
                return
        
        # اعلام خروج به بقیه
        await self.broadcast_json(channel, {
            "type": "leave",
            "userId": user_id
        })
        
        print(f"[-] User {user_name} ({user_id}) left channel {channel}")
    
    async def broadcast_audio(self, channel: str, audio_data: bytes, sender_id: str):
        """ارسال صدا به همه کاربران کانال به جز فرستنده"""
        if channel not in channels:
            return
        
        # ارسال همزمان به همه
        tasks = []
        for user_id, data in list(channels[channel].items()):
            if user_id != sender_id:
                tasks.append(self._send_audio(data["ws"], audio_data))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _send_audio(self, ws: WebSocket, audio_data: bytes):
        """ارسال صدا به یک کاربر"""
        try:
            await ws.send_bytes(audio_data)
        except Exception:
            pass
    
    async def broadcast_json(self, channel: str, message: dict, exclude: str = None):
        """ارسال پیام JSON به همه کاربران کانال"""
        if channel not in channels:
            return
        
        tasks = []
        for user_id, data in list(channels[channel].items()):
            if user_id != exclude:
                tasks.append(self._send_json(data["ws"], message))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _send_json(self, ws: WebSocket, message: dict):
        """ارسال JSON به یک کاربر"""
        try:
            await ws.send_json(message)
        except Exception:
            pass
    
    async def send_private(self, channel: str, target_id: str, message: dict):
        """ارسال پیام خصوصی به یک کاربر"""
        if channel not in channels:
            return False
        
        if target_id in channels[channel]:
            try:
                await channels[channel][target_id]["ws"].send_json(message)
                return True
            except Exception:
                return False
        return False


manager = ConnectionManager()


@app.websocket("/ws/{channel}/{user_id}/{user_name}")
async def websocket_endpoint(
    websocket: WebSocket, 
    channel: str, 
    user_id: str, 
    user_name: str
):
    """نقطه پایانی WebSocket برای چت صوتی"""
    
    # اعتبارسنجی کد کانال
    if not channel.isdigit() or not (6 <= len(channel) <= 12):
        await websocket.close(code=4001, reason="Invalid channel code")
        return
    
    await manager.connect(websocket, channel, user_id, user_name)
    
    try:
        while True:
            # دریافت داده
            message = await websocket.receive()
            
            if "bytes" in message:
                # داده صوتی PCM - ارسال به بقیه
                audio_data = message["bytes"]
                if len(audio_data) > 0:
                    await manager.broadcast_audio(channel, audio_data, user_id)
            
            elif "text" in message:
                # پیام JSON
                try:
                    data = json.loads(message["text"])
                    
                    if data.get("type") == "talking":
                        # اعلام وضعیت صحبت کردن
                        await manager.broadcast_json(channel, {
                            "type": "talking",
                            "userId": user_id,
                            "isTalking": data.get("isTalking", False)
                        }, exclude=user_id)
                    
                    elif data.get("type") == "chat":
                        # پیام چت
                        chat_message = {
                            "type": "chat",
                            "senderId": user_id,
                            "senderName": user_name,
                            "text": data.get("text", "")[:500],  # محدودیت طول
                            "isPrivate": data.get("isPrivate", False),
                            "targetId": data.get("targetId")
                        }
                        
                        if data.get("isPrivate") and data.get("targetId"):
                            # پیام خصوصی
                            await manager.send_private(channel, data["targetId"], chat_message)
                        else:
                            # پیام عمومی
                            await manager.broadcast_json(channel, chat_message, exclude=user_id)
                        
                except json.JSONDecodeError:
                    pass
                    
    except WebSocketDisconnect:
        await manager.disconnect(channel, user_id)
    except Exception as e:
        print(f"[!] Error: {e}")
        await manager.disconnect(channel, user_id)


@app.get("/")
def home():
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE)
    return {"status": "VOE is running but index.html not found"}


@app.get("/health")
async def health_check():
    """بررسی سلامت سرور"""
    total_users = sum(len(users) for users in channels.values())
    return {
        "status": "healthy",
        "active_channels": len(channels),
        "total_users": total_users
    }


@app.get("/stats")
async def get_stats():
    """آمار سرور"""
    channel_stats = {}
    for ch, users in channels.items():
        channel_stats[ch] = {
            "users": len(users),
            "user_names": [u["name"] for u in users.values()]
        }
    return {
        "total_channels": len(channels),
        "channels": channel_stats
    }


# سرو کردن فایل‌های استاتیک
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)