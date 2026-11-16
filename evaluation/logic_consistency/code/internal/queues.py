"""This module manages evaluation queues."""

from queue import Queue
from threading import Lock

class BaseQueueManager:


    def __init__(self):

        self.queue = Queue()

        self.lock = Lock()

    def add(self, task):


        self.queue.put(task)

    def get(self):


        return self.queue.get(block=False)

    def empty(self):


        return self.queue.empty()

class TaskQueueManager(BaseQueueManager):


    def __init__(self):

        super().__init__()

class APICallManager(BaseQueueManager):


    def __init__(self, frequency):

        super().__init__()

        self.frequency = frequency
