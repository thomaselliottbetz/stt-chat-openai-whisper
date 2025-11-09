"""
FastAPI backend for chat application with speech-to-text transcription.
Handles user authentication, chat management, and real-time messaging via WebSockets.
"""
import sqlite3
import os
import uuid
import asyncio
import json
import secrets
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import boto3
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    Response,
    Cookie,
    Form,
    Depends,
    Body,
    Query,
)
from fastapi.responses import JSONResponse
from passlib.hash import bcrypt
from sqlalchemy import create_engine, text

load_dotenv("fastapi.env")

# Configuration from environment variables
DATABASE_PATH = os.getenv("DATABASE_PATH", "db/app.db")
SHARED_SECRET = os.getenv("SHARED_SECRET")
if not SHARED_SECRET:
    raise ValueError("SHARED_SECRET environment variable must be set")

INPUT_BUCKET = os.getenv("INPUT_BUCKET")
if not INPUT_BUCKET:
    raise ValueError("INPUT_BUCKET environment variable must be set")

OUTPUT_BUCKET = os.getenv("OUTPUT_BUCKET")
if not OUTPUT_BUCKET:
    raise ValueError("OUTPUT_BUCKET environment variable must be set")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")


def generate_token():
    return secrets.token_urlsafe(32)


def get_username_by_id(user_id: int) -> str:
    db = get_db()
    cur = db.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    db.close()
    if row:
        return row[0]
    return "unknown"


def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str) -> str:
    return bcrypt.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.verify(password, hashed)


s3 = boto3.client("s3")
app = FastAPI()
connections = {}
app.state.sessions = {}


PACIFIC = ZoneInfo("America/Los_Angeles")


def format_pacific(ts: str) -> str:
    """
    Convert a stored timestamp string to 'Sat, Sep 6, 01:38' in America/Los_Angeles.
    Accepts ISO8601 or SQLite 'YYYY-MM-DD HH:MM:SS'.
    """
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # assume UTC if naive
    except Exception:
        try:
            # SQLite default CURRENT_TIMESTAMP: 'YYYY-MM-DD HH:MM:SS' (UTC)
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return ts  # fallback: return as-is

    dt_pacific = dt.astimezone(PACIFIC)
    return dt_pacific.strftime("%a, %b %-d, %H:%M")


async def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = app.state.sessions.get(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid session")
    return user_id


@app.post("/api/get-presigned-url")
async def get_presigned_url(
    data: Optional[dict] = Body(None),
    user_id: int = Depends(get_current_user),
):
    username = get_username_by_id(user_id)
    chat_id = (data or {}).get("chat_id")  # present only when admin

    if username == ADMIN_USERNAME and chat_id:
        key_prefix = f"{username}/{chat_id}"
    else:
        key_prefix = username  # unchanged for regular users

    key = f"{key_prefix}/{uuid.uuid4()}.webm"
    url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": INPUT_BUCKET, "Key": key, "ContentType": "audio/webm"},
        ExpiresIn=300,
    )
    return {"url": url, "key": key}


@app.get("/api/get-transcription")
def get_transcription(request: Request):
    key = request.query_params.get("key")
    if not key:
        raise HTTPException(status_code=400, detail="Missing key")

    # Extract UUID from incoming key (e.g., uploads/uuid.webm → uuid)
    uuid_part = os.path.splitext(os.path.basename(key))[0]

    # List objects in OUTPUT_BUCKET and find matching suffix
    try:
        response = s3.list_objects_v2(Bucket=OUTPUT_BUCKET)
        for obj in response.get("Contents", []):
            obj_key = obj["Key"]
            if obj_key.endswith(f"{uuid_part}.json"):
                file = s3.get_object(Bucket=OUTPUT_BUCKET, Key=obj_key)
                data = json.loads(file["Body"].read().decode("utf-8"))
                return {"transcription": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=204, detail="Transcription not ready")


