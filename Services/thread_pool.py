import threading
import queue
import logging
from typing import Callable, Any
from Services.runtime_manager import runtime_man

# Sentinel object to signal worker threads to stop
_STOP_SENTINEL = object() # Corrected typo from SENTENTINEL

class CustomThreadPool:
    def __init__(self, max_workers: int):
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        # self.mutex = threading.Lock() # Removed redundant mutex, using specific lock below
        self._max_workers = max_workers
        self._task_queue = queue.Queue()
        self._workers = []
        self._shutdown_lock = threading.Lock()
        self._is_shutting_down = False
        # 💡 NEW: Mutex to control task execution (will serialize execution)
        self.retard_ai_lock = threading.Lock()
        self._execution_lock = threading.Lock() 

        for i in range(self._max_workers):
            thread = threading.Thread(target=self._worker, name=f"Worker-{i}", daemon=True)
            self._workers.append(thread)
            thread.start()
        logging.info(f"CustomThreadPool started with {max_workers} workers.")

    def _worker(self):
        """Target function for worker threads."""
        while runtime_man.is_run():
            
                
            task_item = None # Initialize to handle potential errors before assignment
            try:
                # Block until a task is available or the sentinel is received
                # queue.Queue().get() is already thread-safe
                with self.retard_ai_lock:
                    task_item = self._task_queue.get(block=True)

                if task_item is _STOP_SENTINEL:
                    # Received stop signal, put it back for other threads and exit
                    self._task_queue.put(_STOP_SENTINEL)
                    logging.debug(f"{threading.current_thread().name} received stop signal.")
                    break 
                
                func, args, kwargs = task_item
                
                # retard ai 
                
                logging.debug(f"{threading.current_thread().name} acquired execution lock.")
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    logging.error(f"Task execution failed in {threading.current_thread().name}: {e}", exc_info=True)
                finally:
                        # 💡 Release lock AFTER execution (or error)
                        logging.debug(f"{threading.current_thread().name} releasing execution lock.")
                # Lock is released automatically by 'with' statement here
                    
                # Mark task as done *after* execution and outside the lock
                self._task_queue.task_done()
                    
            except queue.Empty:
                 # This should ideally not happen with block=True unless interrupted
                 continue
            except Exception as e:
                 # Catch broader issues in the worker loop itself
                 logging.error(f"Error in worker {threading.current_thread().name}: {e}", exc_info=True)
                 # Attempt to mark task done if one was potentially dequeued before the error
                 if task_item is not None and task_item is not _STOP_SENTINEL:
                     try:
                         self._task_queue.task_done()
                     except ValueError:
                         pass # No task was active or already done
                 # Decide if the worker should continue or terminate based on the error

    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any):
        """Submit a task to the thread pool."""
        with self._shutdown_lock:
            if self._is_shutting_down:
                raise RuntimeError("Cannot schedule new tasks after shutdown.")
            
            # Put the function and its arguments onto the queue
            self._task_queue.put((func, args, kwargs))

    def shutdown(self, wait: bool = True):
        """Signal all worker threads to stop and optionally wait for completion."""
        with self._shutdown_lock:
            if self._is_shutting_down:
                return # Already shutting down
            self._is_shutting_down = True
        
        logging.info("Initiating shutdown of CustomThreadPool...")
        
        # Signal each worker to stop by putting the sentinel on the queue
        for _ in self._workers:
            self._task_queue.put(_STOP_SENTINEL)
            
        if wait:
            logging.debug("Waiting for worker threads to terminate...")
            for worker in self._workers:
                worker.join()
            logging.info("All worker threads terminated.")

        logging.info("CustomThreadPool shutdown complete.")

# --- Example Usage (similar to callback_manager) ---
# Replace the ThreadPoolExecutor in callback_manager.py:
#
# from concurrent.futures import ThreadPoolExecutor # Remove this
# from your_module import CustomThreadPool # Import the new class
#
# class ThreadedCallbackService:
#     def __init__(self, max_workers: int = 5):
#         self._callbacks: Dict[str, List[Callable[[float], None]]] = {}
#         self._lock = Lock()
#         # self._executor = ThreadPoolExecutor(max_workers=max_workers) # Remove this
#         self._executor = CustomThreadPool(max_workers=max_workers) # Use the custom pool
#
#     # ... (rest of the class remains the same) ...
#
#     def shutdown(self, wait: bool = True):
#         logging.info("Shutting down callback executor")
#         self._executor.shutdown(wait=wait) # Call the custom shutdown