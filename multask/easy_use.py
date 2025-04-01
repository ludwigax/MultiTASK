from . import AsyncCake, ThreadedCake
from .utils import oai as openai
from .utils import crossref as crf
from .utils import pdf as pdf

import os
import warnings
from typing import List, Tuple, Dict, Union, Optional, Any

def batch_query_openai(
        messages: List[List],
        save_paths: Optional[List[str]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        proxy: Optional[str] = None,
        rpm_cap = 1000,
        max_workers = 5,
        random_delay = False, # avoid for 429 error
        chat_params: Dict[str, Any] = {},
    ) -> List:
    """
    chat_params: {
    model: str (required),
    reasoning_effort: str,
    n: int,
    seed: int,
    temperature: int,
    max_completion_tokens: int
    }
    """

    if save_paths and (len(messages) != len(save_paths)):
        raise ValueError("The number of save paths must match the number of messages.")
    
    if not (os.environ.get("OPENAI_API_KEY") or api_key):
        raise ValueError("The 'OPENAI_API_KEY' is not set.")
    
    if not chat_params.get("model"):
        chat_params["model"] = "gpt-4o-mini"

    if (not chat_params["model"] in ["o1-mini", "o3-mini"]) \
        and chat_params.get("reasoning_effort") is not None:
        warnings.warn("The 'reasoning_effort' field is only available for the 'o1-mini' and 'o3-mini' models.")
        chat_params["reasoning_effort"] = None
    
    if base_url:
        print("Base URL is set:", base_url)

    if proxy:
        print("Proxy is set:", proxy)
    
    if random_delay:
        print("Random delay is enabled.")

    if not save_paths:
        print("No save paths provided. Results will not be immediacy saved.")

    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url
    if proxy:
        os.environ["PROXY"] = proxy

    tasks = []
    for i in range(len(messages)):
        task = {
            "name": f"Task {i}",
            "data": {
                "messages": messages[i], **chat_params
            }
        }
        if save_paths:
            task["save_path"] = save_paths[i]
        tasks.append(task)

    if save_paths:
        helper = openai.openai_save
    else:
        helper = openai.openai_parse

    async_cake = AsyncCake(
        worker=openai.openai_post,
        helper=helper,
        max_workers=max_workers,
        max_concurrent_requests=max_workers,
        rpm_cap=rpm_cap,
        random_delay=random_delay,
        max_retries=3,
    )

    results = async_cake.run(tasks)
    return results


def oai_price_calculator(token_usages: Union[Dict, List[Dict]], model_name: str) -> float:
    """
    token_usage (Dict or List[Dict]): 
    model_name (str): e.g. "gpt-4", "gpt-4-32k", "gpt-3.5-turbo"。

    float: total cost in USD.
    """

    pricing = {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "o1": {"input": 15.00, "output": 60.00},
        "o1-mini": {"input": 1.10, "output": 4.40},
        "o3-mini": {"input": 1.10, "output": 4.40},
        "qwen-max": {"input": 0.34, "output": 1.37},
        "qwen-max-latest": {"input": 0.34, "output": 1.37},
    }

    if model_name not in pricing:
        raise ValueError(f"The model '{model_name}' is not supported.")

    if isinstance(token_usages, dict):
        token_usages = [token_usages]

    total_cost = 0.0
    for usage in token_usages:
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        input_price_per_1m = pricing[model_name]["input"]
        output_price_per_1m = pricing[model_name]["output"]

        input_cost = (prompt_tokens) / 1e6 * input_price_per_1m
        output_cost = (completion_tokens) / 1e6 * output_price_per_1m

        total_cost += input_cost + output_cost
    return round(total_cost, 6)


def batch_query_crossref(
        queries: List[str],
        mailto: Optional[str] = None,
        proxy: Optional[str] = None,
        rpm_cap = 1000,
        max_workers = 5,
        random_delay = False, # avoid for 429 error
    ):
    if proxy:
        print("Proxy is set:", proxy)
        os.environ["PROXY"] = proxy
    
    if random_delay:
        print("Random delay is enabled.")

    tasks = []
    for i in range(len(queries)):
        task = {
            "name": f"Task {i}",
            "params": {
                "query": queries[i],
                "rows": 1,
                "mailto": mailto
            }
        }
        tasks.append(task)

    async_cake = AsyncCake(
        worker=crf.crossref_batch_scrap,
        helper=crf.crossref_parse,
        # post=crf.crossref_post,
        max_workers=max_workers,
        max_concurrent_requests=max_workers,
        rpm_cap=rpm_cap,
        random_delay=random_delay,
        max_retries=3,
    )

    results = async_cake.run(tasks)
    return results