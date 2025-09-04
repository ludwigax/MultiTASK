"""
Examples demonstrating the new API interfaces with optional dependency handling.

This file shows how to use the three predefined API interfaces:
- CrossRef API for academic literature
- OpenAI API for language models
- PDF API for document processing
"""

import asyncio
from typing import List, Dict, Any

# Import multask and check available APIs
import multask
from multask import apis


async def example_crossref_api():
    """Example using CrossRef API interface."""
    print("=== CrossRef API Example ===")
    
    # Check if CrossRef API is available
    available, error = apis.check_api_availability('crossref')
    if not available:
        print(f"CrossRef API not available: {error}")
        return
    
    # Example requests
    requests = [
        # DOI lookup
        {
            'type': 'doi',
            'doi': '10.1038/nature12373'
        },
        # Keyword search
        {
            'type': 'query',
            'query': 'machine learning neural networks',
            'rows': 5,
            'filter': 'type:journal-article'
        }
    ]
    
    try:
        results = await apis.crossref_batch_query(
            requests=requests,
            mailto="researcher@university.edu",  # Recommended for polite pool
            max_workers=2,
            rate_limit_rpm=30  # Conservative rate limiting
        )
        
        print(f"Retrieved {len(results)} results")
        for i, result in enumerate(results):
            if result is None:
                print(f"  Request {i}: No result (task may have failed)")
            elif isinstance(result, dict) and 'error' in result:
                print(f"  Request {i}: Error - {result['error']}")
            else:
                print(f"  Request {i}: {result.get('title', 'No title')[:60]}...")
                
    except Exception as e:
        print(f"CrossRef API error: {e}")


async def example_openai_api():
    """Example using OpenAI API interface."""
    print("\n=== OpenAI API Example ===")
    
    # Check if OpenAI API is available
    available, error = apis.check_api_availability('openai')
    if not available:
        print(f"OpenAI API not available: {error}")
        return
    
    # Example messages
    messages_list = [
        [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is machine learning?"}
        ],
        [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Explain quantum computing in simple terms."}
        ]
    ]
    
    try:
        results = await apis.openai_batch_query(
            messages_list=messages_list,
            model="gpt-4o-mini",
            max_workers=2,
            rate_limit_rpm=100,
            chat_params={
                "temperature": 0.7,
                "max_tokens": 150
            }
        )
        
        print(f"Processed {len(results)} requests")
        
        # Calculate total cost
        token_usages = [r.get('token_usage', {}) for r in results if 'token_usage' in r]
        if token_usages:
            total_cost = apis.openai_price_calculator(token_usages, "gpt-4o-mini")
            print(f"Total cost: ${total_cost}")
        
        for i, result in enumerate(results):
            if 'error' in result:
                print(f"  Request {i}: Error - {result['error']}")
            else:
                content = result.get('content', '')[:100]
                print(f"  Request {i}: {content}...")
                
    except Exception as e:
        print(f"OpenAI API error: {e}")


async def example_pdf_api():
    """Example using PDF API interface."""
    print("\n=== PDF API Example ===")
    
    # Check if PDF API is available
    available, error = apis.check_api_availability('pdf')
    if not available:
        print(f"PDF API not available: {error}")
        return
    
    # Example PDF files (you would need actual PDF files)
    pdf_files = [
        "example1.pdf",  # These would be real PDF file paths
        "example2.pdf",
        "example3.pdf"
    ]
    
    # Filter to only existing files for demo
    import os
    existing_pdfs = [f for f in pdf_files if os.path.exists(f)]
    
    if not existing_pdfs:
        print("No PDF files found for processing. Please provide actual PDF file paths.")
        return
    
    try:
        results = await apis.pdf_batch_extract(
            pdf_paths=existing_pdfs,
            output_dir="extracted_texts",
            method="advanced",  # or "simple"
            max_workers=2,
            no_references=True
        )
        
        print(f"Processed {len(results)} PDF files")
        
        for i, result in enumerate(results):
            if not result.get('success', False):
                print(f"  PDF {i}: Error - {result.get('error', 'Unknown error')}")
            else:
                word_count = result.get('word_count', 0)
                page_count = result.get('page_count', 0)
                print(f"  PDF {i}: {word_count} words, {page_count} pages")
                
    except Exception as e:
        print(f"PDF processing error: {e}")


async def example_custom_executor():
    """Example using core executors directly for custom tasks."""
    print("\n=== Custom Executor Example ===")
    
    # Define a custom worker function
    async def custom_api_worker(session, endpoint: str, api_key: str, **kwargs):
        """Custom worker for some API."""
        import aiohttp
        headers = {"Authorization": f"Bearer {api_key}"}
        async with session.get(endpoint, headers=headers) as response:
            return {
                "endpoint": endpoint,
                "status": response.status,
                "success": response.status == 200
            }
    
    # Prepare tasks
    tasks = [
        {"endpoint": "https://api.example.com/users"},
        {"endpoint": "https://api.example.com/posts"},
        {"endpoint": "https://api.example.com/comments"}
    ]
    
    # Create executor with shared context
    import aiohttp
    async with aiohttp.ClientSession() as session:
        executor = multask.AsyncExecutor(
            worker=custom_api_worker,
            shared_context={
                "session": session,
                "api_key": "your-api-key-here"
            },
            max_workers=2,
            rate_limit_config=multask.RateLimitConfig(base_rpm=60)
        )
        
        try:
            results = await executor.execute(tasks)
            print(f"Processed {len(results)} custom API requests")
            
            for i, result in enumerate(results):
                if isinstance(result[1], dict) and 'error' in result[1]:
                    print(f"  Request {i}: Error - {result[1]['error']}")
                else:
                    success = result[1].get('success', False)
                    status = result[1].get('status', 'unknown')
                    print(f"  Request {i}: Status {status}, Success: {success}")
                    
        except Exception as e:
            print(f"Custom executor error: {e}")


def show_available_apis():
    """Show which APIs are currently available."""
    print("=== Available APIs ===")
    available_apis = apis.get_available_apis()
    
    if available_apis:
        print(f"Available: {', '.join(available_apis)}")
    else:
        print("No APIs available - missing dependencies")
    
    # Check each API individually
    for api_name in ['crossref', 'openai', 'pdf']:
        available, error = apis.check_api_availability(api_name)
        status = "✓ Available" if available else f"✗ Not available: {error}"
        print(f"  {api_name}: {status}")


async def main():
    """Run all examples."""
    show_available_apis()
    
    # Run API examples
    await example_crossref_api()
    await example_openai_api()
    await example_pdf_api()
    await example_custom_executor()


if __name__ == "__main__":
    asyncio.run(main())
