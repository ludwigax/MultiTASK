"""
CrossRef API interface for academic literature retrieval.

This module provides a high-level interface for querying the CrossRef API
using the multask execution framework with proper error handling and rate limiting.
"""

import itertools
from typing import List, Dict, Any, Optional, Union

# Check for required dependencies
try:
    import aiohttp
except ImportError:
    raise ImportError(
        "aiohttp is required for CrossRef API. Install with: pip install aiohttp"
    )

from ..core import AsyncExecutor
from ..core.controllers import BasicController, SmartController, BasicControllerConfig, SmartControllerConfig
from ..core.rate_limiter import RateLimitConfig
from ..core.exceptions import classify_exception, FatalError


async def crossref_doi_worker(session: aiohttp.ClientSession, doi: str, 
                              mailto: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """
    Worker function for fetching single DOI from CrossRef API.
    
    Args:
        session: HTTP session for making requests
        doi: DOI to fetch
        mailto: Email for polite pool access
        **kwargs: Additional parameters
        
    Returns:
        Dict containing the CrossRef response
        
    Raises:
        FatalError: If DOI is invalid or missing
    """
    if not doi:
        raise FatalError("DOI is required for CrossRef DOI search")
    
    params = {}
    if mailto:
        params["mailto"] = mailto
    
    url = f"https://api.crossref.org/works/{doi}"
    
    try:
        async with session.get(url, params=params) as response:
            response.raise_for_status()
            data = await response.json()
            return data
    except aiohttp.ClientResponseError as e:
        if e.status == 404:
            raise FatalError(f"DOI not found: {doi}")
        raise  # Let classify_exception handle other HTTP errors


async def crossref_query_worker(session: aiohttp.ClientSession, query: str,
                               rows: int = 20, offset: int = 0,
                               mailto: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """
    Worker function for CrossRef keyword search.
    
    Args:
        session: HTTP session for making requests
        query: Search query string
        rows: Number of results to fetch
        offset: Offset for pagination
        mailto: Email for polite pool access
        **kwargs: Additional query parameters (filter, sort, order, etc.)
        
    Returns:
        Dict containing the CrossRef response
        
    Raises:
        FatalError: If query is invalid or missing
    """
    if not query:
        raise FatalError("Query is required for CrossRef keyword search")
    
    params = {
        "query": query,
        "rows": rows,
        "offset": offset
    }
    
    if mailto:
        params["mailto"] = mailto
    
    # Add additional query parameters
    for key, value in kwargs.items():
        if key not in ["session", "query", "rows", "offset", "mailto"]:
            params[key] = value
    
    url = "https://api.crossref.org/works"
    
    try:
        async with session.get(url, params=params) as response:
            response.raise_for_status()
            data = await response.json()
            return data
    except aiohttp.ClientResponseError as e:
        if e.status == 400:
            raise FatalError(f"Invalid query parameters: {params}")
        raise  # Let classify_exception handle other HTTP errors


def crossref_result_processor(results: List[tuple]) -> List[Dict[str, Any]]:
    """
    Process CrossRef API results and extract metadata.
    
    Args:
        results: List of (index, raw_response) tuples
        
    Returns:
        List of processed metadata dictionaries
    """
    # Find the maximum index to determine result list size
    max_index = max(index for index, _ in results) if results else -1
    processed_results = [None] * (max_index + 1)
    
    for index, response in results:
        if isinstance(response, dict) and "error" in response:
            # Error response
            processed_results[index] = {
                "index": index,
                "error": response["error"]
            }
            continue
        
        try:
            # Check response structure
            if not isinstance(response, dict) or response.get("status") != "ok":
                processed_results[index] = {
                    "index": index,
                    "error": "Invalid response from CrossRef API"
                }
                continue
            
            # Extract items
            if response.get("message-type") == "work":
                items = [response.get("message")]
            elif response.get("message-type") == "work-list":
                items = response.get("message", {}).get("items", [])
            else:
                processed_results[index] = {
                    "index": index,
                    "error": f"Unknown message type: {response.get('message-type')}"
                }
                continue
            
            # Process items (for now, take the first item if multiple)
            # TODO: Handle multiple items per response properly
            if items:
                item = items[0]  # Take first item
                try:
                    # Extract authors
                    authors = []
                    for author in item.get("author", []):
                        if "name" in author:
                            authors.append(author["name"])
                        else:
                            family = author.get("family", "")
                            given = author.get("given", "")
                            if family or given:
                                authors.append(f"{family}, {given}".strip(", "))
                    
                    # Extract publication date
                    pub_date = None
                    if "published-print" in item:
                        date_parts = item["published-print"].get("date-parts", [[]])[0]
                        if date_parts:
                            pub_date = date_parts[0]  # Year
                    elif "published-online" in item:
                        date_parts = item["published-online"].get("date-parts", [[]])[0]
                        if date_parts:
                            pub_date = date_parts[0]  # Year
                    
                    metadata = {
                        "index": index,
                        "title": item.get("title", [""])[0] if item.get("title") else "",
                        "authors": authors,
                        "journal": item.get("container-title", [""])[0] if item.get("container-title") else "",
                        "volume": item.get("volume", ""),
                        "issue": item.get("issue", ""),
                        "pages": item.get("page", ""),
                        "published_year": pub_date,
                        "doi": item.get("DOI", ""),
                        "url": item.get("URL", ""),
                        "abstract": item.get("abstract", ""),
                        "citation_count": item.get("is-referenced-by-count", 0),
                        "reference_count": len(item.get("reference", [])),
                        "type": item.get("type", ""),
                        "publisher": item.get("publisher", ""),
                        "issn": item.get("ISSN", []),
                        "language": item.get("language", "")
                    }
                    processed_results[index] = metadata
                    
                except Exception as e:
                    processed_results[index] = {
                        "index": index,
                        "doi": item.get("DOI", "unknown") if item else "unknown",
                        "error": f"Error processing item: {str(e)}"
                    }
            else:
                processed_results[index] = {
                    "index": index,
                    "error": "No items found in response"
                }
                    
        except Exception as e:
            processed_results[index] = {
                "index": index,
                "error": f"Error processing response: {str(e)}"
            }
    
    return processed_results


class CrossRefExecutor(AsyncExecutor):
    """
    Specialized executor for CrossRef API queries.
    
    Features:
    - Automatic rate limiting for CrossRef API
    - Built-in result processing and metadata extraction
    - Support for both DOI and keyword searches
    - Polite pool access with mailto parameter
    """
    
    def __init__(self, mailto: Optional[str] = None, **kwargs):
        """
        Initialize CrossRef executor.
        
        Args:
            mailto: Email for polite pool access (recommended)
            **kwargs: Additional executor parameters
        """
        # Set up default controller configuration
        _setup_crossref_controller_config(kwargs)
        
        # Use dynamic worker selection
        super().__init__(
            worker=self._dynamic_worker,
            result_processor=crossref_result_processor,
            **kwargs
        )
        
        self.mailto = mailto
    
    async def _dynamic_worker(self, session: aiohttp.ClientSession, 
                             task_type: str, **kwargs) -> Dict[str, Any]:
        """
        Dynamic worker that selects appropriate function based on task type.
        
        Args:
            session: HTTP session
            task_type: Type of task ('doi' or 'query')
            **kwargs: Task parameters
            
        Returns:
            CrossRef API response
        """
        # Add mailto to kwargs if provided
        if self.mailto:
            kwargs["mailto"] = self.mailto
        
        if task_type == "doi":
            return await crossref_doi_worker(session=session, **kwargs)
        elif task_type == "query":
            return await crossref_query_worker(session=session, **kwargs)
        else:
            raise FatalError(f"Unknown task type: {task_type}")


def crossref_batch_query(
    requests: List[Dict[str, Any]],
    mailto: Optional[str] = None,
    max_workers: int = 3,
    rate_limit_rpm: int = 50,
    controller_type: str = "smart",
    **executor_kwargs
) -> List[Dict[str, Any]]:
    """
    High-level function for batch CrossRef queries.
    
    Args:
        requests: List of request dictionaries. Each should contain either:
                 - {'type': 'doi', 'doi': 'DOI_STRING'}
                 - {'type': 'query', 'query': 'SEARCH_TERMS', 'rows': 20, 'offset': 0}
        mailto: Email for polite pool access
        max_workers: Maximum concurrent workers
        rate_limit_rpm: Requests per minute limit
        controller_type: Type of controller to use ("basic" or "smart")
        **executor_kwargs: Additional executor parameters
        
    Returns:
        List of processed metadata dictionaries
        
    Example:
        requests = [
            {'type': 'doi', 'doi': '10.1038/nature12373'},
            {'type': 'query', 'query': 'machine learning', 'rows': 10}
        ]
        results = crossref_batch_query(requests, mailto='user@example.com')
    """
    # Convert requests to tasks
    tasks = []
    for i, request in enumerate(requests):
        if request.get('type') == 'doi':
            task = {
                'task_type': 'doi',
                'doi': request['doi']
            }
        elif request.get('type') == 'query':
            task = {
                'task_type': 'query',
                'query': request['query'],
                'rows': request.get('rows', 20),
                'offset': request.get('offset', 0)
            }
            # Add any additional query parameters
            for key, value in request.items():
                if key not in ['type', 'query', 'rows', 'offset']:
                    task[key] = value
        else:
            raise ValueError(f"Invalid request type in request {i}: {request}")
        
        tasks.append(task)
    
    # Internal async implementation
    async def _async_batch_query():
        """Internal async implementation."""
        # Create executor with session
        async with aiohttp.ClientSession() as session:
            # Update rate limiting configuration for the controller
            if controller_type == "smart":
                executor_kwargs.setdefault('smart_controller_config', SmartControllerConfig(
                    rate_limit_config=RateLimitConfig(max_rpm=rate_limit_rpm, safety_factor=0.8)
                ))
            else:
                executor_kwargs.setdefault('basic_controller_config', BasicControllerConfig(
                    rate_limit_config=RateLimitConfig(max_rpm=rate_limit_rpm, safety_factor=0.8)
                ))
            
            executor = CrossRefExecutor(
                mailto=mailto,
                max_workers=max_workers,
                controller_type=controller_type,
                shared_context={'session': session},
                **executor_kwargs
            )
            
            return await executor.execute(tasks)
    
    # Run the async implementation and return results
    import asyncio
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


def _setup_crossref_controller_config(kwargs: dict) -> None:
    """Set up default controller configuration for CrossRef API."""
    controller_type = kwargs.pop('controller_type', 'smart')
    
    if controller_type == 'smart':
        if 'smart_controller_config' not in kwargs:
            kwargs['smart_controller_config'] = SmartControllerConfig(
                rate_limit_config=RateLimitConfig(
                    max_rpm=50,  # Conservative rate limit
                    safety_factor=0.8
                ),
                failure_threshold=3,
                circuit_timeout=120.0  # 2 minutes
            )
    else:
        if 'basic_controller_config' not in kwargs:
            kwargs['basic_controller_config'] = BasicControllerConfig(
                rate_limit_config=RateLimitConfig(
                    max_rpm=50,
                    safety_factor=0.8
                )
            )
    
    kwargs['controller_type'] = controller_type
