# Campus Notice Hub

A prototype college notice portal built with Streamlit, Python, SQLite, and Gemini through the `google-genai` SDK.

## Features

- Student view with latest notices first.
- Admin view to publish, edit, delete, and download image/PDF attachments.
- Optional Gemini assistant that answers questions from the published notices.
- Local SQLite database and uploads folder, suitable for a classroom prototype.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

Set `GEMINI_API_KEY` and `ADMIN_PASSWORD` in your environment before starting the app. The prototype admin password defaults to `admin123` only when no environment value is provided; change it before sharing the app.

The app creates `data/notices.db` and stores attachments in `data/uploads/` on first run. For a production version, replace local storage and the simple password with proper authentication, cloud storage, and a hosted database.
