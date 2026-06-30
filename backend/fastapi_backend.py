# IMPORT PACKAGES
import os
import json
import logging
from datetime import datetime, timezone
from fastapi import FastAPI, Query, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import Optional
import uuid

from langchain_core.messages import HumanMessage, AIMessageChunk
from fastapi.responses import JSONResponse, StreamingResponse

from .ai_agent import graph_builder
from utils import AgentState
from .auth import get_current_user_id
from .supabase_database import (
    insert_chat,
    get_chat_history,
    get_session_summaries,
    refresh_authentication,
)

load_dotenv()

# Structured JSON logging for Cloud Logging
class CloudLogFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(log_entry)

handler = logging.StreamHandler()
handler.setFormatter(CloudLogFormatter())
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Supervisor Multi-Agent API", version="2.0.0")

agent_app = None
_start_time = datetime.now(timezone.utc)

# CORS — restrict to frontend domain in production
# Note: Streamlit makes server-side requests, so CORS doesn't apply.
# If a browser-based frontend is added, restrict CORS_ORIGINS to that domain.
_cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Type"],
)


# STARTUP: validate config + build agent graph
@app.on_event("startup")
async def startup():
    global agent_app

    required_vars = [
        "OPENROUTER_API_KEY",
        "SUPABASE_URL",
        "SUPABASE_JWT_SECRET",
        "SUPABASE_SERVICE_KEY",
    ]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {missing}")
    if not os.getenv("SUPABASE_DB_URL"):
        logger.warning("SUPABASE_DB_URL not set — falling back to MemorySaver (not prod-safe)")

    agent_app = await graph_builder()
    logger.info("Agent graph built successfully")


# HOME ROUTE
@app.get("/")
async def home():
    return JSONResponse(
        content={
            "message": "Welcome to the Supervisor Multi-Agent API",
            "documentation": "Visit /docs for API documentation",
            "version": "2.0.0",
        },
        status_code=200,
    )


# HEALTH CHECK ROUTE
@app.get("/health")
async def health_check():
    uptime = (datetime.now(timezone.utc) - _start_time).total_seconds()
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "sma-api",
            "version": "2.0.0",
            "uptime_seconds": uptime,
            "started_at": _start_time.isoformat(),
        },
        status_code=200,
    )


# --- AUTH ENDPOINTS ---

class RefreshRequest(BaseModel):
    refresh_token: str


@app.post("/api/auth/refresh")
def refresh_token(req: RefreshRequest):
    try:
        new_session = refresh_authentication(req.refresh_token)
        return {
            "access_token": new_session.session.access_token,
            "refresh_token": new_session.session.refresh_token,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token refresh failed: {str(e)}",
        )


# --- CHAT PERSISTENCE ENDPOINTS (backend-owned DB ops) ---

class ChatInsertRequest(BaseModel):
    session_id: str
    role: str
    content: str


@app.post("/api/sessions/chat")
def insert_chat_endpoint(req: ChatInsertRequest, user_id: str = Depends(get_current_user_id)):
    result = insert_chat(
        user_id=user_id, session_id=req.session_id, role=req.role, content=req.content
    )
    return {"status": "ok", "data": str(result)}


@app.get("/api/sessions")
def get_sessions_endpoint(user_id: str = Depends(get_current_user_id)):
    return get_session_summaries(user_id=user_id)


@app.get("/api/sessions/{session_id}")
def get_chat_history_endpoint(session_id: str, user_id: str = Depends(get_current_user_id)):
    return get_chat_history(user_id=user_id, session_id=session_id)


# --- CHAT STREAM ENDPOINT ---

class ChatStreamRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None


async def generate_agent_response(message: str, thread_id: str):
    memory_config = {"configurable": {"thread_id": thread_id}}

    snapshot = await agent_app.aget_state(config=memory_config)

    if snapshot and "messages" in snapshot.values:
        old_msgs = snapshot.values["messages"]
    else:
        old_msgs = []

    events = agent_app.astream_events(
        input=AgentState(messages=old_msgs + [HumanMessage(content=message)]),
        version="v2",
        config=memory_config,
    )

    try:
        async for event in events:
            if event["event"] == "on_chat_model_stream":
                if isinstance(event["data"]["chunk"], AIMessageChunk):
                    event_content = event["data"]["chunk"].content
                    safe_content_json = {"type": "content", "content": event_content}
                    yield f"data: {json.dumps(safe_content_json)}\n\n"
    except Exception as e:
        logger.error(f"Agent streaming error: {e}")
        error_json = json.dumps({"type": "error", "content": str(e)})
        yield f"data: {error_json}\n\n"
    finally:
        yield "data: {\"type\": \"end\"}\n\n"


@app.post("/chat_stream")
def chat_stream(req: ChatStreamRequest, user_id: str = Depends(get_current_user_id)):
    return StreamingResponse(
        generate_agent_response(req.message, req.thread_id or user_id),
        media_type="text/event-stream",
    )