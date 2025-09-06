"""
Adaptive execution controller with circuit breaker and intelligent rate control.

This module provides advanced execution control that combines circuit breaker patterns
with adaptive rate limiting, providing intelligent error handling and user interaction.
"""

import time
import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional, Callable, Any, Dict, Tuple
from dataclasses import dataclass

from .exceptions import CircuitBreakerError, ErrorSeverity, RateLimitError, classify_exception
from .rate_limiter import BasicRateLimiter, RateLimitConfig


class CircuitState(Enum):
    """States of the circuit breaker."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""
    failure_threshold: int = 5          # Failures before opening
    success_threshold: int = 3          # Successes to close from half-open
    timeout: float = 60.0              # Seconds before trying half-open
    reset_timeout: float = 300.0       # Seconds to fully reset failure count


@dataclass  
class AdaptiveControllerConfig:
    """Configuration for adaptive execution controller."""
    # Rate limiting config
    rate_limit_config: Optional[RateLimitConfig] = None
    
    # Circuit breaker config
    circuit_breaker_config: Optional[CircuitBreakerConfig] = None
    
    # Adaptive behavior config
    rate_limit_backoff_factor: float = 2.0      # Multiply delay by this on rate limit
    max_backoff_factor: float = 10.0            # Maximum backoff multiplier
    backoff_recovery_time: int = 300            # Seconds to recover from backoff
    adaptive_speed_enabled: bool = True         # Enable adaptive speed control
    
    def __post_init__(self):
        if self.rate_limit_config is None:
            self.rate_limit_config = RateLimitConfig()
        if self.circuit_breaker_config is None:
            self.circuit_breaker_config = CircuitBreakerConfig()


class BaseController(ABC):
    """
    Abstract base class for execution controllers.
    
    Provides unified interface for both basic and smart controllers.
    """
    
    @abstractmethod
    async def wait_for_slot(self) -> None:
        """Wait until a request slot is available."""
        pass
    
    @abstractmethod
    async def execute_task(
        self, 
        worker: Callable, 
        task_params: Dict[str, Any],
        max_retries: int = 3
    ) -> Tuple[bool, Any]:
        """
        Execute a single task with appropriate control.
        
        Args:
            worker: Function to execute
            task_params: Parameters to pass to worker
            max_retries: Maximum retry attempts
            
        Returns:
            Tuple of (success: bool, result: Any)
        """
        pass
    
    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Get current controller state information."""
        pass
    
    @abstractmethod
    def reset(self) -> None:
        """Reset controller to initial state."""
        pass
    
    @property
    @abstractmethod
    def effective_rpm(self) -> int:
        """Get current effective RPM."""
        pass
    
    @property
    @abstractmethod
    def current_rpm(self) -> int:
        """Get current actual requests per minute."""
        pass


