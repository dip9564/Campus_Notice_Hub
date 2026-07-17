import hashlib
import html
import os
import re
import sqlite3
import base64
import time
import unicodedata
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import streamlit as st
from dotenv import load_dotenv
load_dotenv()

try:
    from google import genai
except ImportError:
    genai = None

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    PdfReader = None
    PdfWriter = None

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "notices.db"
APP_TIMEZONE = ZoneInfo("Asia/Kolkata")
DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

st.set_page_config(page_title="Campus Notice Hub", page_icon="📢", layout="wide")
st.markdown("""
<style>
div.stButton > button[kind="secondary"] { border-radius: 18px; }
div.stDownloadButton > button { border-radius: 18px !important; }
.chat-launcher { position: fixed; bottom: 20px; right: 24px; z-index: 999; }
.notice-chat-row { display: flex; margin: 1px 0 1px; width: 100%; }
.notice-chat-row.user { justify-content: flex-end; }
.notice-chat-row.assistant { justify-content: flex-start; }
.notice-chat-bubble { color: #f5f5f5; font-size: 1.15rem; line-height: 1.5; padding: 14px 20px; max-width: 72%; word-wrap: break-word; white-space: pre-wrap; }
.notice-chat-row.user .notice-chat-bubble { background: #2563eb; border-radius: 30px 30px 6px 30px; }
.notice-chat-row.assistant .notice-chat-bubble { background: #2b2d31; border-radius: 30px 30px 30px 6px; }
            
.notice-thinking { display: flex; align-items: center; gap: 7px; }
.notice-thinking-dot { width: 8px; height: 8px; background: #bdbdbd; border-radius: 50%; animation: notice-bounce 1.4s infinite ease-in-out; }
.notice-thinking-dot:nth-child(2) { animation-delay: .2s; }
.notice-thinking-dot:nth-child(3) { animation-delay: .4s; }
            
@keyframes notice-bounce { 0%, 80%, 100% { transform: scale(.6); opacity: .4; } 40% { transform: scale(1); opacity: 1; } }
.notice-cursor { animation: notice-blink 1s infinite; }
@keyframes notice-blink { 50% { opacity: 0; } }
[data-testid="stChatInput"] { position: fixed; bottom: 2.5rem; left: 50%; transform: translateX(-50%); width: min(850px, calc(100% - 2rem)); z-index: 1000; background: transparent; }
[data-testid="stChatInput"] > div { border-radius: 28px; box-shadow: 0 4px 18px rgba(0, 0, 0, .25); }
.notice-chat-row:last-of-type { margin-bottom: 10px; }
</style>
""", unsafe_allow_html=True)


def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with get_connection() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                admin_name TEXT NOT NULL DEFAULT 'Admin',
                description TEXT NOT NULL,
                notice_date TEXT NOT NULL,
                important_date TEXT,
                file_name TEXT,
                file_path TEXT,
                file_type TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(notices)")}
        if "admin_name" not in columns:
            connection.execute("ALTER TABLE notices ADD COLUMN admin_name TEXT NOT NULL DEFAULT 'Admin'")


def list_notices():
    remove_expired_notices()
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM notices ORDER BY notice_date DESC, created_at DESC"
        ).fetchall()


def remove_expired_notices():
    today = datetime.now(APP_TIMEZONE).date().isoformat()
    with get_connection() as connection:
        expired_notices = connection.execute(
            "SELECT file_path FROM notices WHERE important_date IS NOT NULL AND important_date < ?",
            (today,),
        ).fetchall()
        connection.execute(
            "DELETE FROM notices WHERE important_date IS NOT NULL AND important_date < ?",
            (today,),
        )
    for notice in expired_notices:
        if notice["file_path"]:
            Path(notice["file_path"]).unlink(missing_ok=True)


