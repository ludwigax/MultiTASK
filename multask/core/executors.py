"""
Base executor classes for concurrent task execution.

This module provides the foundation for both asynchronous and threaded
task execution with integrated error handling, rate limiting, and circuit breaking.
"""

import asyncio
import itertools
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from queue import Queue

import aiohttp
from tqdm import tqdm

from .exceptions import (
    MultaskError, ErrorSeverity, classify_exception,
    RateLimitError, NetworkInstabilityError, CircuitBreakerError
)
from .rate_limiter import RateLimiter, RateLimitConfig
from .circuit_breaker import CircuitBreaker, CircuitBreakerConfig, UserInteractionHandler


class BaseExecutor(ABC):
    """
    Abstract base class for task executors.
    
    Provides common functionality for error handling, progress tracking,
    and result processing.
    """
    
    def __init__(
        self,
        worker: Callable,
        result_processor: Optional[Callable] = None,
        max_workers: int = 5,
        max_retries: int = 3,
        rate_limit_config: Optional[RateLimitConfig] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
        enable_user_interaction: bool = True
    ):
        """
        Initialize base executor.
        
        Args:
            worker: Function to execute for each task
            result_processor: Optional function to process final results
            max_workers: Maximum number of concurrent workers
            max_retries: Maximum retry attempts for recoverable errors
            rate_limit_config: Rate limiting configuration
            circuit_breaker_config: Circuit breaker configuration  
            enable_user_interaction: Whether to prompt user on circuit breaker
        """
        self.worker = worker
        self.result_processor = result_processor
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.enable_user_interaction = enable_user_interaction
        
        # Initialize components
        self.rate_limiter = RateLimiter(rate_limit_config)
        self.circuit_breaker = CircuitBreaker(circuit_breaker_config)
        
        # Progress tracking
        self.progress_bar: Optional[tqdm] = None
        self.rate_bar: Optional[tqdm] = None
        
        # Execution state
        self.should_stop = False
        self.completed_tasks = 0
        self.total_tasks = 0
    
    def _init_progress_bars(self, total_tasks: int) -> None:
        """Initialize progress bars."""
        self.total_tasks = total_tasks
        self.completed_tasks = 0
        self.progress_bar = tqdm(
            total=total_tasks, 
            desc="Progress", 
            ncols=100,
            position=0
        )
        self.rate_bar = tqdm(
            total=self.rate_limiter.effective_rpm,
            desc="Rate (RPM)", 
            ncols=100,
            position=1
        )
    
    def _update_progress_bars(self) -> None:
        """Update progress bars."""
        if self.progress_bar:
            self.progress_bar.refresh()
        if self.rate_bar:
            self.rate_bar.n = self.rate_limiter.current_rpm
            self.rate_bar.total = self.rate_limiter.effective_rpm
            self.rate_bar.refresh()
    
    def _close_progress_bars(self) -> None:
        """Close progress bars."""
        if self.progress_bar:
            self.progress_bar.close()
            self.progress_bar = None
        if self.rate_bar:
            self.rate_bar.close()
            self.rate_bar = None
    
    def _handle_circuit_breaker_error(self, error: CircuitBreakerError) -> str:
        """
        Handle circuit breaker error with user interaction.
        
        Returns:
            str: User action ('skip', 'retry', 'stop')
        """
        if not self.enable_user_interaction:
            return 'stop'
        
        progress_info = {
            'completed': self.completed_tasks,
            'total': self.total_tasks
        }
        
        return UserInteractionHandler.prompt_user_action(
            str(error), 
            progress_info
        )
    
    @abstractmethod
    async def execute(self, tasks: List[Dict[str, Any]]) -> List[Tuple[int, Any]]:
        """
        Execute tasks concurrently.
        
        Args:
            tasks: List of task dictionaries
            
        Returns:
            List of (index, result) tuples
        """
        pass


