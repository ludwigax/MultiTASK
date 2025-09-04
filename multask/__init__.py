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

# Legacy compatibility (deprecated)
from .req.async_request import AsyncCake
from .req.thread_request import ThreadedCake

# API interfaces with graceful error handling
from . import apis

# Legacy easy-use functions (deprecated, use apis instead)
from .easy_use import (
    batch_query_openai,
    oai_price_calculator,
    batch_query_crossref
)