def save_notice(admin_name, title, description, notice_date, important_date, uploaded_file, notice_id=None):
    now = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
    notice_timestamp = now
    file_name = file_path = file_type = None
    if uploaded_file:
        original_name = Path(uploaded_file.name).name
        safe_name = unicodedata.normalize("NFKD", original_name).encode("ascii", "ignore").decode("ascii")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", safe_name).strip("._") or "attachment"
        file_name = safe_name
        file_path = str(UPLOAD_DIR / f"{hashlib.sha256((now + safe_name).encode()).hexdigest()[:12]}_{safe_name}")
        file_type = uploaded_file.type
        file_bytes = uploaded_file.getvalue()
        if file_type == "application/pdf" and PdfReader and PdfWriter:
            reader = PdfReader(BytesIO(file_bytes))
            if len(reader.pages) > 1:
                first_page = reader.pages[0]
                first_page_text = (first_page.extract_text() or "").strip()
                first_page_contents = first_page.get_contents()
                if not first_page_text and first_page_contents is None:
                    writer = PdfWriter()
                    for page in reader.pages[1:]:
                        writer.add_page(page)
                    output = BytesIO()
                    writer.write(output)
                    file_bytes = output.getvalue()
        Path(file_path).write_bytes(file_bytes)

    with get_connection() as connection:
        if notice_id:
            old = connection.execute("SELECT file_path, notice_date FROM notices WHERE id = ?", (notice_id,)).fetchone()
            notice_timestamp = old["notice_date"]
            if file_path is None:
                connection.execute(
                    "UPDATE notices SET admin_name=?, title=?, description=?, notice_date=?, important_date=?, updated_at=? WHERE id=?",
                    (admin_name, title, description, notice_timestamp, important_date.isoformat() if important_date else None, now, notice_id),
                )
            else:
                connection.execute(
                    "UPDATE notices SET admin_name=?, title=?, description=?, notice_date=?, important_date=?, file_name=?, file_path=?, file_type=?, updated_at=? WHERE id=?",
                    (admin_name, title, description, notice_timestamp, important_date.isoformat() if important_date else None, file_name, file_path, file_type, now, notice_id),
                )
                if old and old["file_path"]:
                    Path(old["file_path"]).unlink(missing_ok=True)
        else:
            connection.execute(
                "INSERT INTO notices (admin_name, title, description, notice_date, important_date, file_name, file_path, file_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (admin_name, title, description, notice_timestamp, important_date.isoformat() if important_date else None, file_name, file_path, file_type, now, now),
            )


def delete_notice(notice_id):
    with get_connection() as connection:
        notice = connection.execute("SELECT file_path FROM notices WHERE id = ?", (notice_id,)).fetchone()
        connection.execute("DELETE FROM notices WHERE id = ?", (notice_id,))
    if notice and notice["file_path"]:
        Path(notice["file_path"]).unlink(missing_ok=True)


def set_flash_message(message, message_type="success"):
    st.session_state.flash_message = message
    st.session_state.flash_message_type = message_type


def show_flash_message():
    message = st.session_state.pop("flash_message", None)
    message_type = st.session_state.pop("flash_message_type", "success")
    if message:
        getattr(st, message_type)(message)


def notice_is_new(notice):
    created_at = datetime.fromisoformat(notice["created_at"])
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return datetime.now(APP_TIMEZONE) - created_at.astimezone(APP_TIMEZONE) < timedelta(hours=12)


def format_notice_timestamp(timestamp):
    timestamp_value = datetime.fromisoformat(timestamp)
    if timestamp_value.tzinfo is None:
        timestamp_value = timestamp_value.replace(tzinfo=timezone.utc)
    return timestamp_value.astimezone(APP_TIMEZONE).strftime("%Y-%m-%d [%I:%M %p]")


def extract_notice_context(notices):
    context = []
    for notice in notices:
        latest_label = "🔔🆕 LATEST NOTICE" if notice_is_new(notice) else ""
        text = f"{latest_label}\nTitle: {notice['title']}\nDescription: {notice['description']}\nNotice date: {notice['notice_date']}\nDeadline date: {notice['important_date']}"
        if notice["file_path"] and notice["file_type"] == "application/pdf" and PdfReader:
            try:
                text += "\nPDF text: " + "\n".join(page.extract_text() or "" for page in PdfReader(notice["file_path"].open("rb")).pages)
            except Exception:
                pass
        context.append(text)
    return "\n\n---\n\n".join(context)


