from queue import Queue
from threading import Lock

class BaseQueueManager:
    """
    基础队列管理器基类

    输入:
    - 无参数

    输出:
    - self.queue: Queue对象，用于存储任务
    - self.lock: Lock对象，用于线程同步
    """
    def __init__(self):
        # 输出：创建一个先进先出的队列实例
        self.queue = Queue()
        # 输出：创建一个线程锁实例，保证线程安全操作
        self.lock = Lock()

    def add(self, task):
        """
        向队列中添加任务

        输入:
        - task: Any - 要添加的任务对象

        输出:
        - 将任务放入队列中（队列内部操作）
        """
        # 输出：将任务放入队列（线程安全，Queue内部已处理）
        self.queue.put(task)

    def get(self):
        """
        从队列中获取任务

        输出:
        - Any: 从队列中取出的任务对象

        异常:
        - 如果队列为空且block=False，会引发queue.Empty异常
        """
        # 输出：从队列中获取任务，block=False表示非阻塞模式
        return self.queue.get(block=False)

    def empty(self):
        """
        检查队列是否为空

        输出:
        - bool: 队列为空返回True，否则返回False
        """
        # 输出：返回队列的空状态
        return self.queue.empty()

class TaskQueueManager(BaseQueueManager):
    """
    任务队列管理器类（继承自BaseQueueManager）

    输入:
    - 无参数

    输出:
    - 继承父类的队列和锁，用于一般任务管理
    """
    def __init__(self):
        # 输出：调用父类初始化方法
        super().__init__()

class APICallManager(BaseQueueManager):
    """
    API调用管理器类（继承自BaseQueueManager）

    输入:
    - frequency: int/float - API调用频率限制参数

    输出:
    - self.frequency: 存储API调用频率参数
    - 继承父类的队列和锁，用于API调用任务管理
    """
    def __init__(self, frequency):
        # 输出：调用父类初始化方法
        super().__init__()
        # 输入：存储API调用频率参数
        self.frequency = frequency