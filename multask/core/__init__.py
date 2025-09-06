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

from .rate_limiter import BasicRateLimiter, RateLimitConfig
from .executors import AsyncExecutor, ThreadExecutor
from .controllers import (
    BaseController, BasicController, SmartController,
    BasicControllerConfig, SmartControllerConfig,
    UserInteractionHandler
)

# Backward compatibility aliases
RateLimiter = BasicRateLimiter

__all__ = [
    # Exceptions
    'MultaskError',
    'InternetError', 
    'FatalError',
    'RateLimitError',
    'NetworkInstabilityError',
    'CircuitBreakerError',
    # Core components
    'RateLimiter',  # Backward compatibility alias
    'BasicRateLimiter',
    'AsyncExecutor',
    'ThreadExecutor',
    # Controllers
    'BaseController',
    'BasicController',
    'SmartController',
    'BasicControllerConfig',
    'SmartControllerConfig',
    'UserInteractionHandler'
]
