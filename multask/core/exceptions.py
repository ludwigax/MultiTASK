"""
Custom exception hierarchy for multask package.

This module defines specific exception types for different error scenarios
that require different handling strategies in concurrent task execution.
"""

from enum import Enum
from typing import Optional, Any


class ErrorSeverity(Enum):
    """Severity levels for different types of errors."""
    RECOVERABLE = "recoverable"      # Can retry with backoff
    RATE_LIMITED = "rate_limited"    # Need rate limiting adjustment
    CIRCUIT_BREAKER = "circuit_breaker"  # Trigger circuit breaker
    FATAL = "fatal"                  # Unrecoverable, stop execution


class MultaskError(Exception):
    """Base exception class for all multask-related errors."""
    
    def __init__(self, message: str, severity: ErrorSeverity = ErrorSeverity.FATAL, 
                 original_error: Optional[Exception] = None, **kwargs):
        super().__init__(message)
        self.severity = severity
        self.original_error = original_error
        self.metadata = kwargs
    
    def __str__(self):
        base_msg = super().__str__()
        if self.original_error:
            return f"{base_msg} (caused by: {self.original_error})"
        return base_msg


class InternetError(MultaskError):
    """
    Internet connectivity issues that should trigger circuit breaker.
    
    This includes DNS resolution failures, connection timeouts, 
    and other network infrastructure problems.
    """
    
    def __init__(self, message: str = "Internet connectivity error", 
                 original_error: Optional[Exception] = None, **kwargs):
        super().__init__(
            message, 
            severity=ErrorSeverity.CIRCUIT_BREAKER,
            original_error=original_error,
            **kwargs
        )


class FatalError(MultaskError):
    """
    Fatal programming errors that should stop execution immediately.
    
    This includes invalid parameters, configuration errors,
    and other user programming mistakes.
    """
    
    def __init__(self, message: str = "Fatal programming error", 
                 original_error: Optional[Exception] = None, **kwargs):
        super().__init__(
            message,
            severity=ErrorSeverity.FATAL, 
            original_error=original_error,
            **kwargs
        )


class RateLimitError(MultaskError):
    """
    Rate limiting errors that require backoff and rate adjustment.
    
    This includes HTTP 429 responses and API quota exceeded errors.
    """
    
    def __init__(self, message: str = "Rate limit exceeded", 
                 retry_after: Optional[int] = None,
                 original_error: Optional[Exception] = None, **kwargs):
        super().__init__(
            message,
            severity=ErrorSeverity.RATE_LIMITED,
            original_error=original_error,
            retry_after=retry_after,
            **kwargs
        )
        self.retry_after = retry_after


class NetworkInstabilityError(MultaskError):
    """
    Temporary network instability that can be retried.
    
    This includes temporary connection drops, server timeouts,
    and other transient network issues.
    """
    
    def __init__(self, message: str = "Network instability detected", 
                 original_error: Optional[Exception] = None, **kwargs):
        super().__init__(
            message,
            severity=ErrorSeverity.RECOVERABLE,
            original_error=original_error,
            **kwargs
        )


class CircuitBreakerError(MultaskError):
    """
    Error indicating that the circuit breaker is open.
    
    This is raised when too many failures have occurred
    and the circuit breaker prevents further execution.
    """
    
    def __init__(self, message: str = "Circuit breaker is open", 
                 failure_count: int = 0, **kwargs):
        super().__init__(
            message,
            severity=ErrorSeverity.CIRCUIT_BREAKER,
            failure_count=failure_count,
            **kwargs
        )
        self.failure_count = failure_count


def classify_exception(exc: Exception) -> MultaskError:
    """
    Classify a generic exception into appropriate MultaskError type.
    
    Args:
        exc: The original exception to classify
        
    Returns:
        MultaskError: Classified exception with appropriate severity
    """
    import aiohttp
    import asyncio
    from requests.exceptions import ConnectionError, Timeout, HTTPError
    
    # Network connectivity issues
    if isinstance(exc, (ConnectionError, aiohttp.ClientConnectorError)):
        return InternetError("Connection failed", original_error=exc)
    
    # Timeouts - could be network instability or internet issues
    if isinstance(exc, (Timeout, asyncio.TimeoutError, aiohttp.ServerTimeoutError)):
        return NetworkInstabilityError("Request timeout", original_error=exc)
    
    # Rate limiting
    if isinstance(exc, aiohttp.ClientResponseError) and exc.status == 429:
        retry_after = None
        if hasattr(exc, 'headers') and 'Retry-After' in exc.headers:
            try:
                retry_after = int(exc.headers['Retry-After'])
            except (ValueError, TypeError):
                pass
        return RateLimitError("Rate limit exceeded", retry_after=retry_after, original_error=exc)
    
    if isinstance(exc, HTTPError) and hasattr(exc, 'response') and exc.response.status_code == 429:
        return RateLimitError("Rate limit exceeded", original_error=exc)
    
    # Client errors (4xx) - usually programming errors
    if isinstance(exc, aiohttp.ClientResponseError) and 400 <= exc.status < 500 and exc.status != 429:
        return FatalError(f"Client error: {exc.status}", original_error=exc)
    
    if isinstance(exc, HTTPError) and hasattr(exc, 'response') and 400 <= exc.response.status_code < 500 and exc.response.status_code != 429:
        return FatalError(f"Client error: {exc.response.status_code}", original_error=exc)
    
    # Server errors (5xx) - temporary issues
    if isinstance(exc, (aiohttp.ClientResponseError, HTTPError)):
        if isinstance(exc, aiohttp.ClientResponseError) and exc.status >= 500:
            return NetworkInstabilityError(f"Server error: {exc.status}", original_error=exc)
        elif isinstance(exc, HTTPError) and hasattr(exc, 'response') and exc.response.status_code >= 500:
            return NetworkInstabilityError(f"Server error: {exc.response.status_code}", original_error=exc)
    
    # Programming errors
    if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)):
        return FatalError("Programming error", original_error=exc)
    
    # Default: treat as network instability for safety
    return NetworkInstabilityError("Unknown error", original_error=exc)
