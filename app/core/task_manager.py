import asyncio
import uuid
import logging
from typing import Dict, Any, Optional, Callable
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

class TaskManager:
    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=4)

    def submit_task(self, func: Callable, *args, **kwargs) -> str:
        """
        Submits a function to be executed in the background.
        Returns a task_id.
        """
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {
            "status": "PENDING",
            "result": None,
            "error": None
        }
        
        # Run the task in a separate thread
        asyncio.create_task(self._run_task(task_id, func, *args, **kwargs))
        
        return task_id

    async def _run_task(self, task_id: str, func: Callable, *args, **kwargs):
        """
        Internal method to run the task and update its status.
        """
        self._tasks[task_id]["status"] = "PROCESSING"
        logger.info(f"Task {task_id} started.")
        
        try:
            # Run blocking functions in the executor
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(self._executor, func, task_id, *args, **kwargs)
            
            self._tasks[task_id]["status"] = "SUCCESS"
            self._tasks[task_id]["result"] = result
            logger.info(f"Task {task_id} completed successfully.")
            
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            self._tasks[task_id]["status"] = "FAILURE"
            self._tasks[task_id]["error"] = str(e)

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns the status and result of a task.
        """
        return self._tasks.get(task_id)

# Global instance
task_manager = TaskManager()