class CircuitBreaker:
    """
    Circuit breaker implementation for fault tolerance.
    
    The circuit breaker monitors failures and can:
    - Block requests when failure rate is too high (OPEN state)
    - Allow limited testing when timeout expires (HALF_OPEN state)  
    - Resume normal operation when service recovers (CLOSED state)
    """
    
    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self.last_success_time = 0.0
        self._lock = asyncio.Lock()
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function with circuit breaker protection.
        
        Args:
            func: Function to execute
            *args, **kwargs: Arguments to pass to function
            
        Returns:
            Result of function execution
            
        Raises:
            CircuitBreakerError: If circuit is open
        """
        async with self._lock:
            await self._check_state()
            
            if self.state == CircuitState.OPEN:
                raise CircuitBreakerError(
                    f"Circuit breaker is OPEN after {self.failure_count} failures",
                    failure_count=self.failure_count
                )
        
        try:
            # Execute the function
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            # Record success
            await self._on_success()
            return result
            
        except Exception as exc:
            # Classify and handle the error
            from .exceptions import classify_exception
            multask_error = classify_exception(exc)
            
            # Only count certain types of errors as circuit breaker failures
            if multask_error.severity in [ErrorSeverity.CIRCUIT_BREAKER, ErrorSeverity.FATAL]:
                await self._on_failure()
            
            # Re-raise the original exception
            raise
    
    async def _check_state(self) -> None:
        """Check and update circuit breaker state."""
        now = time.time()
        
        if self.state == CircuitState.OPEN:
            # Check if we should transition to half-open
            if now - self.last_failure_time >= self.config.timeout:
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
        
        elif self.state == CircuitState.CLOSED:
            # Check if we should reset failure count
            if (self.failure_count > 0 and 
                now - self.last_failure_time >= self.config.reset_timeout):
                self.failure_count = 0
    
    async def _on_success(self) -> None:
        """Handle successful execution."""
        async with self._lock:
            self.last_success_time = time.time()
            
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.config.success_threshold:
                    # Service has recovered, close the circuit
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
    
    async def _on_failure(self) -> None:
        """Handle failed execution."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.state == CircuitState.HALF_OPEN:
                # Failed during testing, go back to open
                self.state = CircuitState.OPEN
                self.success_count = 0
            
            elif (self.state == CircuitState.CLOSED and 
                  self.failure_count >= self.config.failure_threshold):
                # Too many failures, open the circuit
                self.state = CircuitState.OPEN
    
    def get_state(self) -> dict:
        """Get current circuit breaker state information."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time,
            "last_success_time": self.last_success_time
        }
    
    def reset(self) -> None:
        """Reset circuit breaker to initial state."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self.last_success_time = 0.0


