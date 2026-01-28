"""Custom exceptions for the application."""
from typing import Any, Dict, Optional


class AppException(Exception):
    """Base exception for application errors."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class AuthenticationError(AppException):
    """Raised when authentication fails."""
    pass


class AuthorizationError(AppException):
    """Raised when user is not authorized to perform an action."""
    pass


class ResourceNotFoundError(AppException):
    """Raised when a requested resource is not found."""
    pass


class ConflictError(AppException):
    """Raised when there is a conflict (e.g., duplicate email)."""
    pass


class ValidationError(AppException):
    """Raised when validation fails."""
    pass


class MLServiceError(Exception):
    """Base exception for ML service communication errors."""
    def __init__(self, message: str, retry_after: int = 30):
        self.message = message
        self.retry_after = retry_after
        super().__init__(self.message)


class MLServiceUnavailable(MLServiceError):
    """Raised when ML service is not responding."""
    pass


class MLServiceTimeout(MLServiceError):
    """Raised when ML service request times out."""
    pass


class MLGenerationFailed(MLServiceError):
    """Raised when model generation fails in ML service."""
    def __init__(self, message: str, error_details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.error_details = error_details or {}
