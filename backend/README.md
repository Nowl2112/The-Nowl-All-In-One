# The Nowl modular backend

This package is a behavior-preserving split of the supplied 7,491-line
`app.py`. All 49 route declarations are registered through feature Blueprints.

## Structure

```text
app.py                 Application factory and development entry point
config.py              Environment-backed Flask configuration
extensions.py          Flask-CORS initialization
core.py                Existing shared helpers and domain logic
routes/                 Feature-specific HTTP Blueprints
services/firebase.py   Firebase initialization and database access
word_list.py            Wordle answer list
requirements.txt       Python dependencies
.env.example           Environment variable template
app_original.py.txt    Unmodified source supplied for reference
```

## Migrate

1. Back up your current backend folder.
2. Copy these files into the backend folder.
3. If your old project has a larger `word_list.py`, keep that file instead of
   the starter list included here.
4. Keep your existing `.env` and Firebase credentials; do not commit either.
5. Install and run:

```bash
python -m pip install -r requirements.txt
python app.py
```

For Render, the start command can remain:

```text
gunicorn app:app
```

The `core.py` module intentionally retains shared logic in one place so this
first migration does not silently change route behavior. It can later be split
further into calendar, news, Telegram, task-board, and Wordle services one
feature at a time.