def ask_gemini(question, notices, chat_messages):
    api_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", None)
    if not api_key or genai is None:
        return "Gemini is not configured yet. Add GEMINI_API_KEY to your environment or Streamlit secrets to enable AI answers."
    client = genai.Client(api_key=api_key)
    recent_messages = chat_messages[-20:]
    conversation_context = "\n".join(
        f"{message['role'].upper()}: {message['content']}"
        for message in recent_messages
    )
    prompt = f"""You are a helpful college notice assistant. Answer the CURRENT STUDENT QUESTION only. Use the previous conversation only as extra context. Answer only from the notice context and conversation context. If the answer is not present, say that clearly. Mention the relevant notice title and date when possible. If the student asks for the latest notice, prioritize the notice marked '🔔🆕 LATEST NOTICE'. Keep the answer concise.

NOTICE CONTEXT:
{extract_notice_context(notices)}

PREVIOUS CONVERSATION CONTEXT (last 20 messages from this chat only):
{conversation_context}

CURRENT STUDENT QUESTION:
{question}"""
    try:
        contents = [prompt]
        for notice in notices:
            file_path = notice["file_path"]
            if file_path and Path(file_path).exists() and notice["file_type"] in {"application/pdf", "image/png", "image/jpeg"}:
                contents.append(client.files.upload(file=file_path))
        model_name = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").removeprefix("models/") or st.secrets.get("GEMINI_MODEL", None)
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
        )
        return response.text
    except Exception as error:
        return (
            "Gemini could not generate a reply.\n\n"
            f"Model: {model_name}\n"
            f"Error: {type(error).__name__}: {error}\n\n"
            "Check GEMINI_API_KEY, GEMINI_MODEL, internet access, and API quota."
        )


def render_notice(notice, admin=False):
    with st.container(border=True):
        title = f"{notice['title']} 🔔🆕" if notice_is_new(notice) else notice["title"]
        st.subheader(title)
        st.caption(f"Posted by: {notice['admin_name']}")
        st.caption(
            f"Published: {format_notice_timestamp(notice['notice_date'])}  •  "
            f"Deadline: {notice['important_date']}"
        )
        st.markdown(
            f'<div style="white-space: pre-wrap; line-height: 1.6; margin-bottom: 1rem;">{html.escape(notice["description"])}</div>',
            unsafe_allow_html=True,
        )
        if notice["file_path"] and Path(notice["file_path"]).exists():
            attachment_key = f"show_attachment_{notice['id']}"
            show_col, download_col,_ = st.columns((1,1,2))
            if show_col.button(
                "Hide attachment" if st.session_state.get(attachment_key) else "Show attachment",
                key=f"attachment_button_{notice['id']}",
            ):
                st.session_state[attachment_key] = not st.session_state.get(attachment_key, False)
                st.rerun()
            download_col.download_button(
                "Download attachment",
                data=Path(notice["file_path"]).read_bytes(),
                file_name=notice["file_name"],
                mime=notice["file_type"],
                key=f"download_attachment_{notice['id']}",
            )
            if st.session_state.get(attachment_key):
                if notice["file_type"] in {"image/png", "image/jpeg"}:
                    st.image(notice["file_path"], use_container_width=True)
                elif notice["file_type"] == "application/pdf":
                    st.pdf(notice["file_path"], height=700)
                else:
                    st.warning("This attachment type cannot be previewed in the browser.")
        if admin:
            edit_col, delete_col = st.columns(2)
            if edit_col.button("Edit", key=f"edit_{notice['id']}"):
                st.session_state.edit_notice_id = notice["id"]
                st.rerun()
            if delete_col.button("Delete", key=f"delete_{notice['id']}", type="secondary"):
                delete_notice(notice["id"])
                set_flash_message("Notice deleted successfully.")
                st.rerun()


def admin_page(notices):
    st.header("Admin dashboard")
    st.caption("Create, update, and remove campus notices.")
    edit_id = st.session_state.get("edit_notice_id")
    existing = next((notice for notice in notices if notice["id"] == edit_id), None)
    with st.form("notice_form", clear_on_submit=True):
        title_col, admin_col = st.columns(2)
        title = title_col.text_input("Notice title", value=existing["title"] if existing else "")
        admin_name = admin_col.text_input("Admin name", value=existing["admin_name"] if existing else "")
        description = st.text_area("Notice details", value=existing["description"] if existing else "")
        current_notice_time = datetime.fromisoformat(existing["notice_date"]) if existing else datetime.now(APP_TIMEZONE)
        st.text_input(
            "Notice date and time",
            value=current_notice_time.strftime("%Y-%m-%d %H:%M"),
            disabled=True,
        )
        deadline_date = st.date_input(
            "Deadline date",
            value=datetime.fromisoformat(existing["important_date"]).date() if existing and existing["important_date"] else datetime.now(APP_TIMEZONE).date(),
        )
        uploaded_file = st.file_uploader("Attachment (PNG, JPG, PDF)", type=["png", "jpg", "jpeg", "pdf"])
        submitted = st.form_submit_button("Update notice" if existing else "Publish notice", type="primary")
        if submitted:
            if not title.strip() or not admin_name.strip() or not description.strip():
                st.error("Admin name, title, and details are required.")
            else:
                save_notice(admin_name.strip(), title.strip(), description.strip(), None, deadline_date, uploaded_file, edit_id)
                st.session_state.pop("edit_notice_id", None)
                set_flash_message("Notice updated successfully." if edit_id else "Notice created successfully.")
                st.rerun()
    st.divider()
    st.subheader("Manage notices")
    for notice in notices:
        render_notice(notice, admin=True)


