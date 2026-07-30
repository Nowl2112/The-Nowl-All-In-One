"""Firebase initialization and shared Firestore access."""

import core

initialize_firebase = core._initialize_firebase

def get_db():
    return core.FIRESTORE_DB

def is_configured():
    return core.FIREBASE_CONFIGURED
