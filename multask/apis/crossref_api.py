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

from ..core import AsyncExecutor, RateLimitConfig, CircuitBreakerConfig
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
        # Set up CrossRef-specific rate limiting (be conservative)
        if 'rate_limit_config' not in kwargs:
            kwargs['rate_limit_config'] = RateLimitConfig(
                base_rpm=50,  # Conservative rate limit
                safety_factor=0.8
            )
        
        # Set up circuit breaker
        if 'circuit_breaker_config' not in kwargs:
            kwargs['circuit_breaker_config'] = CircuitBreakerConfig(
                failure_threshold=3,
                timeout=120.0  # 2 minutes
            )
        
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


async def crossref_batch_query(
    requests: List[Dict[str, Any]],
    mailto: Optional[str] = None,
    max_workers: int = 3,
    rate_limit_rpm: int = 50,
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
        **executor_kwargs: Additional executor parameters
        
    Returns:
        List of processed metadata dictionaries
        
    Example:
        requests = [
            {'type': 'doi', 'doi': '10.1038/nature12373'},
            {'type': 'query', 'query': 'machine learning', 'rows': 10}
        ]
        results = await crossref_batch_query(requests, mailto='user@example.com')
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
    
    # Set up rate limiting
    rate_config = RateLimitConfig(base_rpm=rate_limit_rpm, safety_factor=0.8)
    
    # Create executor with session
    async with aiohttp.ClientSession() as session:
        executor = CrossRefExecutor(
            mailto=mailto,
            max_workers=max_workers,
            rate_limit_config=rate_config,
            shared_context={'session': session},
            **executor_kwargs
        )
        
        results = await executor.execute(tasks)
    
    # Extract just the processed data (remove index info)
    return [result[1] if isinstance(result[1], dict) and 'error' not in result[1] 
            else result[1] for result in results]