@app.get("/api/get-transcription-feed")
def get_transcription_feed():
    try:
        response = s3.list_objects_v2(Bucket=OUTPUT_BUCKET)
        items = []

        for obj in sorted(response.get("Contents", []), key=lambda x: x["Key"]):
            key = obj["Key"]
            if key.endswith(".json"):
                file = s3.get_object(Bucket=OUTPUT_BUCKET, Key=key)
                data = json.loads(file["Body"].read().decode("utf-8"))
                items.append({"key": key, "text": data.get("text", "")})

        return {"transcriptions": items}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    username = None

    try:
        # Wait for the client to send the auth message
        try:
            auth_message = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        except asyncio.TimeoutError:
            await websocket.close(code=1008)
            return

        data = json.loads(auth_message)

        if data.get("type") != "auth":
            await websocket.close(code=1008)
            return

        token = data.get("token")
        if not token or token not in app.state.sessions:
            await websocket.close(code=1008)
            return

        user_id = app.state.sessions[token]
        username = get_username_by_id(user_id)

        # Replace old connection if user reconnects
        if username in connections:
            try:
                old_ws = connections[username]
                await old_ws.close(code=1000)
            except Exception:
                pass

        connections[username] = websocket

        # Keep the connection alive
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break

    except Exception:
        pass
    finally:
        if username:
            connections.pop(username, None)


@app.post("/transcription-callback")
async def transcription_callback(request: Request):
    data = await request.json()

    # 1 — auth
    if data.get("secret") != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    message = data.get("message")
    if not message or "text" not in message:
        raise HTTPException(status_code=400, detail="Missing message data")

    transcription_text = message["text"].strip()
    real_sender = message["sender"]
    ts_iso = datetime.now(PACIFIC).isoformat(timespec="seconds")
    # timestamp = message.get("timestamp", formatted_time)
    audio_key = message.get("audio_key", "")  # NEW

    # 2 — resolve user IDs
    sender_id = get_user_id_by_username(real_sender)
    admin_id = get_user_id_by_username(ADMIN_USERNAME)
    if sender_id is None:
        return JSONResponse({"status": "ignored"})
    if admin_id is None:
        return JSONResponse({"status": "error", "detail": "Admin not found"})

    # 3 — determine chat_id
    chat_id_from_key = None
    parts = audio_key.split("/")
    if len(parts) >= 3 and parts[0] == ADMIN_USERNAME:
        try:
            chat_id_from_key = int(parts[1])
        except ValueError:
            pass

    if chat_id_from_key:  # honour explicit chat
        chat_id = chat_id_from_key
    else:  # fallback: first admin↔sender chat
        chat_id = get_or_create_chat(sender_id, admin_id)

    # 4 — persist transcription
    try:
        save_transcribed_message(real_sender, transcription_text, ts_iso)
    except Exception:
        pass

    # 5 — prepare payload
    payload = {
        "type": "transcription",
        "chat_id": chat_id,
        "text": transcription_text,
        "sender": real_sender,
        "timestamp": format_pacific(ts_iso),
    }

    admin_ws = connections.get(ADMIN_USERNAME)
    sender_ws = connections.get(real_sender)

    if admin_ws and real_sender != ADMIN_USERNAME:
        try:
            await admin_ws.send_text(json.dumps(payload))
        except Exception:
            connections.pop(ADMIN_USERNAME, None)

    if sender_ws:
        try:
            await sender_ws.send_text(json.dumps(payload))
        except Exception:
            connections.pop(real_sender, None)

    # Send to other chat participants
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT u.username
                    FROM chat_participants cp
                    JOIN users u ON u.id = cp.user_id
                    WHERE cp.chat_id = :cid
                """
                ),
                {"cid": chat_id},
            ).fetchall()

        partners = [
            p[0]
            for p in rows
            if p[0] not in (real_sender, ADMIN_USERNAME)
        ]

        for partner in partners:
            partner_ws = connections.get(partner)
            if partner_ws:
                try:
                    await partner_ws.send_text(json.dumps(payload))
                except Exception:
                    connections.pop(partner, None)

    except Exception:
        pass

    return JSONResponse({"status": "ok"})


@app.post("/api/register")
async def register(
    username: str = Form(...), password: str = Form(...), invite_code: str = Form(...)
):
    db = get_db()

    # Validate invite code
    cur = db.execute(
        "SELECT id, used FROM registration_invitations WHERE code = ?", (invite_code,)
    )
    invite = cur.fetchone()
    if not invite:
        raise HTTPException(status_code=400, detail="Invalid invite code")
    if invite["used"]:
        raise HTTPException(status_code=400, detail="Invite code already used")

    # Create user
    try:
        password_hash = hash_password(password)
        db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
        db.execute(
            "UPDATE registration_invitations SET used = 1 WHERE id = ?", (invite["id"],)
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Username already exists")

    return {"status": "registered"}


@app.post("/api/login")
def login(username: str = Form(...), password: str = Form(...)):
    conn = sqlite3.connect(DATABASE_PATH)
    cur = conn.cursor()

    # Check credentials
    cur.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?", (username,)
    )
    row = cur.fetchone()

    if not row or not verify_password(password, row[2]):
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user_id, uname = row[0], row[1]
    token = generate_token()

    # Insert session
    cur.execute("INSERT INTO sessions (user_id, token) VALUES (?, ?)", (user_id, token))
    conn.commit()
    conn.close()

    # Ensure session is in memory
    if not hasattr(app.state, "sessions"):
        app.state.sessions = {}
    app.state.sessions[token] = user_id

    redirect_url = "/index.html"
    response = JSONResponse(content={"status": "logged_in", "redirect": redirect_url})
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )
    return response


@app.get("/api/me")
async def me(request: Request):
    token = request.cookies.get("session_token")
    if not token or token not in app.state.sessions:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = app.state.sessions[token]
    username = get_username_by_id(user_id)
    return {
        "username": username,
        "token": token,
        "isAdmin": username == ADMIN_USERNAME,
    }


@app.post("/api/logout")
async def logout(response: Response, request: Request):
    token = request.cookies.get("session_token")
    if token and token in app.state.sessions:
        del app.state.sessions[token]
    response.delete_cookie("session_token")
    return {"status": "logged_out"}


@app.get("/api/validate-invite")
async def validate_invite(code: str):
    db = get_db()
    cur = db.execute(
        "SELECT id, used FROM registration_invitations WHERE code = ?", (code,)
    )
    invite = cur.fetchone()
    db.close()
    if not invite:
        raise HTTPException(status_code=400, detail="Invalid invite code")
    if invite["used"]:
        raise HTTPException(status_code=400, detail="Invite code already used")
    return {"status": "valid"}


engine = create_engine(
    f"sqlite:///{DATABASE_PATH}", connect_args={"check_same_thread": False}
)


def get_user_id_by_username(username):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM users WHERE username = :u"), {"u": username}
        ).fetchone()
        return result[0] if result else None


def get_or_create_chat(user1_id, user2_id):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
            SELECT c.id FROM chats c
            JOIN chat_participants cp1 ON cp1.chat_id = c.id AND cp1.user_id = :u1
            JOIN chat_participants cp2 ON cp2.chat_id = c.id AND cp2.user_id = :u2
        """
            ),
            {"u1": user1_id, "u2": user2_id},
        ).fetchone()
        if result:
            return result[0]
        chat_id = conn.execute(text("INSERT INTO chats DEFAULT VALUES")).lastrowid
        conn.execute(
            text(
                "INSERT INTO chat_participants (chat_id, user_id) VALUES (:c, :u1), (:c, :u2)"
            ),
            {"c": chat_id, "u1": user1_id, "u2": user2_id},
        )
        return chat_id


