# Core execution framework
from .core.executors import AsyncExecutor, ThreadExecutor
from .core.rate_limiter import BasicRateLimiter as RateLimiter
from .core.exceptions import (
    MultaskError,
    InternetError,
    FatalError,
    RateLimitError,
    NetworkInstabilityError,
    CircuitBreakerError
)
from .core.controllers import (
    BaseController,
    BasicController,
    SmartController,
    BasicControllerConfig,
    SmartControllerConfig
)

# API interfaces with graceful error handling
from . import apis