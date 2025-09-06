"""
Basic rate limiting module for controlling request frequency.

This module provides simple rate limiting functionality without adaptive features.
For advanced adaptive control, use the AdaptiveController instead.
"""

import time
import asyncio
from typing import List, Optional
from dataclasses import dataclass


@dataclass
class RateLimitConfig:
    """Configuration for basic rate limiting behavior."""
    max_rpm: int = 1000                     # Maximum requests per minute
    safety_factor: float = 0.9              # Use 90% of limit for safety


class BasicRateLimiter:
    """
    Basic rate limiter for simple RPM control.
    
    Features:
    - Tracks requests per minute (RPM)
    - Simple rate limiting without adaptive behavior
    - Fixed rate control
    """
    
    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()
        self.request_timestamps: List[float] = []
        self._lock = asyncio.Lock()
    
    @property
    def effective_rpm(self) -> int:
        """Get effective RPM with safety factor applied."""
        return max(1, int(self.config.max_rpm * self.config.safety_factor))
    
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
        """Called when a rate limit error occurs. Basic implementation does nothing."""
        pass
    
    def update(self) -> None:
        """Update rate limiter state. Basic implementation does nothing."""
        pass
    
    def get_recommended_delay(self) -> float:
        """Get recommended delay between requests in seconds."""
        if self.effective_rpm <= 0:
            return 60.0  # 1 minute if RPM is 0
        return 60.0 / self.effective_rpm
    
    def reset(self) -> None:
        """Reset rate limiter to initial state."""
        self.request_timestamps.clear()


# Backward compatibility alias
RateLimiter = BasicRateLimiter
