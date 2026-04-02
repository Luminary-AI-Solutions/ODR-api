import logging
from .config import AppSettings, ApiKeys
from typing import Optional
from .state import initialize_firebase_sync
from firebase_admin.firestore import Client

logger = logging.getLogger(__name__)

_settings: Optional[AppSettings] = None
_api_keys: Optional[ApiKeys] = None

def get_settings() -> AppSettings:
    """Dependency function to get application settings."""
    global _settings
    if _settings is None:
        _settings = AppSettings()
    return _settings

def get_api_keys() -> ApiKeys:
    """Dependency function to get API keys."""
    global _api_keys
    if _api_keys is None:
        _api_keys = ApiKeys.from_settings(get_settings())
    return _api_keys

def get_firestore_db() -> Optional[Client]:
    """
    Dependency function to get the Firestore client instance.
    Initializes Firebase if necessary.
    Returns None if initialization fails, allowing optional usage.
    """
    firestore_client = initialize_firebase_sync(get_settings())
    # No longer raises HTTPException. Routes/Orchestrators must handle None.
    return firestore_client 