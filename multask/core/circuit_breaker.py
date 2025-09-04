"""
Circuit breaker pattern implementation for fault tolerance.

This module provides circuit breaker functionality to prevent cascading failures
and provide graceful degradation when services are experiencing issues.
"""

import time
import asyncio
from enum import Enum
from typing import Optional, Callable, Any
from dataclasses import dataclass

from .exceptions import CircuitBreakerError, ErrorSeverity


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
