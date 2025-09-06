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
from .controllers import (
    BaseController, BasicController, SmartController,
    BasicControllerConfig, SmartControllerConfig,
    UserInteractionHandler
)


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
        controller: Optional[BaseController] = None,
        controller_type: str = "smart",  # "basic" or "smart"
        basic_controller_config: Optional[BasicControllerConfig] = None,
        smart_controller_config: Optional[SmartControllerConfig] = None,
        enable_user_interaction: bool = True,
        shared_context: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize base executor.
        
        Args:
            worker: Function to execute for each task
            result_processor: Optional function to process final results
            max_workers: Maximum number of concurrent workers
            max_retries: Maximum retry attempts for recoverable errors
            controller: Custom controller instance (overrides controller_type)
            controller_type: Type of controller to use ("basic" or "smart")
            basic_controller_config: Configuration for BasicController
            smart_controller_config: Configuration for SmartController
            enable_user_interaction: Whether to prompt user on circuit breaker
            shared_context: Shared parameters passed to all worker calls
        """
        self.worker = worker
        self.result_processor = result_processor
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.enable_user_interaction = enable_user_interaction
        self.shared_context = shared_context or {}
        
        # Initialize controller
        if controller is not None:
            # Use provided controller
            self.controller = controller
        elif controller_type == "basic":
            # Use basic controller
            self.controller = BasicController(basic_controller_config)
        elif controller_type == "smart":
            # Use smart controller
            self.controller = SmartController(smart_controller_config)
        else:
            raise ValueError(f"Invalid controller_type: {controller_type}. Must be 'basic' or 'smart'")
        
        # Store controller type for logic decisions
        self.controller_type = controller_type if controller is None else getattr(controller, 'config', {}).get('type', 'unknown')
        
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
            total=self.controller.effective_rpm,
            desc="Rate (RPM)", 
            ncols=100,
            position=1
        )
    
    def _update_progress_bars(self) -> None:
        """Update progress bars."""
        if self.progress_bar:
            self.progress_bar.refresh()
        if self.rate_bar:
            self.rate_bar.n = self.controller.current_rpm
            self.rate_bar.total = self.controller.effective_rpm
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
        Only called in adaptive mode.
        
        Returns:
            str: User action ('skip', 'retry', 'stop')
        """
        if not self.enable_user_interaction or self.controller_type != "smart":
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
            
            # Start worker coroutines without forcing session creation
            workers = [
                asyncio.create_task(self._worker_coroutine())
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
    
    async def _worker_coroutine(self) -> List[Tuple[int, Any]]:
        """Individual worker coroutine."""
        worker_results = []
        
        while not self.task_queue.empty() and not self.should_stop:
            try:
                # Get next task
                index, task_params = await asyncio.wait_for(
                    self.task_queue.get(), 
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            
            async with self.semaphore:
                result = await self._execute_single_task(index, task_params)
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
        index: int, 
        task_params: Dict[str, Any]
    ) -> Optional[Tuple[int, Any]]:
        """Execute a single task using unified controller interface."""
        
        try:
            # Apply random delay if enabled
            if hasattr(self, 'random_delay') and self.random_delay:
                delay = random.uniform(0.1, 0.5)
                await asyncio.sleep(delay)
            
            # Combine shared context with task-specific parameters
            # Task parameters take precedence over shared context
            combined_params = {**self.shared_context, **task_params}
            
            # Execute task using unified controller interface
            success, result = await self.controller.execute_task(
                self.worker, 
                combined_params, 
                self.max_retries
            )
            
            if success:
                return (index, result)
            else:
                # Check if it's a circuit breaker error that needs user interaction
                if (isinstance(result, dict) and 
                    (
                        ("circuit_breaker_error" in result) or 
                        ("fatal_error" in result)
                    ) and 
                    self.controller_type == "smart"):
                    
                    action = self._handle_circuit_breaker_error(
                        CircuitBreakerError(result["circuit_breaker_error"])
                    )
                    
                    if action == 'skip':
                        return (index, {"error": f"Skipped due to circuit breaker: {result['circuit_breaker_error']}"})
                    elif action == 'stop':
                        self.should_stop = True
                        return (index, {"error": f"Stopped due to circuit breaker: {result['circuit_breaker_error']}"})
                    elif action == 'retry':
                        # Retry once more
                        success, retry_result = await self.controller.execute_task(
                            self.worker, 
                            combined_params, 
                            1  # Single retry
                        )
                        return (index, retry_result if success else {"error": f"Retry failed: {retry_result}"})
                
                return (index, result)
                
        except Exception as exc:
            # This should rarely happen with the unified controller interface
            return (index, {"error": f"Unexpected error in task execution: {exc}"})


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
        """Execute a single task synchronously with basic error handling."""
        
        try:
            # Simple rate limiting check (synchronous)
            while self.controller.current_rpm >= self.controller.effective_rpm:
                time.sleep(1.0)
            
            # Combine shared context with task-specific parameters
            combined_params = {**self.shared_context, **task_params}
            
            # Note: ThreadExecutor currently only supports BasicController
            # since SmartController requires async operations
            if self.controller_type == "smart":
                # For thread executor, we fall back to basic retry logic even with smart controller
                for attempt in range(self.max_retries + 1):
                    try:
                        result = self.worker(**combined_params)
                        return (index, result)
                    except Exception as exc:
                        multask_error = classify_exception(exc)
                        if multask_error.severity == ErrorSeverity.FATAL:
                            return (index, {"error": f"Fatal error: {multask_error}"})
                        elif attempt < self.max_retries:
                            time.sleep(2 ** attempt)  # Exponential backoff
                        else:
                            return (index, {"error": f"Max retries ({self.max_retries}) exceeded: {exc}"})
            else:
                # Use basic controller - but since it's async, we need to handle it specially
                # For now, implement basic retry logic directly
                for attempt in range(self.max_retries + 1):
                    try:
                        result = self.worker(**combined_params)
                        return (index, result)
                    except Exception as exc:
                        multask_error = classify_exception(exc)
                        if multask_error.severity == ErrorSeverity.FATAL:
                            return (index, {"error": f"Fatal error: {multask_error}"})
                        elif multask_error.severity == ErrorSeverity.RATE_LIMITED:
                            if isinstance(multask_error, RateLimitError) and multask_error.retry_after:
                                time.sleep(multask_error.retry_after)
                            else:
                                time.sleep(2 ** attempt)
                        elif multask_error.severity == ErrorSeverity.RECOVERABLE:
                            time.sleep(2 ** attempt)
                        else:
                            return (index, {"error": f"Critical error: {multask_error}"})
                
                return (index, {"error": f"Max retries ({self.max_retries}) exceeded"})
                
        except Exception as exc:
            return (index, {"error": f"Unexpected error: {exc}"})
