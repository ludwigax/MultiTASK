import os
from typing import Dict, Union

from aiohttp import ClientSession
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

    async with session.post(url, json=data, headers=headers, proxy=proxy) as response:
        response.raise_for_status()
        data = await response.json()
    return data
    
def openai_parse(response: Dict, filtered_fields=None, params=None) -> Dict[str, Union[str, Dict]]:
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    model = response.get("model", "")

    usage = response.get("usage", {})
    if usage:
        token_usage = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "reasoning_tokens": usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
            "output_tokens": usage.get("completion_tokens_details", {}).get("accepted_prediction_tokens", 0)
        }

    return {
        "content": content,
        "model": model,
        "token_usage": token_usage
    }

def openai_save(data, params=None, **kwargs):
    if not params.get("save_path"):
        raise ValueError("The 'save_path' field is required.")
    results = openai_parse(data)
    with open(params["save_path"], "w", encoding="utf-8") as f:
        f.write(results["content"])
    return results

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