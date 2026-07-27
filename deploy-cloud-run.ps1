param(
    [Parameter(Mandatory=$true)]
    [string]$ProjectId,

    [string]$Region = "us-central1",
    [string]$ServiceName = "the-nowl-all-in-one"
)

$ErrorActionPreference = "Stop"

Write-Host "Building and deploying to Google Cloud Run..."

gcloud config set project $ProjectId

gcloud builds submit --tag "gcr.io/$ProjectId/$ServiceName"

gcloud run deploy $ServiceName `
    --image "gcr.io/$ProjectId/$ServiceName" `
    --platform managed `
    --region $Region `
    --allow-unauthenticated `
    --set-env-vars PORT=8080

Write-Host "Deployment started."
Write-Host "Next, configure these environment variables in the Cloud Run service console:"
Write-Host "- FRONTEND_ORIGINS"
Write-Host "- ADMIN_REGISTRATION_KEY"
Write-Host "- FIREBASE_CREDENTIALS_JSON"
Write-Host "- VITE_FIREBASE_API_KEY"
Write-Host "- VITE_FIREBASE_AUTH_DOMAIN"
Write-Host "- VITE_FIREBASE_PROJECT_ID"
Write-Host "- VITE_FIREBASE_STORAGE_BUCKET"
Write-Host "- VITE_FIREBASE_MESSAGING_SENDER_ID"
Write-Host "- VITE_FIREBASE_APP_ID"