def save_transcribed_message(sender_username: str, message_text: str, timestamp: str):
    sender_id = get_user_id_by_username(sender_username)
    if sender_id is None:
        raise Exception(f"Unknown sender: {sender_username}")

    admin_id = get_user_id_by_username(ADMIN_USERNAME)
    if admin_id is None:
        raise Exception(f"Admin user '{ADMIN_USERNAME}' not found")

    chat_id = get_or_create_chat(sender_id, admin_id)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO messages (chat_id, sender_id, text, timestamp)
                VALUES (:cid, :sid, :text, :timestamp)
                """
            ),
            {
                "cid": chat_id,
                "sid": sender_id,
                "text": message_text.strip(),
                "timestamp": timestamp,
            },
        )


@app.get("/api/chats")
def get_chats(session_token: str = Cookie(None)):
    """
    Get all chats for the authenticated user.
    SECURITY: Regular (non-admin) users can ONLY see their chat with admin.
    Admin can see all their chats with other users.
    This is enforced server-side regardless of client-side manipulation.
    """
    user = validate_session(session_token)
    username = user["username"]
    user_id = get_user_id_by_username(username)
    
    # SECURITY: Regular users should ONLY see their chat with admin
    # Admin can see all their chats
    is_admin_user = username == ADMIN_USERNAME
    
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT 
                    c.id,
                    u.username,
                    m.text AS last_message,
                    m.timestamp,
                    (
                        SELECT COUNT(*) FROM messages msg
                        WHERE msg.chat_id = c.id
                        AND msg.sender_id != :me
                        AND msg.timestamp > IFNULL(
                            (SELECT MAX(viewed_at) FROM chat_reads cr
                             WHERE cr.chat_id = c.id AND cr.user_id = :me), 
                            0
                        )
                    ) AS unread_count
                FROM chats c
                JOIN chat_participants cp ON cp.chat_id = c.id
                JOIN chat_participants cp2 ON cp2.chat_id = c.id
                JOIN users u ON u.id = cp2.user_id
                LEFT JOIN (
                    SELECT chat_id, text, MAX(timestamp) as timestamp
                    FROM messages
                    GROUP BY chat_id
                ) m ON m.chat_id = c.id
                WHERE cp.user_id = :me AND u.id != :me
                ORDER BY m.timestamp DESC NULLS LAST
                """
            ),
            {"me": user_id},
        ).fetchall()

        # SECURITY: Additional server-side filtering
        # Regular users can ONLY have their chat with admin
        admin_id = get_user_id_by_username(ADMIN_USERNAME)
        filtered_rows = []
        
        for r in rows:
            chat_id = r[0]
            other_username = r[1]
            
            # For regular users, only return chats where the other participant is admin
            if not is_admin_user:
                if other_username != ADMIN_USERNAME:
                    continue
            
            filtered_rows.append(r)

        return [
            {
                "chat_id": r[0],
                "username": r[1],
                "last_message": r[2],
                "timestamp": format_pacific(r[3]) if r[3] else None,
                "unread": r[4] > 0,
            }
            for r in filtered_rows
        ]