class AdaptiveController:
    """
    Advanced execution controller with adaptive rate limiting and circuit breaker.
    
    This controller combines:
    - Basic rate limiting through internal BasicRateLimiter
    - Circuit breaker pattern for fault tolerance  
    - Adaptive speed control based on error patterns
    - User interaction for critical failures
    """
    
    def __init__(self, config: Optional[AdaptiveControllerConfig] = None):
        self.config = config or AdaptiveControllerConfig()
        
        # Internal components
        self.rate_limiter = BasicRateLimiter(self.config.rate_limit_config)
        self.circuit_breaker = CircuitBreaker(self.config.circuit_breaker_config)
        
        # Adaptive rate control state
        self.current_backoff_factor = 1.0
        self.last_rate_limit_time = 0.0
        self._lock = asyncio.Lock()
    
    @property
    def effective_rpm(self) -> int:
        """Get current effective RPM considering adaptive backoff."""
        base_rpm = self.rate_limiter.effective_rpm
        if self.config.adaptive_speed_enabled:
            return max(1, int(base_rpm / self.current_backoff_factor))
        return base_rpm
    
    @property
    def current_rpm(self) -> int:
        """Get current actual requests per minute."""
        return self.rate_limiter.current_rpm
    
    async def acquire(self) -> bool:
        """Acquire permission to make a request with adaptive control."""
        async with self._lock:
            # Update adaptive state
            await self._update_adaptive_state()
            
            # Check circuit breaker first
            await self.circuit_breaker._check_state()
            if self.circuit_breaker.state == CircuitState.OPEN:
                return False
            
            # Check rate limiting with adaptive RPM
            if self.current_rpm >= self.effective_rpm:
                return False
            
            # Record request in rate limiter
            return await self.rate_limiter.acquire()
    
    async def wait_for_slot(self) -> None:
        """Wait until a request slot is available."""
        while not await self.acquire():
            await asyncio.sleep(1.0)
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function with full adaptive control.
        
        Args:
            func: Function to execute
            *args, **kwargs: Arguments to pass to function
            
        Returns:
            Result of function execution
            
        Raises:
            CircuitBreakerError: If circuit is open or other critical failures
        """
        # Wait for available slot
        await self.wait_for_slot()
        
        try:
            # Execute through circuit breaker
            result = await self.circuit_breaker.call(func, *args, **kwargs)
            
            # Record success for adaptive control
            await self._on_success()
            return result
            
        except Exception as exc:
            # Handle error for adaptive control
            await self._on_error(exc)
            raise
    
    async def _update_adaptive_state(self) -> None:
        """Update adaptive control state."""
        if not self.config.adaptive_speed_enabled:
            return
            
        now = time.time()
        
        # Gradually recover from backoff
        if (self.current_backoff_factor > 1.0 and 
            now - self.last_rate_limit_time > self.config.backoff_recovery_time):
            # Reduce backoff factor gradually
            recovery_rate = 0.1  # 10% recovery per update
            self.current_backoff_factor = max(
                1.0, 
                self.current_backoff_factor - recovery_rate
            )
    
    async def _on_success(self) -> None:
        """Handle successful execution."""
        # Circuit breaker handles its own success tracking
        pass
    
    async def _on_error(self, exc: Exception) -> None:
        """Handle error with adaptive response."""
        if not self.config.adaptive_speed_enabled:
            return
            
        # Classify the exception
        from .exceptions import classify_exception
        multask_error = classify_exception(exc)
        
        # Apply adaptive response based on error type
        if multask_error.severity == ErrorSeverity.RATE_LIMITED:
            await self._on_rate_limit_error(multask_error)
    
    async def _on_rate_limit_error(self, error) -> None:
        """Handle rate limit error with adaptive backoff."""
        async with self._lock:
            now = time.time()
            self.last_rate_limit_time = now
            
            # Increase backoff factor
            new_factor = self.current_backoff_factor * self.config.rate_limit_backoff_factor
            self.current_backoff_factor = min(new_factor, self.config.max_backoff_factor)
    
    def get_state(self) -> dict:
        """Get current controller state information."""
        return {
            "circuit_breaker": self.circuit_breaker.get_state(),
            "rate_limiter": {
                "current_rpm": self.current_rpm,
                "effective_rpm": self.effective_rpm,
                "base_rpm": self.rate_limiter.effective_rpm
            },
            "adaptive_control": {
                "backoff_factor": self.current_backoff_factor,
                "last_rate_limit_time": self.last_rate_limit_time,
                "adaptive_enabled": self.config.adaptive_speed_enabled
            }
        }
    
    def reset(self) -> None:
        """Reset controller to initial state."""
        self.rate_limiter.reset()
        self.circuit_breaker.reset()
        self.current_backoff_factor = 1.0
        self.last_rate_limit_time = 0.0


class UserInteractionHandler:
    """
    Handler for user interaction when circuit breaker is triggered.
    
    This provides options for users to:
    - Skip current task
    - Retry with manual confirmation  
    - Stop execution immediately
    """
    
    @staticmethod
    def prompt_user_action(error_type: str, current_progress: dict) -> str:
        """
        Prompt user for action when circuit breaker triggers.
        
        Args:
            error_type: Type of error that triggered the circuit breaker
            current_progress: Current execution progress information
            
        Returns:
            str: User's choice ('skip', 'retry', 'stop')
        """
        print(f"\n⚠️  Circuit breaker triggered due to: {error_type}")
        print(f"Progress: {current_progress.get('completed', 0)}/{current_progress.get('total', 0)} tasks completed")
        print("\nOptions:")
        print("1. Skip current task and continue")
        print("2. Retry current task") 
        print("3. Stop execution and return current results")
        
        while True:
            try:
                choice = input("\nEnter your choice (1/2/3): ").strip()
                if choice == '1':
                    return 'skip'
                elif choice == '2':
                    return 'retry'
                elif choice == '3':
                    return 'stop'
                else:
                    print("Invalid choice. Please enter 1, 2, or 3.")
            except (EOFError, KeyboardInterrupt):
                print("\nStopping execution...")
                return 'stop'
