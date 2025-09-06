"""
Unified execution controllers for task management.

This module provides a clean, unified interface for different execution control strategies:
- BasicController: Simple rate limiting with basic retry logic
- SmartController: Adaptive rate limiting with circuit breaker and user interaction
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
class BasicControllerConfig:
    """Configuration for basic controller."""
    rate_limit_config: Optional[RateLimitConfig] = None
    
    def __post_init__(self):
        if self.rate_limit_config is None:
            self.rate_limit_config = RateLimitConfig()


@dataclass
class SmartControllerConfig:
    """Configuration for smart controller."""
    # Rate limiting config
    rate_limit_config: Optional[RateLimitConfig] = None
    
    # Circuit breaker config
    failure_threshold: int = 5          # Failures before opening
    success_threshold: int = 3          # Successes to close from half-open
    circuit_timeout: float = 60.0       # Seconds before trying half-open
    reset_timeout: float = 300.0        # Seconds to fully reset failure count
    
    # Adaptive behavior config
    rate_limit_backoff_factor: float = 2.0      # Multiply delay by this on rate limit
    max_backoff_factor: float = 10.0            # Maximum backoff multiplier
    backoff_recovery_time: int = 300            # Seconds to recover from backoff
    adaptive_speed_enabled: bool = True         # Enable adaptive speed control
    
    def __post_init__(self):
        if self.rate_limit_config is None:
            self.rate_limit_config = RateLimitConfig()


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


class BasicController(BaseController):
    """
    Basic execution controller with simple rate limiting and retries.
    
    Features:
    - Simple rate limiting
    - Basic retry logic with exponential backoff
    - No circuit breaker or adaptive behavior
    """
    
    def __init__(self, config: Optional[BasicControllerConfig] = None):
        self.config = config or BasicControllerConfig()
        self.rate_limiter = BasicRateLimiter(self.config.rate_limit_config)
    
    async def wait_for_slot(self) -> None:
        """Wait until a request slot is available."""
        await self.rate_limiter.wait_for_slot()
    
    async def execute_task(
        self, 
        worker: Callable, 
        task_params: Dict[str, Any],
        max_retries: int = 3
    ) -> Tuple[bool, Any]:
        """Execute task with basic retry logic."""
        
        for attempt in range(max_retries + 1):
            try:
                # Execute the worker
                if asyncio.iscoroutinefunction(worker):
                    result = await worker(**task_params)
                else:
                    result = worker(**task_params)
                
                return (True, result)
                
            except Exception as exc:
                # Classify the exception
                multask_error = classify_exception(exc)
                
                # Handle based on severity
                if multask_error.severity == ErrorSeverity.FATAL:
                    return (False, {"error": f"Fatal error: {multask_error}"})
                
                elif multask_error.severity == ErrorSeverity.RATE_LIMITED:
                    # Simple exponential backoff
                    if isinstance(multask_error, RateLimitError) and multask_error.retry_after:
                        await asyncio.sleep(multask_error.retry_after)
                    else:
                        await asyncio.sleep(2 ** attempt)
                
                elif multask_error.severity == ErrorSeverity.RECOVERABLE:
                    # Wait and retry
                    await asyncio.sleep(2 ** attempt)
                
                else:  # CIRCUIT_BREAKER severity - treat as fatal in basic mode
                    return (False, {"error": f"Critical error (treated as fatal): {multask_error}"})
        
        # All retries exhausted
        return (False, {"error": f"Max retries ({max_retries}) exceeded"})
    
    def get_state(self) -> Dict[str, Any]:
        """Get current controller state information."""
        return {
            "type": "basic",
            "rate_limiter": {
                "current_rpm": self.current_rpm,
                "effective_rpm": self.effective_rpm
            }
        }
    
    def reset(self) -> None:
        """Reset controller to initial state."""
        self.rate_limiter.reset()
    
    @property
    def effective_rpm(self) -> int:
        """Get current effective RPM."""
        return self.rate_limiter.effective_rpm
    
    @property
    def current_rpm(self) -> int:
        """Get current actual requests per minute."""
        return self.rate_limiter.current_rpm


class SmartController(BaseController):
    """
    Smart execution controller with adaptive rate limiting and circuit breaker.
    
    Features:
    - Adaptive rate limiting with intelligent backoff
    - Circuit breaker pattern for fault tolerance
    - User interaction on critical failures
    - Intelligent error classification and handling
    """
    
    def __init__(self, config: Optional[SmartControllerConfig] = None):
        self.config = config or SmartControllerConfig()
        
        # Rate limiting components
        self.rate_limiter = BasicRateLimiter(self.config.rate_limit_config)
        
        # Circuit breaker state
        self.circuit_state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self.last_success_time = 0.0
        
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
    
    async def wait_for_slot(self) -> None:
        """Wait until a request slot is available with circuit breaker check."""
        while True:
            async with self._lock:
                # Update circuit breaker state
                await self._update_circuit_state()
                
                # Check if circuit is open
                if self.circuit_state == CircuitState.OPEN:
                    raise CircuitBreakerError(
                        f"Circuit breaker is OPEN after {self.failure_count} failures",
                        failure_count=self.failure_count
                    )
                
                # Update adaptive state
                await self._update_adaptive_state()
                
                # Check rate limiting with adaptive RPM
                if self.current_rpm >= self.effective_rpm:
                    await asyncio.sleep(1.0)
                    continue
                
                # Record request in rate limiter
                if await self.rate_limiter.acquire():
                    break
            
            await asyncio.sleep(1.0)
    
    async def execute_task(
        self, 
        worker: Callable, 
        task_params: Dict[str, Any],
        max_retries: int = 3
    ) -> Tuple[bool, Any]:
        """Execute task with smart adaptive control."""
        
        for attempt in range(max_retries + 1):
            try:
                # Wait for available slot (includes circuit breaker check)
                await self.wait_for_slot()
                
                # Execute the worker
                if asyncio.iscoroutinefunction(worker):
                    result = await worker(**task_params)
                else:
                    result = worker(**task_params)
                
                # Record success
                await self._on_success()
                return (True, result)
                
            except CircuitBreakerError as e:
                # Circuit breaker is open, let caller handle user interaction
                return (False, {"circuit_breaker_error": str(e)})
                
            except Exception as exc:
                # Classify and handle the error
                multask_error = classify_exception(exc)
                
                # Record failure for circuit breaker
                if multask_error.severity in [ErrorSeverity.CIRCUIT_BREAKER, ErrorSeverity.FATAL]:
                    await self._on_failure()
                
                # Handle based on severity
                if multask_error.severity == ErrorSeverity.FATAL:
                    return (False, {"error": f"Fatal error: {multask_error}"})
                
                elif multask_error.severity == ErrorSeverity.RATE_LIMITED:
                    await self._on_rate_limit_error(multask_error)
                    if isinstance(multask_error, RateLimitError) and multask_error.retry_after:
                        await asyncio.sleep(multask_error.retry_after)
                    else:
                        await asyncio.sleep(2 ** attempt)
                
                elif multask_error.severity == ErrorSeverity.RECOVERABLE:
                    await asyncio.sleep(2 ** attempt)
                
                else:  # CIRCUIT_BREAKER severity
                    await self._on_failure()
                    return (False, {"error": f"Circuit breaker error: {multask_error}"})
        
        # All retries exhausted
        return (False, {"error": f"Max retries ({max_retries}) exceeded"})
    
    async def _update_circuit_state(self) -> None:
        """Update circuit breaker state."""
        now = time.time()
        
        if self.circuit_state == CircuitState.OPEN:
            # Check if we should transition to half-open
            if now - self.last_failure_time >= self.config.circuit_timeout:
                self.circuit_state = CircuitState.HALF_OPEN
                self.success_count = 0
        
        elif self.circuit_state == CircuitState.CLOSED:
            # Check if we should reset failure count
            if (self.failure_count > 0 and 
                now - self.last_failure_time >= self.config.reset_timeout):
                self.failure_count = 0
    
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
        async with self._lock:
            self.last_success_time = time.time()
            
            if self.circuit_state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.config.success_threshold:
                    # Service has recovered, close the circuit
                    self.circuit_state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
    
    async def _on_failure(self) -> None:
        """Handle failed execution."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.circuit_state == CircuitState.HALF_OPEN:
                # Failed during testing, go back to open
                self.circuit_state = CircuitState.OPEN
                self.success_count = 0
            
            elif (self.circuit_state == CircuitState.CLOSED and 
                  self.failure_count >= self.config.failure_threshold):
                # Too many failures, open the circuit
                self.circuit_state = CircuitState.OPEN
    
    async def _on_rate_limit_error(self, error) -> None:
        """Handle rate limit error with adaptive backoff."""
        if not self.config.adaptive_speed_enabled:
            return
            
        async with self._lock:
            now = time.time()
            self.last_rate_limit_time = now
            
            # Increase backoff factor
            new_factor = self.current_backoff_factor * self.config.rate_limit_backoff_factor
            self.current_backoff_factor = min(new_factor, self.config.max_backoff_factor)
    
    def get_state(self) -> Dict[str, Any]:
        """Get current controller state information."""
        return {
            "type": "smart",
            "circuit_breaker": {
                "state": self.circuit_state.value,
                "failure_count": self.failure_count,
                "success_count": self.success_count,
                "last_failure_time": self.last_failure_time,
                "last_success_time": self.last_success_time
            },
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
        self.circuit_state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self.last_success_time = 0.0
        self.current_backoff_factor = 1.0
        self.last_rate_limit_time = 0.0


class UserInteractionHandler:
    """
    Handler for user interaction when smart controller encounters critical failures.
    
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