@app.get("/api/get-messages")
def get_messages(
    chat_id: int,
    before_id: int = Query(None),
    session_token: str = Cookie(None),
):
    """
    Get messages for a specific chat.
    SECURITY: Verifies the user is a participant AND for regular users,
    ensures they can ONLY access their chat with admin.
    """
    user = validate_session(session_token)
    username = user["username"]
    user_id = get_user_id_by_username(username)
    is_admin_user = username == ADMIN_USERNAME

    with engine.connect() as conn:
        # Verify user is a participant - THIS IS THE SECURITY CHECK
        check = conn.execute(
            text(
                "SELECT 1 FROM chat_participants WHERE chat_id = :cid AND user_id = :uid"
            ),
            {"cid": chat_id, "uid": user_id},
        ).fetchone()
        if not check:
            raise HTTPException(status_code=403, detail="Not in chat")
        
        # ADDITIONAL SECURITY: Regular users can ONLY access chats with admin
        if not is_admin_user:
            # Get all participants in this chat
            participants = conn.execute(
                text(
                    """
                    SELECT u.username FROM chat_participants cp
                    JOIN users u ON u.id = cp.user_id
                    WHERE cp.chat_id = :cid
                    """
                ),
                {"cid": chat_id},
            ).fetchall()
            
            participant_usernames = [p[0] for p in participants]
            
            # Regular users can ONLY be in chats with admin
            if ADMIN_USERNAME not in participant_usernames:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: regular users can only access chats with admin",
                )

            # Ensure there are only 2 participants (user and admin)
            if len(participant_usernames) != 2:
                raise HTTPException(
                    status_code=403, detail="Access denied: invalid chat configuration"
                )

        if before_id:
            # Fetch older messages in ascending order
            query = """
            SELECT m.id, u.username, m.text, m.timestamp
            FROM messages m
            JOIN users u ON u.id = m.sender_id
            WHERE m.chat_id = :cid AND m.id < :before_id
            ORDER BY m.id DESC
            LIMIT 10
            """
            rows = conn.execute(
                text(query), {"cid": chat_id, "before_id": before_id}
            ).fetchall()
            rows = list(rows)[::-1] # flip to ascending
        else:
            # Initial load: latest 10, then reverse
            query = """
            SELECT m.id, u.username, m.text, m.timestamp
            FROM messages m
            JOIN users u ON u.id = m.sender_id
            WHERE m.chat_id = :cid
            ORDER BY m.id DESC
            LIMIT 10
            """
            rows = conn.execute(text(query), {"cid": chat_id}).fetchall()
            rows = list(rows)[::-1]  # flip to ascending

        return [
            {
                "id": r[0],
                "sender": r[1],
                "text": r[2],
                "timestamp": format_pacific(r[3]),
            }
            for r in rows
        ]


