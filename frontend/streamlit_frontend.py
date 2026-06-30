import streamlit as st
import requests
import json
import re
import sys
import os
import uuid
import logging
from streamlit_cookies_controller import CookieController

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.supabase_database import sb_auth

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
COOKIE_ACCESS = "sma_v1_access"
COOKIE_REFRESH = "sma_v1_refresh"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30

controller = CookieController()


def _cookie_secure():
    cookie_secure = os.getenv("COOKIE_SECURE", "").lower()
    if cookie_secure in {"1", "true", "yes"}:
        return True
    if cookie_secure in {"0", "false", "no"}:
        return False
    return os.getenv("ENVIRONMENT", "").lower() == "production" or BACKEND_URL.startswith("https")


def _set_cookie(name, value):
    controller.set(
        name,
        value,
        max_age=COOKIE_MAX_AGE,
        secure=_cookie_secure(),
        same_site="lax",
        path="/",
    )


def _remove_cookie(name):
    controller.remove(name, path="/", secure=_cookie_secure(), same_site="lax")


def is_authenticated():
    return bool(
        st.session_state.get("user_id")
        and st.session_state.get("access_token")
        and st.session_state.get("refresh_token")
    )


def save_session(session, user):
    st.session_state["user_id"] = user.id
    st.session_state["access_token"] = session.access_token
    st.session_state["refresh_token"] = session.refresh_token
    _set_cookie(COOKIE_ACCESS, session.access_token)
    _set_cookie(COOKIE_REFRESH, session.refresh_token)


def clear_session():
    st.session_state["user_id"] = None
    st.session_state["access_token"] = None
    st.session_state["refresh_token"] = None
    _remove_cookie(COOKIE_ACCESS)
    _remove_cookie(COOKIE_REFRESH)


def restore_session():
    if sb_auth is None:
        logging.error("Supabase auth client is not configured")
        clear_session()
        return False

    try:
        access_cookie = controller.get(COOKIE_ACCESS)
        refresh_cookie = controller.get(COOKIE_REFRESH)

        if not access_cookie or not refresh_cookie:
            return False

        response = sb_auth.auth.set_session(access_cookie, refresh_cookie)
        if response and response.session and response.user:
            save_session(response.session, response.user)
            return True
    except Exception:
        logging.exception("Session restoration failed")

    clear_session()
    return False


def refresh_session():
    if sb_auth is None or not st.session_state.get("refresh_token"):
        clear_session()
        return False

    try:
        response = sb_auth.auth.refresh_session(st.session_state["refresh_token"])
        if response and response.session and response.user:
            save_session(response.session, response.user)
            return True
    except Exception:
        logging.exception("Session refresh failed")

    clear_session()
    return False


# API CALL HELPER with auto-refresh on 401
def api_call(method, path, **kwargs):
    headers = kwargs.pop("headers", {})
    if "access_token" in st.session_state and st.session_state["access_token"]:
        headers["Authorization"] = f"Bearer {st.session_state['access_token']}"

    resp = requests.request(method, f"{BACKEND_URL}{path}", headers=headers, timeout=30, **kwargs)

    # Auto-refresh on expired token, then retry this request once.
    if resp.status_code == 401 and "refresh_token" in st.session_state and st.session_state["refresh_token"]:
        if refresh_session():
            headers["Authorization"] = f"Bearer {st.session_state['access_token']}"
            resp = requests.request(method, f"{BACKEND_URL}{path}", headers=headers, timeout=30, **kwargs)
        else:
            clear_session()

    return resp


# UTILITIES

route_tags = [
    json.dumps({"route": "greeting"}),
    json.dumps({"route": "enhancer"}),
    json.dumps({"route": "coder"}),
    json.dumps({"route": "maths_reasoner"}),
    json.dumps({"route": "researcher"}),
]

def clean_text(text):
    # Remove any JSON route tags (with or without spaces after colon)
    text = re.sub(r'\{"route"\s*:\s*"[^"]+"\}', '', text)
    for tag in route_tags:
        text = text.replace(tag, "")
    return text

