# Core execution framework
from .core import (
    AsyncExecutor, 
    ThreadExecutor,
    RateLimiter,
    CircuitBreaker,
    # Exceptions
    MultaskError,
    InternetError,
    FatalError,
    RateLimitError,
    NetworkInstabilityError,
    CircuitBreakerError
)

# API interfaces with graceful error handling
from . import apis