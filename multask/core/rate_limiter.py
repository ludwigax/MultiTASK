"""
Rate limiting module for controlling request frequency.

This module provides intelligent rate limiting with adaptive backoff
and rate adjustment based on API responses.
"""

import time
import asyncio
from typing import List, Optional
from dataclasses import dataclass


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting behavior."""
    base_rpm: int = 1000                    # Base requests per minute
    safety_factor: float = 0.9              # Use 90% of limit for safety
    rate_limit_backoff_factor: float = 2.0  # Multiply delay by this on rate limit
    max_backoff_factor: float = 10.0        # Maximum backoff multiplier
    backoff_recovery_time: int = 300        # Seconds to recover from backoff


class RateLimiter:
    """
    Intelligent rate limiter with adaptive backoff.
    
    Features:
    - Tracks requests per minute (RPM)
    - Adaptive backoff on rate limit errors
    - Automatic recovery from backoff state
    """
    
    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()
        self.request_timestamps: List[float] = []
        self.current_backoff_factor = 1.0
        self.last_rate_limit_time = 0.0
        self._lock = asyncio.Lock()
    
    @property
    def effective_rpm(self) -> int:
        """Get current effective RPM considering backoff."""
        base_rpm = int(self.config.base_rpm * self.config.safety_factor)
        return max(1, int(base_rpm / self.current_backoff_factor))
    
    @property
    def current_rpm(self) -> int:
        """Get current actual requests per minute."""
        now = time.time()
        # Keep only timestamps from last minute
        self.request_timestamps = [
            ts for ts in self.request_timestamps 
            if ts > now - 60
        ]
        return len(self.request_timestamps)
    
    async def acquire(self) -> bool:
        """
        Acquire permission to make a request.
        
        Returns:
            bool: True if request is allowed, False if should wait
        """
        async with self._lock:
            now = time.time()
            
            # Clean old timestamps
            self.request_timestamps = [
                ts for ts in self.request_timestamps 
                if ts > now - 60
            ]
            
            # Check if we can make a request
            if len(self.request_timestamps) >= self.effective_rpm:
                return False
            
            # Record this request
            self.request_timestamps.append(now)
            return True
    
    async def wait_for_slot(self) -> None:
        """Wait until a request slot is available."""
        while not await self.acquire():
            await asyncio.sleep(1.0)
    
    def on_rate_limit_error(self) -> None:
        """Called when a rate limit error occurs."""
        now = time.time()
        self.last_rate_limit_time = now
        
        # Increase backoff factor
        new_factor = self.current_backoff_factor * self.config.rate_limit_backoff_factor
        self.current_backoff_factor = min(new_factor, self.config.max_backoff_factor)
    
    def update(self) -> None:
        """Update rate limiter state (call periodically)."""
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
    
    def get_recommended_delay(self) -> float:
        """Get recommended delay between requests in seconds."""
        if self.effective_rpm <= 0:
            return 60.0  # 1 minute if RPM is 0
        return 60.0 / self.effective_rpm
    
    def reset(self) -> None:
        """Reset rate limiter to initial state."""
        self.request_timestamps.clear()
        self.current_backoff_factor = 1.0
        self.last_rate_limit_time = 0.0