def format_latex_markdown(text: str) -> str:
    text = re.sub(r'\(([^)]*?\\frac[^)]*?)\)', r'$\1$', text)
    return text.replace("\\n", "\n")


# INITIALISE ST SESSIONS
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())
if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = []
if "all_session_summaries" not in st.session_state:
    st.session_state["all_session_summaries"] = []
if "user_id" not in st.session_state:
    st.session_state["user_id"] = None
if "access_token" not in st.session_state:
    st.session_state["access_token"] = None
if "refresh_token" not in st.session_state:
    st.session_state["refresh_token"] = None
if "is_signing_in" not in st.session_state:
    st.session_state["is_signing_in"] = True


# AUTHENTICATION FUNCTIONS
def change_auth_mode():
    st.session_state["is_signing_in"] = not st.session_state["is_signing_in"]

def show_auth_ui():
    st.title("Sign In" if st.session_state["is_signing_in"] else "Sign Up")

    email = st.text_input("Enter your email")
    password = st.text_input("Enter your password", type="password")

    sign_btn = st.button("Sign In" if st.session_state["is_signing_in"] else "Sign Up")
    st.toggle(label="Already have an account?", value=True, on_change=change_auth_mode, key="auth_toggle")
    st.html("<br >")

    if sign_btn:
        if email and password:
            try:
                if sb_auth is None:
                    raise Exception("Supabase auth client not configured")

                # TODO: Wrap direct sb_auth calls again if auth abstraction is restored later.
                if st.session_state["is_signing_in"]:
                    response = sb_auth.auth.sign_in_with_password({"email": email, "password": password})
                else:
                    sign_up_payload = {"email": email, "password": password}
                    if os.getenv("REDIRECT_URL"):
                        sign_up_payload["options"] = {"email_redirect_to": os.getenv("REDIRECT_URL")}
                    response = sb_auth.auth.sign_up(sign_up_payload)

                if response and response.session and response.user:
                    save_session(response.session, response.user)
                    st.rerun()
                elif st.session_state["is_signing_in"]:
                    st.error("Sign in did not return a valid session. Please try again.")
                else:
                    st.info("Sign up succeeded. Please verify your email before signing in.")

            except Exception as e:
                st.error(f"Sign in failed\n\nError: {str(e)}" if st.session_state["is_signing_in"] else f"Sign up failed\n\nError: {str(e)}")
        else:
            st.error("Both email and password should be filled")


# SHOW AUTH UI
if not is_authenticated():
    restore_session()

if not is_authenticated():
    show_auth_ui()
    st.stop()


# SELECT PRE-EXISTING SESSION
def select_pre_existing_session(session_id):
    st.session_state["session_id"] = session_id
    resp = api_call("GET", f"/api/sessions/{session_id}")
    if resp.status_code == 200:
        st.session_state["chat_history"] = resp.json()
    else:
        st.session_state["chat_history"] = []


# CREATE NEW SESSION
def create_new_chat():
    st.session_state["chat_history"] = []
    st.session_state["session_id"] = str(uuid.uuid4())
    resp = api_call("GET", "/api/sessions")
    if resp.status_code == 200:
        st.session_state["all_session_summaries"] = resp.json()
    else:
        st.session_state["all_session_summaries"] = []


# LOGOUT USER
def logout_user():
    clear_session()


# CREATE BASIC UI
if is_authenticated():
    st.title("Chat with the Multi-Agent AI")
    st.write("Type your message below and press Enter to chat with the AI agent.")
    st.html("<br >")
    st.html("<br >")


