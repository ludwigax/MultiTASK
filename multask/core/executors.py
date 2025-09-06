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
        shared_context: Optional[Dict[str, Any]] = None,
        enable_error_printing: bool = True
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
            enable_error_printing: Whether to print errors to console
        """
        self.worker = worker
        self.result_processor = result_processor
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.enable_user_interaction = enable_user_interaction
        self.shared_context = shared_context or {}
        self.enable_error_printing = enable_error_printing
        
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
    
    def _print_error(self, error_msg: str, task_index: Optional[int] = None) -> None:
        """Print error message to console if error printing is enabled."""
        if self.enable_error_printing:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            if task_index is not None:
                error_text = f"[{timestamp}] ERROR in task {task_index}: {error_msg}"
            else:
                error_text = f"[{timestamp}] ERROR: {error_msg}"
            
            # Use tqdm.write to avoid interfering with progress bars
            # Check if we're in a context where progress bars might be active
            try:
                tqdm.write(error_text)
            except:
                # Fallback to regular print if tqdm.write fails
                print(error_text)
    
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
            combined_params = {**self.shared_context, **task_params}
            
            # Execute task using unified controller interface
            success, result = await self.controller.execute_task(
                self.worker, 
                combined_params, 
                self.max_retries
            )
            
            if success:
                return (index, result)
            
            # Handle circuit breaker errors for smart controller
            if self._is_circuit_breaker_error(result):
                return self._handle_circuit_breaker_result(index, result)
            
            # Print error if it's an error result
            if isinstance(result, dict) and "error" in result:
                self._print_error(str(result["error"]), index)
                
            return (index, result)
                
        except Exception as exc:
            error_msg = f"Unexpected error in task execution: {exc}"
            self._print_error(error_msg, index)
            return (index, {"error": error_msg})
    
    def _is_circuit_breaker_error(self, result: Any) -> bool:
        """Check if result contains a circuit breaker error."""
        return (isinstance(result, dict) and 
                "circuit_breaker_error" in result and 
                self.controller_type == "smart")
    
    def _handle_circuit_breaker_result(self, index: int, result: Dict) -> Tuple[int, Any]:
        """Handle circuit breaker error result."""
        circuit_error = result["circuit_breaker_error"]
        self._print_error(f"Circuit breaker triggered: {circuit_error}", index)
        
        action = self._handle_circuit_breaker_error(
            CircuitBreakerError(circuit_error)
        )
        
        if action == 'skip':
            error_msg = f"Skipped due to circuit breaker: {circuit_error}"
            self._print_error(f"Action: {action} - {error_msg}", index)
            return (index, {"error": error_msg})
        elif action == 'stop':
            self.should_stop = True
            error_msg = f"Stopped due to circuit breaker: {circuit_error}"
            self._print_error(f"Action: {action} - {error_msg}", index)
            return (index, {"error": error_msg})
        elif action == 'retry':
            # Note: Retry logic could be implemented here if needed
            error_msg = f"Retry requested but not implemented in this context"
            self._print_error(f"Action: {action} - {error_msg}", index)
            return (index, {"error": error_msg})
        
        return (index, result)


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
                        error_msg = f"Worker thread failed: {e}"
                        self._print_error(error_msg)
            
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
        """Execute a single task synchronously with simplified error handling."""
        
        try:
            # Simple rate limiting check
            while self.controller.current_rpm >= self.controller.effective_rpm:
                time.sleep(1.0)
            
            # Combine shared context with task-specific parameters
            combined_params = {**self.shared_context, **task_params}
            
            # Execute with basic retry logic (ThreadExecutor doesn't support async controllers fully)
            return self._sync_retry_execution(index, combined_params)
                
        except Exception as exc:
            error_msg = f"Unexpected error: {exc}"
            self._print_error(error_msg, index)
            return (index, {"error": error_msg})
    
    def _sync_retry_execution(self, index: int, combined_params: Dict[str, Any]) -> Tuple[int, Any]:
        """Execute with synchronous retry logic."""
        for attempt in range(self.max_retries + 1):
            try:
                result = self.worker(**combined_params)
                return (index, result)
            except Exception as exc:
                multask_error = classify_exception(exc)
                
                if multask_error.severity == ErrorSeverity.FATAL:
                    error_msg = f"Fatal error: {multask_error}"
                    self._print_error(error_msg, index)
                    return (index, {"error": error_msg})
                
                if attempt < self.max_retries:
                    # Calculate backoff delay
                    delay = self._get_retry_delay(multask_error, attempt)
                    self._print_error(f"Retrying in {delay}s due to: {multask_error} (attempt {attempt + 1}/{self.max_retries})", index)
                    time.sleep(delay)
                else:
                    error_msg = f"Max retries ({self.max_retries}) exceeded: {exc}"
                    self._print_error(error_msg, index)
                    return (index, {"error": error_msg})
        
        error_msg = f"Max retries ({self.max_retries}) exceeded"
        self._print_error(error_msg, index)
        return (index, {"error": error_msg})
    
    def _get_retry_delay(self, multask_error, attempt: int) -> float:
        """Calculate appropriate retry delay based on error type."""
        if multask_error.severity == ErrorSeverity.RATE_LIMITED:
            if isinstance(multask_error, RateLimitError) and multask_error.retry_after:
                return multask_error.retry_after
        
        # Default exponential backoff
        return 2 ** attempt