@app.post("/api/send-message")
async def send_message(data: dict, session_token: str = Cookie(None)):
    """
    Send a message to a chat.
    SECURITY: Verifies the user is a participant AND for regular users,
    ensures they can ONLY send messages to their chat with admin.
    """
    user = validate_session(session_token)
    username = user["username"]
    user_id = get_user_id_by_username(username)
    is_admin_user = username == ADMIN_USERNAME
    chat_id = data.get("chat_id")
    text_msg = data.get("text")
    if not chat_id or not text_msg:
        raise HTTPException(status_code=400, detail="Missing chat_id or text")

    now_iso = datetime.now(PACIFIC).isoformat(timespec="seconds")  # store ISO
    with engine.begin() as conn:
        # Security check: verify user is a participant
        check = conn.execute(
            text(
                """
            SELECT 1 FROM chat_participants WHERE chat_id = :cid AND user_id = :uid
        """
            ),
            {"cid": chat_id, "uid": user_id},
        ).fetchone()
        if not check:
            raise HTTPException(status_code=403, detail="Not in chat")
        
        # ADDITIONAL SECURITY: Regular users can ONLY send messages to chats with admin
        if not is_admin_user:
            participants = conn.execute(
                text(
                    """
                    SELECT u.username FROM chat_participants cp
                    JOIN users u ON u.id = cp.user_id
                    WHERE cp.chat_id = :cid
                    """
                ),
                {"cid": chat_id},
            ).fetchall()
            
            participant_usernames = [p[0] for p in participants]
            
            # Regular users can ONLY send to chats with admin
            if ADMIN_USERNAME not in participant_usernames:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: regular users can only message in chats with admin",
                )

            # Ensure there are only 2 participants (user and admin)
            if len(participant_usernames) != 2:
                raise HTTPException(
                    status_code=403, detail="Access denied: invalid chat configuration"
                )
        conn.execute(
            text(
                """
            INSERT INTO messages (chat_id, sender_id, text, timestamp)
            VALUES (:cid, :sid, :txt, :ts)
        """
            ),
            {"cid": chat_id, "sid": user_id, "txt": text_msg, "ts": now_iso},
        )

    # Determine recipient (chat partner)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT u.username FROM chat_participants cp
                JOIN users u ON u.id = cp.user_id
                WHERE cp.chat_id = :cid AND u.username != :me
            """
            ),
            {"cid": chat_id, "me": user["username"]},
        ).fetchall()

    # Send to chat partner(s) and self
    targets = [r[0] for r in rows] + [user["username"]]
    display_ts = format_pacific(now_iso)
    payload = {
        "type": "message",
        "chat_id": chat_id,
        "sender": user["username"],
        "text": text_msg,
        "timestamp": display_ts,
    }

    for username in targets:
        ws = connections.get(username)
        if ws:
            asyncio.create_task(ws.send_text(json.dumps(payload)))

    return {"status": "ok"}


def validate_session(session_token: str = Cookie(None)):
    if not session_token:
        raise HTTPException(status_code=401, detail="Missing session token")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username FROM users WHERE id = (SELECT user_id FROM sessions WHERE token = ?)",
        (session_token,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid session")

    return {"id": row["id"], "username": row["username"]}


@app.post("/api/mark-read")
def mark_chat_read(data: dict, session_token: str = Cookie(None)):
    user = validate_session(session_token)
    user_id = get_user_id_by_username(user["username"])
    chat_id = data.get("chat_id")

    if not chat_id:
        raise HTTPException(status_code=400, detail="Missing chat_id")

    now = datetime.now(PACIFIC).isoformat(timespec="seconds")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO chat_reads (chat_id, user_id, viewed_at)
                VALUES (:cid, :uid, :ts)
                ON CONFLICT(chat_id, user_id) 
                DO UPDATE SET viewed_at = :ts
                """
            ),
            {"cid": chat_id, "uid": user_id, "ts": now},
        )
    return {"status": "ok"}