def student_page(notices):
    notice_tab, chatbot_tab = st.tabs(["Notice Board", "Chatbot"])

    with notice_tab:
        st.header("📢 Campus Notice Hub")
        st.subheader("Latest campus notices")
        st.caption("Stay up to date with announcements, events, deadlines, and exams.")
        if not notices:
            st.info("No notices have been published yet.")
        for notice in notices:
            render_notice(notice)

    with chatbot_tab:
        st.header("Notice assistant")
        st.caption("Ask questions about published notices, PDFs, images, dates, and campus information.")
        if "notice_chats" not in st.session_state:
            st.session_state.notice_chats = {
                "Chat 1": [
                    {"role": "assistant", "content": "Hello! Ask me about the published notices, dates, or campus information."}
                ]
            }
        if "notice_current_chat" not in st.session_state:
            st.session_state.notice_current_chat = "Chat 1"

        new_chat_col,_, chat_name_col = st.columns([1, 4,1])
        if new_chat_col.button("✛ New chat", key="new_notice_chat"):
            name = f"Chat {len(st.session_state.notice_chats) + 1}"
            st.session_state.notice_chats[name] = [
                {"role": "assistant", "content": "What would you like to know about the notices?"}
            ]
            st.session_state.notice_current_chat = name
            st.session_state.notice_chat_selector = name
            st.rerun()
        chat_name = chat_name_col.selectbox(
            "Conversation",
            list(st.session_state.notice_chats),
            index=list(st.session_state.notice_chats).index(st.session_state.notice_current_chat),
            label_visibility="collapsed",
            key="notice_chat_selector",
        )
        st.session_state.notice_current_chat = chat_name
        messages = st.session_state.notice_chats[chat_name]
        question = st.chat_input("Ask about a notice or important date", key="notice_chat_input")
        if question and question.strip():
            messages.append({"role": "user", "content": question.strip()})

        for message in messages:
            role = "user" if message["role"] == "user" else "assistant"
            safe_content = html.escape(message["content"])
            st.markdown(
                f'<div class="notice-chat-row {role}"><div class="notice-chat-bubble">{safe_content}</div></div>',
                unsafe_allow_html=True,
            )

        if question and question.strip():
            thinking_placeholder = st.empty()
            thinking_placeholder.markdown(
                '<div class="notice-chat-row assistant"><div class="notice-chat-bubble"><div class="notice-thinking">Thinking <div class="notice-thinking-dot"></div><div class="notice-thinking-dot"></div><div class="notice-thinking-dot"></div></div></div></div>',
                unsafe_allow_html=True,
            )
            answer = ask_gemini(question.strip(), notices, messages)
            thinking_placeholder.empty()
            response_placeholder = st.empty()
            typed_text = ""
            for character in answer:
                typed_text += character
                response_placeholder.markdown(
                    '<div class="notice-chat-row assistant"><div class="notice-chat-bubble">'
                    f'{html.escape(typed_text)}<span class="notice-cursor">▌</span>'
                    '</div></div>',
                    unsafe_allow_html=True,
                )
                time.sleep(0.005)
            response_placeholder.markdown(
                f'<div class="notice-chat-row assistant"><div class="notice-chat-bubble">{html.escape(answer)}</div></div>',
                unsafe_allow_html=True,
            )
            messages.append({"role": "assistant", "content": answer})
            st.rerun()


def main():
    initialize_database()
    show_flash_message()
    with st.sidebar.popover("Select role"):
        role = st.selectbox(
            "Select role",
            ["Student", "Admin"],
            label_visibility="collapsed",
            key="role_selector",
        )
    role = st.session_state.get("role_selector", "Student")
    if role == "Admin":
        password = st.sidebar.text_input("Admin password", type="password")
        expected = os.getenv("ADMIN_PASSWORD") or st.secrets.get("ADMIN_PASSWORD", None)
        if password != expected:
            st.info("Enter the admin password to continue.")
            return
    notices = list_notices()
    admin_page(notices) if role == "Admin" else student_page(notices)


if __name__ == "__main__":
    main()
