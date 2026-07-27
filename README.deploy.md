# Google Cloud Run deployment

## Prerequisites

- A Google Cloud project with Cloud Run, Artifact Registry or Container Registry, and Cloud Build enabled.
- Docker installed locally if you want to test the image before deployment.
- Firebase project configured for Authentication and Firestore.

## Required environment variables

Set these in Cloud Run service variables:

- `PORT=8080`
- `FRONTEND_ORIGINS=https://<your-domain>`
- `ADMIN_REGISTRATION_KEY=<secure admin key>`
- `FIREBASE_CREDENTIALS_JSON=<service-account-json-content>`
- `VITE_FIREBASE_API_KEY=<firebase-api-key>`
- `VITE_FIREBASE_AUTH_DOMAIN=<firebase-auth-domain>`
- `VITE_FIREBASE_PROJECT_ID=<firebase-project-id>`
- `VITE_FIREBASE_STORAGE_BUCKET=<firebase-storage-bucket>`
- `VITE_FIREBASE_MESSAGING_SENDER_ID=<firebase-messaging-sender-id>`
- `VITE_FIREBASE_APP_ID=<firebase-app-id>`

## Build and deploy

Run the following commands after replacing the project id:

```bash
gcloud builds submit --tag gcr.io/<PROJECT_ID>/the-nowl-all-in-one

gcloud run deploy the-nowl-all-in-one \
  --image gcr.io/<PROJECT_ID>/the-nowl-all-in-one \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars PORT=8080
```

## Notes

- The Flask app serves the built Vite frontend from the same container.
- Cloud Run will use port 8080, which is set in the container image.
- For production, configure your Firebase credentials securely via environment variables rather than a local JSON file.
