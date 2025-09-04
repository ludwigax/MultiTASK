import os
import json
import asyncio
from typing import Dict, Union, List

from aiohttp import ClientSession, ClientTimeout
from .. import AsyncCake

async def openai_post(session: ClientSession, data={}, **kwargs) -> Dict:
    api_key = os.getenv("OPENAI_API_KEY")
    url = os.getenv("OPENAI_BASE_URL")
    if not url:
        url = "https://api.openai.com/v1"
    url += "/chat/completions" # only for chat model

    proxy = os.getenv("PROXY")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    if not data.get("model"):
        data["model"] = "gpt-4o-mini"
    if not data.get("messages"):
        raise ValueError("The 'messages' field is required.")
    
    is_streaming = data.get("stream", False)

    if is_streaming:
        return await _collect_streaming_chunks(session, url, data, headers, proxy)
    else:
        async with session.post(url, json=data, headers=headers, proxy=proxy) as response:
            response.raise_for_status()
            data = await response.json()
        return data


async def _collect_streaming_chunks(session: ClientSession, url: str, data: dict, headers: dict, proxy: str) -> str:
    """Collect all streaming chunks while session is active"""
    chunks = []

    timeout = ClientTimeout(
        total=480,      # 8 minutes total timeout
        connect=30,     # 30 seconds to establish connection
        sock_read=60    # 60 seconds between chunks 
    )
    
    try:
        async with session.post(url, json=data, headers=headers, proxy=proxy, timeout=timeout) as response:
            response.raise_for_status()

            content_type = response.headers.get('content-type', '')
            if 'text/event-stream' not in content_type and 'text/plain' not in content_type:
                print(f"Warning: Unexpected content-type for streaming: {content_type}")
            
            chunk_count = 0
            async for line in response.content:
                line_str = line.decode('utf-8').strip()
                
                if not line_str or line_str.startswith(':'):
                    continue
            
                # Parse SSE format
                if line_str.startswith('data: '):
                    data_str = line_str[6:]
            
                if data_str.strip() == '[DONE]':
                    break
            
                try:
                    chunk_data = json.loads(data_str)
                
                    # Extract content from streaming response
                    if 'choices' in chunk_data and len(chunk_data['choices']) > 0:
                        choice = chunk_data['choices'][0]
                        delta = choice.get('delta', {})
                    
                        if choice.get('finish_reason') is not None:
                            break
                        
                        if 'content' in delta and delta['content']:
                            chunks.append(delta['content'])
                            chunk_count += 1

                except json.JSONDecodeError:
                    continue

    except asyncio.TimeoutError:
        print("Connection timeout during streaming request")
        raise
    except Exception as e:
        print(f"Streaming error: {type(e).__name__}: {e}")
        raise
    
    return ''.join(chunks)
                        
    
def openai_parse(response: Union[Dict, str], save_path=None, filtered_fields=None, **kwargs) -> Dict[str, Union[str, object, Dict]]:
    # Check if response is a list of streaming chunks
    if isinstance(response, str):
        # Streaming response - return generator that yields string chunks
        model = kwargs.get("model", "")
        
        return {
            "content": response,  # Return generator that yields str chunks
            "model": model,
            "token_usage": {}  # Token usage not available in streaming mode
        }
    else:
        # Non-streaming response - original behavior
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        model = response.get("model", "")

        usage = response.get("usage", {})
        token_usage = {}
        if usage:
            completion_tokens_details = usage.get("completion_tokens_details") or {}
            token_usage = {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "reasoning_tokens": completion_tokens_details.get("reasoning_tokens", 0),
                "output_tokens": completion_tokens_details.get("accepted_prediction_tokens", 0)
            }

        if save_path:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(content)

        return {
            "content": content,
            "model": model,
            "token_usage": token_usage
        } 

# def openai_post(data):
#     raise NotImplementedError("There is no post-processing function for OpenAI API.")


async def fake_openai_post(*args, **kwargs):
    import asyncio
    import random
    await asyncio.sleep(random.uniform(0.1, 0.5))
    return {"choices": [{"message": {"content": "This is a fake response."}}]}


if __name__=="__main__":
    pass
    # tasks = []
    # for i in range(5):
    #     task = {
    #         "name": f"Task {i}",
    #         "data": {
    #             "model": "gpt-4o-mini",
    #             "messages": [
    #                 {"role": "system", "content": "You are a helpful assistant."},
    #                 {"role": "user", "content": "What is the capital of France?"},
    #             ]
    #         }
    #     }
    #     tasks.append(task)
    
    # os.environ["PROXY"] = "http://localhost:10809"
    # os.environ["OPENAI_API_KEY"] = WORK_API_KEY

    # async_crawler = AsyncCake(
    #     worker=openai_post,
    #     helper=openai_parse,
    #     max_workers=5,
    #     max_concurrent_requests=5,
    # )
    # results = async_crawler.run(tasks)