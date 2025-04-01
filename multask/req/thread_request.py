import concurrent.futures
import threading
from queue import Queue
from tqdm import tqdm
import time
import random

class ThreadedCake:
    def __init__(
            self, 
            worker: callable, 
            post: callable=None, 
            max_workers=5, 
            max_retries=0,
        ):
        """
        :param worker: Main Function need IO time.
        :param post: Optional function to process results after fetching all result.
        :param max_workers: Number of worker threads to spawn.
        :param max_retries: Number of retries for each task in case of failure.
        """
        self._worker = worker
        self._post = post
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.task_queue = Queue()
        self.results_lock = threading.Lock()  # To avoid race conditions when appending results
        self.progress_bar = None

    def _init_bars(self, total_tasks):
        self.progress_bar = tqdm(total=total_tasks, desc="Progress", ncols=100)

    def _close_bars(self):
        if self.progress_bar:
            self.progress_bar.close()

    def _cancel_bars(self):
        if self.progress_bar:
            self.progress_bar.clear()
            self.progress_bar.close()

    def reset_task_queue(self, tasks):
        # Reset and populate task queue
        for i, task in enumerate(tasks):
            self.task_queue.put((i, task))

    def worker(self):
        worker_results = []
        while not self.task_queue.empty():
            index, task_params = self.task_queue.get()

            retries = 0
            while True:
                with self.results_lock:
                    self.progress_bar.refresh()

                try:
                    data = self._worker(**task_params)
                    worker_results.append((index, data))
                    break
                except Exception as e:
                    retries += 1
                    if retries > self.max_retries:
                        worker_results.append(
                            (index, {"error": "Max retries reached for {name}. {e}".format(name=task_params.get("name"), e=str(e))})
                        )
                        break
                    else:
                        tqdm.write("{e} encountered for {desc}. Retrying {r}/{mr}...".format(e=type(e), desc=str(e), r=retries, mr=self.max_retries)) 
                        # Exponential backoff
                        threading.Event().wait(2 ** retries)

            # After processing the task, update progress bar
            with self.results_lock:
                self.progress_bar.update(1)

        return worker_results

    def threaded_cake(self, tasks):
        self.reset_task_queue(tasks)
        self._init_bars(len(tasks))

        # Using ThreadPoolExecutor for concurrent threads
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self.worker) for _ in range(self.max_workers)]
            for future in concurrent.futures.as_completed(futures):
                try:
                    worker_results = future.result()
                    results.extend(worker_results)
                except Exception as e:
                    print(f"Worker failed: {e}")

        self._close_bars()

        results = sorted(results, key=lambda x: x[0])
        if self._post:
            results = self._post(results)

        return results

    def run(self, tasks):
        return self.threaded_cake(tasks)


# Debugging functions
def debug_worker(*args, **kwargs):
    """Scraping function: Simulates a scraping process."""
    time.sleep(random.uniform(0.1, 0.5))
    return "Scraped data"

def debug_error_worker(*args, **kwargs):
    """Scraping function: Simulates a scraping process."""
    time.sleep(random.uniform(0.1, 0.5))
    raise Exception("Error encountered")
    

if __name__=="__main__":
    tasks = [{"name": "Task 1"}, {"name": "Task 2"}, {"name": "Task 3"}]
    cake = ThreadedCake(worker=debug_error_worker, max_workers=5, max_retries=2)
    results = cake.run(tasks)

    print(results)
