"""
Core module for multask package.

This module contains the fundamental components for concurrent task execution:
- Custom exception hierarchy
- Rate limiting (RPM) management
- Base executor classes
- Circuit breaker patterns
"""

from .exceptions import (
    MultaskError,
    InternetError,
    FatalError, 
    RateLimitError,
    NetworkInstabilityError,
    CircuitBreakerError
)

from .rate_limiter import RateLimiter
from .executors import AsyncExecutor, ThreadExecutor
from .circuit_breaker import CircuitBreaker

__all__ = [
    # Exceptions
    'MultaskError',
    'InternetError', 
    'FatalError',
    'RateLimitError',
    'NetworkInstabilityError',
    'CircuitBreakerError',
    # Core components
    'RateLimiter',
    'AsyncExecutor',
    'ThreadExecutor', 
    'CircuitBreaker'
]