class AsyncExecutor(BaseExecutor):
    """
    Asynchronous task executor with advanced error handling.
    
    Features:
    - Async/await based concurrency
    - Intelligent rate limiting with backoff
    - Circuit breaker pattern for fault tolerance
    - User interaction on critical failures
    """
    
    def __init__(self, *args, random_delay: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.random_delay = random_delay
        self.task_queue: Optional[asyncio.Queue] = None
        self.semaphore: Optional[asyncio.Semaphore] = None
    
    async def execute(self, tasks: List[Dict[str, Any]]) -> List[Tuple[int, Any]]:
        """Execute tasks asynchronously."""
        self._init_progress_bars(len(tasks))
        
        try:
            # Initialize task queue and semaphore
            self.task_queue = asyncio.Queue()
            self.semaphore = asyncio.Semaphore(self.max_workers)
            
            # Populate task queue
            for i, task in enumerate(tasks):
                await self.task_queue.put((i, task))
            
            # Start worker coroutines
            async with aiohttp.ClientSession() as session:
                workers = [
                    asyncio.create_task(self._worker_coroutine(session))
                    for _ in range(self.max_workers)
                ]
                
                try:
                    # Wait for all tasks to complete
                    await self.task_queue.join()
                    
                    # Cancel workers
                    for worker in workers:
                        worker.cancel()
                    
                    # Gather results
                    results = await asyncio.gather(*workers, return_exceptions=True)
                    
                except asyncio.CancelledError:
                    self._close_progress_bars()
                    raise
                
            # Flatten and sort results
            flattened_results = []
            for result in results:
                if isinstance(result, list):
                    flattened_results.extend(result)
            
            flattened_results.sort(key=lambda x: x[0])
            
            # Apply result processor if provided
            if self.result_processor:
                flattened_results = self.result_processor(flattened_results)
            
            return flattened_results
            
        finally:
            self._close_progress_bars()
    
    async def _worker_coroutine(self, session: aiohttp.ClientSession) -> List[Tuple[int, Any]]:
        """Individual worker coroutine."""
        worker_results = []
        
        while not self.task_queue.empty() and not self.should_stop:
            # Wait for rate limiting
            await self.rate_limiter.wait_for_slot()
            
            try:
                # Get next task
                index, task_params = await asyncio.wait_for(
                    self.task_queue.get(), 
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            
            async with self.semaphore:
                result = await self._execute_single_task(session, index, task_params)
                if result is not None:
                    worker_results.append(result)
                
                # Update progress
                self.completed_tasks += 1
                if self.progress_bar:
                    self.progress_bar.update(1)
                self._update_progress_bars()
                
                # Mark task as done
                self.task_queue.task_done()
        
        return worker_results
    
    async def _execute_single_task(
        self, 
        session: aiohttp.ClientSession, 
        index: int, 
        task_params: Dict[str, Any]
    ) -> Optional[Tuple[int, Any]]:
        """Execute a single task with error handling and retries."""
        
        for attempt in range(self.max_retries + 1):
            try:
                # Apply random delay if enabled
                if self.random_delay:
                    delay = random.uniform(0.1, 0.5)
                    await asyncio.sleep(delay)
                
                # Execute task through circuit breaker
                result = await self.circuit_breaker.call(
                    self.worker, session, **task_params
                )
                
                # Apply result processor if provided and it's not the final processor
                # (The final processor is applied to all results at once)
                return (index, result)
                
            except CircuitBreakerError as e:
                # Handle circuit breaker
                action = self._handle_circuit_breaker_error(e)
                
                if action == 'skip':
                    return (index, {"error": f"Skipped due to circuit breaker: {e}"})
                elif action == 'stop':
                    self.should_stop = True
                    return (index, {"error": f"Stopped due to circuit breaker: {e}"})
                elif action == 'retry':
                    continue  # Retry this task
                
            except Exception as exc:
                # Classify the exception
                multask_error = classify_exception(exc)
                
                # Handle based on severity
                if multask_error.severity == ErrorSeverity.FATAL:
                    return (index, {"error": f"Fatal error: {multask_error}"})
                
                elif multask_error.severity == ErrorSeverity.RATE_LIMITED:
                    # Update rate limiter and wait
                    self.rate_limiter.on_rate_limit_error()
                    if isinstance(multask_error, RateLimitError) and multask_error.retry_after:
                        await asyncio.sleep(multask_error.retry_after)
                    else:
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                
                elif multask_error.severity == ErrorSeverity.RECOVERABLE:
                    # Wait and retry
                    await asyncio.sleep(2 ** attempt)
                
                else:  # CIRCUIT_BREAKER severity
                    # Let circuit breaker handle it
                    await self.circuit_breaker._on_failure()
                    return (index, {"error": f"Circuit breaker error: {multask_error}"})
        
        # All retries exhausted
        return (index, {"error": f"Max retries ({self.max_retries}) exceeded"})


class ThreadExecutor(BaseExecutor):
    """
    Thread-based task executor with advanced error handling.
    
    Features:
    - Thread pool based concurrency
    - Synchronous rate limiting
    - Circuit breaker pattern
    - Compatible with synchronous worker functions
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.task_queue: Optional[Queue] = None
        self.results_lock = Lock()
    
    async def execute(self, tasks: List[Dict[str, Any]]) -> List[Tuple[int, Any]]:
        """Execute tasks using thread pool."""
        self._init_progress_bars(len(tasks))
        
        try:
            # Initialize task queue
            self.task_queue = Queue()
            for i, task in enumerate(tasks):
                self.task_queue.put((i, task))
            
            # Execute using thread pool
            results = []
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = [
                    executor.submit(self._worker_thread)
                    for _ in range(self.max_workers)
                ]
                
                for future in as_completed(futures):
                    try:
                        worker_results = future.result()
                        results.extend(worker_results)
                    except Exception as e:
                        tqdm.write(f"Worker thread failed: {e}")
            
            # Sort results by index
            results.sort(key=lambda x: x[0])
            
            # Apply result processor if provided
            if self.result_processor:
                results = self.result_processor(results)
            
            return results
            
        finally:
            self._close_progress_bars()
    
    def _worker_thread(self) -> List[Tuple[int, Any]]:
        """Individual worker thread function."""
        worker_results = []
        
        while not self.task_queue.empty() and not self.should_stop:
            try:
                index, task_params = self.task_queue.get_nowait()
            except:
                break
            
            result = self._execute_single_task_sync(index, task_params)
            if result is not None:
                worker_results.append(result)
            
            # Update progress
            with self.results_lock:
                self.completed_tasks += 1
                if self.progress_bar:
                    self.progress_bar.update(1)
                self._update_progress_bars()
        
        return worker_results
    
    def _execute_single_task_sync(
        self, 
        index: int, 
        task_params: Dict[str, Any]
    ) -> Optional[Tuple[int, Any]]:
        """Execute a single task synchronously with error handling."""
        
        for attempt in range(self.max_retries + 1):
            try:
                # Simple rate limiting check (synchronous)
                while self.rate_limiter.current_rpm >= self.rate_limiter.effective_rpm:
                    time.sleep(1.0)
                
                # Execute task
                result = self.worker(**task_params)
                
                # Record successful request
                self.rate_limiter.request_timestamps.append(time.time())
                
                return (index, result)
                
            except Exception as exc:
                # Classify the exception
                multask_error = classify_exception(exc)
                
                # Handle based on severity
                if multask_error.severity == ErrorSeverity.FATAL:
                    return (index, {"error": f"Fatal error: {multask_error}"})
                
                elif multask_error.severity == ErrorSeverity.RATE_LIMITED:
                    # Update rate limiter and wait
                    self.rate_limiter.on_rate_limit_error()
                    if isinstance(multask_error, RateLimitError) and multask_error.retry_after:
                        time.sleep(multask_error.retry_after)
                    else:
                        time.sleep(2 ** attempt)  # Exponential backoff
                
                elif multask_error.severity == ErrorSeverity.RECOVERABLE:
                    # Wait and retry
                    time.sleep(2 ** attempt)
                
                else:  # CIRCUIT_BREAKER severity
                    return (index, {"error": f"Circuit breaker error: {multask_error}"})
        
        # All retries exhausted
        return (index, {"error": f"Max retries ({self.max_retries}) exceeded"})
