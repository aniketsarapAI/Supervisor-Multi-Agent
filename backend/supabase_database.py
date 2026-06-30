# IMPORT PACKAGES
import os
from supabase import create_client, Client
from dotenv import load_dotenv
from pydantic import EmailStr
from tenacity import retry, stop_after_attempt, wait_exponential

# INSTANTIATE
load_dotenv()

url: str = os.getenv("SUPABASE_URL", "")
anon_key: str = os.getenv("SUPABASE_KEY", "")
service_key: str = os.getenv("SUPABASE_SERVICE_KEY", "")

# Two clients: auth (anon key) for sign in/up, admin (service key) for DB ops
sb_auth: Client | None = None
sb_admin: Client | None = None

if url and anon_key:
    sb_auth = create_client(url, anon_key)
if url and service_key:
    sb_admin = create_client(url, service_key)


# INSERT THE CHAT TO THE DB
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def insert_chat(user_id: str, session_id: str, role: str, content: str):
    if sb_admin is None:
        return {"error": "Supabase admin client not configured"}
    response = (
        sb_admin.table("session")
        .insert({
            "session_id": session_id,
            "user_id": user_id,
            "role": role,
            "content": content
        })
        .execute()
    )
    return response


# fetch all sessions
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def get_session_summaries(user_id: str):
    if sb_admin is None:
        return []
    response = (
        sb_admin.table("session")
        .select("id, session_id, role, content")
        .eq("user_id", user_id)
        .order("created_at", desc=False)
        .execute()
    )

    if not response.data:
        return []

    first_queries = {}
    for row in response.data:
        sid = row["session_id"]
        if sid not in first_queries:
            first_queries[sid] = {
                "id": row["id"],
                "session_id": sid,
                "first_query": row["content"]
            }

    return list(first_queries.values())


# get application log -> from a session id
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def get_chat_history(user_id, session_id):
    if sb_admin is None:
        return []
    response = (
        sb_admin.table("session")
        .select("role, content")
        .eq("user_id", user_id)
        .eq("session_id", session_id)
        .execute()
    )

    if not response.data:
        return []
    return response.data


# sign up
def sign_up_authentication(email: EmailStr, password: str):
    if sb_auth is None:
        raise Exception("Supabase auth client not configured")
    response = sb_auth.auth.sign_up({
        'email': email,
        'password': password,
        'options': {
            'email_redirect_to': os.getenv("REDIRECT_URL", ""),
        },
    })
    return response


# sign in
def sign_in_authentication(email: EmailStr, password: str):
    if sb_auth is None:
        raise Exception("Supabase auth client not configured")
    response = sb_auth.auth.sign_in_with_password(
        {
            "email": email,
            "password": password,
        }
    )
    return response


# refresh token
def refresh_authentication(refresh_token: str):
    if sb_auth is None:
        raise Exception("Supabase auth client not configured")
    response = sb_auth.auth.refresh_session(refresh_token)
    return response