# FUNCTION TO STREAM CHAT RESPONSE
def stream_chat_response(message):
    headers = {"Authorization": f"Bearer {st.session_state['access_token']}"}
    payload = {"message": message, "thread_id": st.session_state["session_id"]}

    with requests.post(f"{BACKEND_URL}/chat_stream", json=payload, headers=headers, stream=True, timeout=300) as response:
        if response.status_code == 401 and refresh_session():
            headers["Authorization"] = f"Bearer {st.session_state['access_token']}"
            with requests.post(f"{BACKEND_URL}/chat_stream", json=payload, headers=headers, stream=True, timeout=300) as retry_response:
                if retry_response.status_code != 200:
                    yield f"Error: {retry_response.status_code}"
                    return

                for line in retry_response.iter_lines(decode_unicode=True):
                    if line:
                        if line.startswith("data:"):
                            json_str = line[5:].strip()
                            try:
                                data = json.loads(json_str)
                                if data.get("type") == "content":
                                    yield data.get("content", "")
                                elif data.get("type") == "error":
                                    yield f"\n\n**Error:** {data.get('content', 'Unknown error')}"
                            except json.JSONDecodeError:
                                pass
            return

        if response.status_code != 200:
            yield f"Error: {response.status_code}"
            return

        for line in response.iter_lines(decode_unicode=True):
            if line:
                if line.startswith("data:"):
                    json_str = line[5:].strip()
                    try:
                        data = json.loads(json_str)
                        if data.get("type") == "content":
                            yield data.get("content", "")
                        elif data.get("type") == "error":
                            yield f"\n\n**Error:** {data.get('content', 'Unknown error')}"
                    except json.JSONDecodeError:
                        pass


# SHOW ALL SESSIONS IN SIDEBAR
if is_authenticated():
    with st.sidebar:
        col_1, col_2 = st.columns(2)
        col_1.button(label="New Chat", key=str(uuid.uuid4()), on_click=create_new_chat)
        col_2.button(label="Logout", key=str(uuid.uuid4()), on_click=logout_user)

        st.html("<h3>All chats</h3>")

        resp = api_call("GET", "/api/sessions")
        if resp.status_code == 200:
            st.session_state["all_session_summaries"] = resp.json()
        else:
            st.session_state["all_session_summaries"] = []

        if len(st.session_state["all_session_summaries"]) == 0:
            st.write("No sessions found")
        else:
            for session in st.session_state["all_session_summaries"]:
                st.button(
                    label=session["first_query"][:20],
                    key=str(session["id"]),
                    on_click=select_pre_existing_session,
                    args=[session["session_id"]],
                )


# SHOW CHAT HISTORY
if is_authenticated():
    for msg in st.session_state["chat_history"]:
        with st.chat_message(msg["role"]):
            if msg["role"] == "ai":
                st.markdown(msg["content"], unsafe_allow_html=False)
            else:
                st.write(msg["content"])


# USER IS SIGNED IN AND HAS A SESSION
if is_authenticated():
    prompt = st.chat_input("Talk to the AI agent here...")

    if prompt:
        st.chat_message("human").write(prompt)
        st.session_state["chat_history"].append({"role": "human", "content": prompt})

        api_call(
            "POST",
            "/api/sessions/chat",
            json={
                "session_id": st.session_state["session_id"],
                "role": "human",
                "content": prompt,
            },
        )

        with st.spinner("Thinking..."):
            ai_msg_ui = st.chat_message("ai")
            msg_placeholder = ai_msg_ui.empty()
            full_response = ""

            for chunk in stream_chat_response(prompt):
                full_response += chunk
                clean_response = clean_text(full_response)
                msg_placeholder.markdown(format_latex_markdown(clean_response), unsafe_allow_html=False)

            final_ai_content = format_latex_markdown(clean_text(full_response))
            st.session_state["chat_history"].append({"role": "ai", "content": final_ai_content})

            api_call(
                "POST",
                "/api/sessions/chat",
                json={
                    "session_id": st.session_state["session_id"],
                    "role": "ai",
                    "content": final_ai_content,
                },
            )

            resp = api_call("GET", "/api/sessions")
            if resp.status_code == 200:
                st.session_state["all_session_summaries"] = resp.json()

            st.rerun()
