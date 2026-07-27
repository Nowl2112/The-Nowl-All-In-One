# ==========================================================
# Stage 1: Build the React/Vite frontend
# ==========================================================
FROM node:22-alpine AS frontend-builder

WORKDIR /frontend

# Copy dependency files first for better Docker caching
COPY frontend/package.json frontend/package-lock.json ./

RUN npm ci

# Copy the rest of the frontend source
COPY frontend/ ./

# Build arguments used by Vite during npm run build
ARG VITE_API_BASE=""
ARG VITE_FIREBASE_API_KEY
ARG VITE_FIREBASE_AUTH_DOMAIN
ARG VITE_FIREBASE_PROJECT_ID
ARG VITE_FIREBASE_STORAGE_BUCKET
ARG VITE_FIREBASE_MESSAGING_SENDER_ID
ARG VITE_FIREBASE_APP_ID

ENV VITE_API_BASE=${VITE_API_BASE}
ENV VITE_FIREBASE_API_KEY=${VITE_FIREBASE_API_KEY}
ENV VITE_FIREBASE_AUTH_DOMAIN=${VITE_FIREBASE_AUTH_DOMAIN}
ENV VITE_FIREBASE_PROJECT_ID=${VITE_FIREBASE_PROJECT_ID}
ENV VITE_FIREBASE_STORAGE_BUCKET=${VITE_FIREBASE_STORAGE_BUCKET}
ENV VITE_FIREBASE_MESSAGING_SENDER_ID=${VITE_FIREBASE_MESSAGING_SENDER_ID}
ENV VITE_FIREBASE_APP_ID=${VITE_FIREBASE_APP_ID}

RUN npm run build


# ==========================================================
# Stage 2: Run the Flask backend
# ==========================================================
FROM python:3.12-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy backend source files
COPY backend/ ./

# Copy the Vite build into the Flask application
COPY --from=frontend-builder /frontend/dist ./frontend_dist

EXPOSE 8080

# app:app means:
# first "app"  = app.py
# second "app" = Flask application variable
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 0 app:app"]