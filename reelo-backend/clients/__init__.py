"""AI provider clients (Module 3). The ABC + DTOs live in :mod:`clients.base`;
concrete clients and the registry are added by ``reelo-ai-services``."""

from clients.base import (
    AIClient,
    AuthConfig,
    CallContext,
    ImageRequest,
    ImageResult,
    InvalidKeyError,
    KeyStore,
    NotSupportedError,
    ProviderUnavailableError,
    ScriptRequest,
    ScriptResult,
    ServiceConfig,
    Task,
    UsageLogger,
    VoiceRequest,
    VoiceResult,
)

__all__ = [
    "AIClient",
    "AuthConfig",
    "CallContext",
    "ImageRequest",
    "ImageResult",
    "InvalidKeyError",
    "KeyStore",
    "NotSupportedError",
    "ProviderUnavailableError",
    "ScriptRequest",
    "ScriptResult",
    "ServiceConfig",
    "Task",
    "UsageLogger",
    "VoiceRequest",
    "VoiceResult",
]
