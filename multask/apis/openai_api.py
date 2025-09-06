"""
OpenAI API interface for language model interactions.

This module provides a high-level interface for querying OpenAI's API
using the multask execution framework with proper error handling and rate limiting.
"""

import os
import json
import asyncio
from typing import List, Dict, Any, Optional, Union

# Check for required dependencies
try:
    import aiohttp
except ImportError:
    raise ImportError(
        "aiohttp is required for OpenAI API. Install with: pip install aiohttp"
    )

from ..core import AsyncExecutor
from ..core.controllers import BasicController, SmartController, BasicControllerConfig, SmartControllerConfig
from ..core.rate_limiter import RateLimitConfig
from ..core.exceptions import classify_exception, FatalError, RateLimitError


async def openai_chat_worker(
    session: aiohttp.ClientSession,
    messages: List[Dict[str, str]],
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    save_path: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Worker function for OpenAI Chat Completions API.
    
    Args:
        session: HTTP session for making requests
        messages: List of message dictionaries
        model: Model to use for completion
        api_key: OpenAI API key (or from environment)
        base_url: Custom base URL for API
        save_path: Optional path to save response content
        **kwargs: Additional chat parameters
        
    Returns:
        Dict containing the processed response
        
    Raises:
        FatalError: If API key is missing or messages are invalid
        RateLimitError: If rate limit is exceeded
    """
    # Get API key
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise FatalError("OpenAI API key is required (set OPENAI_API_KEY or pass api_key)")
    
    # Get base URL
    if not base_url:
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    
    # Validate messages
    if not messages or not isinstance(messages, list):
        raise FatalError("Messages must be a non-empty list")
    
    # Prepare request
    url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    # Prepare data
    data = {
        "model": model,
        "messages": messages,
        **kwargs
    }
    
    # Handle streaming
    is_streaming = data.get("stream", False)
    
    try:
        if is_streaming:
            content = await _collect_streaming_response(session, url, data, headers)
            return {
                "content": content,
                "model": model,
                "token_usage": {},  # Not available in streaming
                "streaming": True
            }
        else:
            async with session.post(url, json=data, headers=headers) as response:
                if response.status == 429:
                    # Extract retry-after header if available
                    retry_after = None
                    if 'retry-after' in response.headers:
                        try:
                            retry_after = int(response.headers['retry-after'])
                        except (ValueError, TypeError):
                            pass
                    raise RateLimitError(
                        "OpenAI API rate limit exceeded",
                        retry_after=retry_after
                    )
                
                response.raise_for_status()
                response_data = await response.json()
                
                # Process response
                content, token_usage = _extract_response_data(response_data)
                
                # Save content if requested
                _save_content_if_needed(content, save_path)
                
                return {
                    "content": content,
                    "model": response_data.get("model", model),
                    "token_usage": token_usage,
                    "streaming": False
                }
                
    except aiohttp.ClientResponseError as e:
        if e.status == 429:
            raise RateLimitError("OpenAI API rate limit exceeded")
        elif 400 <= e.status < 500:
            raise FatalError(f"OpenAI API client error {e.status}: {e.message}")
        else:
            raise  # Let classify_exception handle server errors


async def _collect_streaming_response(
    session: aiohttp.ClientSession, 
    url: str, 
    data: dict, 
    headers: dict
) -> str:
    """Collect streaming response from OpenAI API."""
    chunks = []
    
    timeout = aiohttp.ClientTimeout(total=480, connect=30, sock_read=60)
    
    try:
        async with session.post(url, json=data, headers=headers, timeout=timeout) as response:
            if response.status == 429:
                raise RateLimitError("OpenAI API rate limit exceeded")
            
            response.raise_for_status()
            
            async for line in response.content:
                line_str = line.decode('utf-8').strip()
                
                if not line_str or line_str.startswith(':'):
                    continue
                
                if line_str.startswith('data: '):
                    data_str = line_str[6:]
                    
                    if data_str.strip() == '[DONE]':
                        break
                    
                    try:
                        chunk_data = json.loads(data_str)
                        
                        if 'choices' in chunk_data and chunk_data['choices']:
                            choice = chunk_data['choices'][0]
                            delta = choice.get('delta', {})
                            
                            if choice.get('finish_reason'):
                                break
                            
                            if 'content' in delta and delta['content']:
                                chunks.append(delta['content'])
                                
                    except json.JSONDecodeError:
                        continue
                        
    except asyncio.TimeoutError:
        raise RateLimitError("OpenAI API request timeout")
    
    return ''.join(chunks)


class OpenAIExecutor(AsyncExecutor):
    """
    Specialized executor for OpenAI API queries.
    
    Features:
    - Automatic rate limiting for OpenAI API
    - Support for streaming and non-streaming responses
    - Built-in cost calculation
    - Environment variable management
    """
    
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, **kwargs):
        """
        Initialize OpenAI executor.
        
        Args:
            api_key: OpenAI API key
            base_url: Custom base URL for API
            **kwargs: Additional executor parameters
        """
        # Set up controller configuration
        controller_type = kwargs.pop('controller_type', 'smart')  # Default to smart controller
        
        if controller_type == 'smart':
            if 'smart_controller_config' not in kwargs:
                kwargs['smart_controller_config'] = SmartControllerConfig(
                    rate_limit_config=RateLimitConfig(
                        max_rpm=500,  # Conservative default
                        safety_factor=0.9
                    ),
                    failure_threshold=5,
                    circuit_timeout=60.0,
                    rate_limit_backoff_factor=2.0,
                    max_backoff_factor=8.0
                )
        else:
            if 'basic_controller_config' not in kwargs:
                kwargs['basic_controller_config'] = BasicControllerConfig(
                    rate_limit_config=RateLimitConfig(
                        max_rpm=500,
                        safety_factor=0.9
                    )
                )
        
        kwargs['controller_type'] = controller_type
        
        super().__init__(
            worker=openai_chat_worker,
            **kwargs
        )
        
        # Store API configuration in shared context
        if 'shared_context' not in kwargs:
            self.shared_context = {}
        
        if api_key:
            self.shared_context['api_key'] = api_key
        if base_url:
            self.shared_context['base_url'] = base_url


def openai_price_calculator(
    token_usages: Union[Dict[str, int], List[Dict[str, int]]], 
    model_name: str
) -> float:
    """
    Calculate the cost of OpenAI API usage based on token consumption.
    
    Args:
        token_usages: Single usage dict or list of usage dicts
        model_name: Name of the model used
        
    Returns:
        Total cost in USD
        
    Raises:
        ValueError: If model is not supported
    """
    # Pricing per 1M tokens (as of 2024)
    pricing = {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-4": {"input": 30.00, "output": 60.00},
        "gpt-4-32k": {"input": 60.00, "output": 120.00},
        "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
        "gpt-3.5-turbo-16k": {"input": 3.00, "output": 4.00},
        "o1": {"input": 15.00, "output": 60.00},
        "o1-mini": {"input": 3.00, "output": 12.00},
        "o3-mini": {"input": 1.10, "output": 4.40},
        # Third-party models
        "qwen-max": {"input": 0.34, "output": 1.37},
        "qwen-max-latest": {"input": 0.34, "output": 1.37},
        "claude-3-sonnet": {"input": 3.00, "output": 15.00},
        "claude-3-haiku": {"input": 0.25, "output": 1.25}
    }
    
    if model_name not in pricing:
        raise ValueError(f"Model '{model_name}' is not supported for cost calculation")
    
    if isinstance(token_usages, dict):
        token_usages = [token_usages]
    
    total_cost = 0.0
    model_pricing = pricing[model_name]
    
    for usage in token_usages:
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        
        input_cost = (prompt_tokens / 1_000_000) * model_pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * model_pricing["output"]
        
        total_cost += input_cost + output_cost
    
    return round(total_cost, 6)


def openai_batch_query(
    messages_list: List[List[Dict[str, str]]],
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    save_paths: Optional[List[str]] = None,
    max_workers: int = 5,
    rate_limit_rpm: int = 500,
    random_delay: bool = False,
    chat_params: Optional[Dict[str, Any]] = None,
    controller_type: str = "smart",
    **executor_kwargs
) -> List[Dict[str, Any]]:
    """
    High-level function for batch OpenAI API queries.
    
    Args:
        messages_list: List of message lists for each request
        model: Model to use for all requests
        api_key: OpenAI API key
        base_url: Custom base URL
        save_paths: Optional list of paths to save responses
        max_workers: Maximum concurrent workers
        rate_limit_rpm: Requests per minute limit
        random_delay: Whether to add random delays
        chat_params: Additional chat parameters
        controller_type: Type of controller to use ("basic" or "smart")
        **executor_kwargs: Additional executor parameters
        
    Returns:
        List of processed response dictionaries
        
    Example:
        messages = [
            [{"role": "user", "content": "Hello, world!"}],
            [{"role": "user", "content": "What is Python?"}]
        ]
        results = openai_batch_query(messages, model="gpt-4o-mini")
    """
    if save_paths and len(messages_list) != len(save_paths):
        raise ValueError("Number of save paths must match number of message lists")
    
    # Prepare tasks
    tasks = []
    for i, messages in enumerate(messages_list):
        task = {
            "messages": messages,
            "model": model
        }
        
        if save_paths:
            task["save_path"] = save_paths[i]
        
        if chat_params:
            task.update(chat_params)
        
        tasks.append(task)
    
    # Internal async implementation
    async def _async_batch_query():
        """Internal async implementation."""
        # Create executor with session
        async with aiohttp.ClientSession() as session:
            # Update rate limiting configuration for the controller
            if controller_type == "smart":
                executor_kwargs.setdefault('smart_controller_config', SmartControllerConfig(
                    rate_limit_config=RateLimitConfig(max_rpm=rate_limit_rpm, safety_factor=0.9)
                ))
            else:
                executor_kwargs.setdefault('basic_controller_config', BasicControllerConfig(
                    rate_limit_config=RateLimitConfig(max_rpm=rate_limit_rpm, safety_factor=0.9)
                ))
            
            executor = OpenAIExecutor(
                api_key=api_key,
                base_url=base_url,
                max_workers=max_workers,
                controller_type=controller_type,
                random_delay=random_delay,
                shared_context={'session': session},
                **executor_kwargs
            )
            
            return await executor.execute(tasks)
    
    # Run the async implementation and return results
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    if loop.is_running():
        # If we're already in an async context, use a thread pool
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, _async_batch_query())
            return future.result()
    else:
        return loop.run_until_complete(_async_batch_query())


def _extract_response_data(response_data: dict) -> tuple[str, dict]:
    """Extract content and token usage from OpenAI response data."""
    content = ""
    token_usage = {}
    
    if "choices" in response_data and response_data["choices"]:
        choice = response_data["choices"][0]
        if "message" in choice:
            content = choice["message"].get("content", "")
    
    if "usage" in response_data:
        usage = response_data["usage"]
        completion_details = usage.get("completion_tokens_details", {})
        token_usage = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "reasoning_tokens": completion_details.get("reasoning_tokens", 0),
            "cached_tokens": completion_details.get("cached_tokens", 0)
        }
    
    return content, token_usage


def _save_content_if_needed(content: str, save_path: Optional[str]) -> None:
    """Save content to file if path is provided."""
    if save_path and content:
        try:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            # Don't fail the whole request for save errors
            pass
