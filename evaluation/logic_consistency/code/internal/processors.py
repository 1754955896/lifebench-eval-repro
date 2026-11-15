from queue import Queue, Empty
from pyrate_limiter import BucketFullException
from evaluation.logic_consistency.code.internal.messages import (
    PersonaMessage, EventMessage, PromptMessage, InfoMessage, FeedbackMessage,
)
from evaluation.logic_consistency.code.internal.queues import TaskQueueManager
from evaluation.logic_consistency.code.internal.tasks import Result
from evaluation.logic_consistency.code.internal.functions import (
    get_limiter, another_day, process_with_threading,
)
from tools import geo
import re
import json
import threading
import time


# 可在测试中替换为假地理对象
GEO_CLIENT = geo

# 队列空闲时的轮询间隔（源实现为 5 秒；缩短以加快离线测试与收尾）
DISPATCH_IDLE_SLEEP = 0.05


class BaseProcessor:
    """
    基础处理器基类

    输入:
    - processor_id: str - 处理器标识符

    输出:
    - self.processor_id: 存储处理器ID
    - self.task: 当前处理的任务
    - self.is_busy: 处理器忙碌状态标志
    - self.lock: 线程锁用于同步访问
    - self.thread: 处理线程
    """
    def __init__(self, processor_id):
        # 输入：处理器标识
        self.processor_id = processor_id
        # 输出：当前任务初始化为空
        self.task = None
        # 输出：初始状态为空闲
        self.is_busy = False
        # 输出：创建线程锁对象
        self.lock = threading.Lock()
        # 输出：处理线程初始化为空
        self.thread = None

    def assign_task(self, task, callback):
        """
        分配任务到处理器

        输入:
        - task: Any - 要处理的任务
        - callback: callable - 任务完成后的回调函数

        输出:
        - bool: 任务分配成功返回True，否则返回False
        """
        with self.lock:
            # 输入检查：如果处理器忙碌则拒绝任务
            if self.is_busy:
                return False

            # 输出：设置任务和忙碌状态
            self.task = task
            self.is_busy = True

            # 输出：创建并启动处理线程
            self.thread = threading.Thread(target=self.run_task, args=(callback,))
            self.thread.start()
            return True

class BaseProcessorCluster:
    """
    基础处理器集群基类

    输入:
    - num_processors: int - 处理器数量（子类实现）
    - task_queue: Queue - 任务输入队列
    - next_queue: Any - 下一阶段队列

    输出:
    - self.processors: 处理器列表（子类实现）
    - self.task_queue: 存储任务队列
    - self.next_queue: 存储下一阶段队列
    - self.running: 集群运行状态事件
    - self.dispatcher_thread: 任务分发线程
    """
    def __init__(self, num_processors, task_queue, next_queue):
        # 输出：处理器列表（由子类初始化）
        self.processors = None
        # 输入：存储任务队列
        self.task_queue = task_queue
        # 输入：存储下一阶段队列
        self.next_queue = next_queue

        # 输出：运行状态控制事件
        self.running = threading.Event()
        # 输出：任务分发线程
        self.dispatcher_thread = None

    def start(self):
        """
        启动处理器集群

        输出:
        - 设置运行标志并启动分发线程
        """
        # 输出：设置运行标志
        self.running.set()
        # 输出：创建并启动分发线程
        self.dispatcher_thread = threading.Thread(target=self.dispatch_loop)
        self.dispatcher_thread.start()

    def dispatch_loop(self):
        """
        任务分发循环（需子类实现）

        输出:
        - 持续从队列获取任务并分配给空闲处理器
        """
        while self.running.is_set():
            for p in self.processors:
                if not p.is_busy:
                    try:
                        # 输入：从队列获取任务
                        task = self.task_queue.get()
                        # 输出：分配任务给处理器
                        p.assign_task(task, self.mark_task_completed)
                    except Empty:
                        # 输出：队列为空时短暂休眠
                        time.sleep(DISPATCH_IDLE_SLEEP)
                        break

    def mark_task_completed(self, result):
        """
        任务完成标记回调

        输入:
        - result: Any - 任务处理结果

        输出:
        - 打印结果信息
        """
        print(result)

    def stop(self):
        """
        停止处理器集群

        输出:
        - 清除运行标志
        """
        self.running.clear()

    def all_processors_idle(self):
        """
        检查所有处理器是否空闲

        输出:
        - bool: 所有处理器空闲返回True，否则返回False
        """
        return all(not p.is_busy for p in self.processors)

class ExtractProcessor(BaseProcessor):
    """
    信息提取处理器类

    输入:
    - processor_id: str - 处理器标识符
    - extract_apis: dict - 提取API配置字典
    - args: argparse.Namespace - 命令行参数

    输出:
    - 类属性extract_apis: 共享的提取API配置
    - 类属性locations_prompt: 地点提取提示词
    - 类属性events_prompt: 事件提取提示词
    - 类属性people_prompt: 人物提取提示词
    """
    extract_apis = None
    locations_prompt = None
    events_prompt = None
    people_prompt = None

    def __init__(self, processor_id, extract_apis, args):
        super().__init__(processor_id)
        # 输出：初始化共享的提取API配置
        if ExtractProcessor.extract_apis is None:
            ExtractProcessor.extract_apis = extract_apis
        # 输出：加载地点提取提示词文件
        if ExtractProcessor.locations_prompt is None:
            with open(args.locations_extraction, "r", encoding="utf-8") as file:
                ExtractProcessor.locations_prompt = PromptMessage(file.read())
        # 输出：加载事件提取提示词文件
        if ExtractProcessor.events_prompt is None:
            with open(args.events_extraction, "r", encoding="utf-8") as file:
                ExtractProcessor.events_prompt = PromptMessage(file.read())
        # 输出：加载人物提取提示词文件
        if ExtractProcessor.people_prompt is None:
            with open(args.people_extraction, "r", encoding="utf-8") as file:
                ExtractProcessor.people_prompt = PromptMessage(file.read())

    def run_task(self, callback):
        """
        执行提取任务

        输入:
        - callback: callable - 任务完成回调函数

        输出:
        - 将格式化的提示词添加到相应队列
        """
        try:
            # 输出：组装地点提取提示词并加入队列
            ExtractProcessor.extract_apis["locations_queue"].add((self.task[0], ExtractProcessor.locations_prompt.write_prompt(self.task[0].persona.location_message, self.task[0].event)))
            # 输出：组装事件提取提示词并加入队列
            ExtractProcessor.extract_apis["events_queue"].add((self.task[0], ExtractProcessor.events_prompt.write_prompt(self.task[0].event)))
            # 输出：组装人物关系提取提示词并加入队列
            ExtractProcessor.extract_apis["relations_queue"].add((self.task[0], ExtractProcessor.people_prompt.write_prompt(self.task[0].event)))

            # 输出：生成完成消息
            result = f"ExtractProcessor [{self.processor_id}] 完成{self.task[0].date}的提取Prompt组装"
            callback(result)
        finally:
            with self.lock:
                # 输出：重置处理器状态
                self.is_busy = False
                self.task = None

class ExtractProcessorCluster(BaseProcessorCluster):
    """
    信息提取处理器集群

    输入:
    - num_processors: int - 处理器数量
    - queue: Queue - 任务队列
    - extract_apis: dict - 提取API配置
    - args: argparse.Namespace - 命令行参数

    输出:
    - 创建指定数量的ExtractProcessor实例
    """
    def __init__(self, num_processors, queue, extract_apis, args):
        super().__init__(num_processors, queue, extract_apis)
        # 输出：创建处理器实例列表
        self.processors = [ExtractProcessor(f"P{i}", self.next_queue, args) for i in range(num_processors)]

class APIExtractProcessor(BaseProcessor):
    """
    API提取处理器类（调用Deepseek API）

    输入:
    - processor_id: str - 处理器标识符
    - next_queue: Queue - 下一阶段队列
    - deepseek: Deepseek - Deepseek API客户端

    输出:
    - 类属性deepseek: 共享的Deepseek客户端
    - 类属性analyze_queue: 共享的分析队列
    """
    deepseek = None
    analyze_queue = None

    def __init__(self, processor_id, next_queue, deepseek):
        super().__init__(processor_id)
        # 输出：初始化共享的Deepseek客户端
        if APIExtractProcessor.deepseek is None:
            APIExtractProcessor.deepseek = deepseek
        # 输出：初始化共享的分析队列
        if APIExtractProcessor.analyze_queue is None:
            APIExtractProcessor.analyze_queue = next_queue

    def assign_task(self, task, num, callback):
        """
        分配任务（扩展基类方法，增加num参数）

        输入:
        - task: Any - 处理任务
        - num: int - 任务类型编号（0:地点, 1:事件, 2:关系）
        - callback: callable - 回调函数

        输出:
        - bool: 任务分配成功返回True，否则返回False
        """
        with self.lock:
            if self.is_busy:
                return False

            self.task = task
            self.is_busy = True

            self.thread = threading.Thread(target=self.run_task, args=(num, callback,))
            self.thread.start()
            return True

    def run_task(self, num, callback):
        """
        执行API提取任务

        输入:
        - num: int - 任务类型编号
        - callback: callable - 回调函数

        输出:
        - 根据任务类型调用API并提取不同信息
        - 任务完成后将结果添加到任务对象
        """
        try:
            # 输入：调用Deepseek API
            response = self.deepseek.ask(self.task[1])

            # 输出：根据任务类型处理响应
            if num % 3 == 0:
                # 输入处理：从响应中提取地点信息
                locations_match = re.search(r"<locations>(.*?)</locations>", response).group(1).strip()
                # 输出：解析地点-城市对
                locations_items = re.findall(r"\((.*?),\s*(.*?)\)", locations_match)
                # 输出：构建地点字典列表
                locations = [{"地点": loc, "城市": city} for loc, city in locations_items]

                # 输入处理：从响应中提取路径信息
                paths_text = re.search(r"<paths>(.*?)</paths>", response, re.S).group(1).strip()
                # 输出：解析路径三元组
                paths_items = re.findall(r"\((.*?),\s*(.*?),\s*(.*?)\)", paths_text)
                # 输出：构建路径字典列表
                paths = [{"from": p[0], "to": p[1], "mode": p[2]} for p in paths_items]

                # 输出：将结果存储到任务对象
                self.task[0].locations = locations
                self.task[0].paths = paths

                result = f"APIExtractProcessor [{self.processor_id}] 完成{self.task[0].date}的locations和paths的提取"
            elif num % 3 == 1:
                # 输入处理：从响应中提取事件信息
                event_text = re.search(r"<events>(.*?)</events>", response, re.S).group(1).strip()
                # 输出：解析事件-时间对
                event_items = re.findall(r"\((.*?),\s*(.*?)\)", event_text)
                # 输出：构建事件字典列表
                events = [{"事件": event, "时间": time} for event, time in event_items]

                # 输出：将结果存储到任务对象
                self.task[0].events = events

                result = f"APIExtractProcessor [{self.processor_id}] 完成{self.task[0].date}的events的提取"
            else:
                # 输入处理：从响应中提取人物名称
                names_match = re.search(r"<names>(.*?)</names>", response)
                # 输出：解析名称列表
                names = names_match.group(1).split() if names_match else []

                # 输出：将结果存储到任务对象
                self.task[0].relations = names

                result = f"APIExtractProcessor [{self.processor_id}] 完成{self.task[0].date}的relations的提取"

            # 输出：如果任务完成第一阶段检查，则加入分析队列
            if self.task[0].check_stage_1():
                APIExtractProcessor.analyze_queue.add((self.task[0],))

            callback(result)

        finally:
            with self.lock:
                # 输出：重置处理器状态
                self.is_busy = False
                self.task = None

class APIExtractProcessorCluster(BaseProcessorCluster):
    """
    API提取处理器集群

    输入:
    - num_processors: int - 处理器数量
    - task_queue: dict - 任务队列字典（三个子队列）
    - next_queue: Queue - 下一阶段队列
    - deepseek: Deepseek - Deepseek API客户端
    - args: argparse.Namespace - 命令行参数

    输出:
    - 创建指定数量的APIExtractProcessor实例
    """
    def __init__(self, num_processors, task_queue, next_queue, deepseek, args):
        super().__init__(num_processors, task_queue, next_queue)
        # 输入：拆解任务队列字典
        self.locations_queue = self.task_queue["locations_queue"]
        self.events_queue = self.task_queue["events_queue"]
        self.relations_queue = self.task_queue["relations_queue"]
        # 输出：创建处理器实例列表
        self.processors = [APIExtractProcessor(f"P{i}", next_queue, deepseek) for i in range(num_processors)]

    def dispatch_loop(self):
        """
        分发循环（轮询三个子队列）

        输出:
        - 按顺序从三个队列中轮流获取任务分配
        """
        count = 0
        while self.running.is_set():
            for p in self.processors:
                if not p.is_busy:
                    try:
                        # 输入：根据计数决定从哪个队列获取任务
                        if count % 3 == 0:
                            task = self.locations_queue.get()
                        elif count % 3 == 1:
                            task = self.events_queue.get()
                        elif count % 3 == 2:
                            task = self.relations_queue.get()
                        # 输出：分配任务（传递任务类型编号）
                        p.assign_task(task, count % 3, self.mark_task_completed)
                        count += 1
                    except Empty:
                        time.sleep(DISPATCH_IDLE_SLEEP)
                        break

class RelationAnalyzeProcessor(BaseProcessor):
    """
    人物关系分析处理器类

    输入:
    - processor_id: str - 处理器标识符

    输出:
    - 类属性relations: 共享的人物关系索引字典
    """
    relations = None

    def __init__(self, processor_id):
        super().__init__(processor_id)

    def _build_name_index(self, nested_list):
        """
        构建人物名称索引

        输入:
        - nested_list: list - 嵌套的人物信息列表

        输出:
        - dict: 名称到人物信息的映射字典
        """
        index = {}

        def flatten(lst):
            """扁平化嵌套列表"""
            for item in lst:
                if isinstance(item, list):
                    yield from flatten(item)
                elif isinstance(item, dict):
                    yield item

        # 输入处理：扁平化嵌套结构
        for d in flatten(nested_list):
            name = d.get("name")
            if name:
                # 输出：建立名称索引
                index[name] = d

        return index

    def _search(self, name):
        """
        搜索人物信息

        输入:
        - name: str - 人物名称

        输出:
        - dict/None: 找到的人物信息字典，未找到返回None
        - 同时将搜索结果添加到任务对象
        """
        try:
            # 输出：直接通过名称查找
            self.task[0].res_for_relations.append(Result(RelationAnalyzeProcessor.relations[name], "<人物正确>", f"{name}的个人信息是{RelationAnalyzeProcessor.relations[name]}"))
            return RelationAnalyzeProcessor.relations[name]
        except KeyError:
            # 输出：通过昵称或关系名查找
            for k, v in RelationAnalyzeProcessor.relations.items():
                if v["nickname"] == name or v["relation"] == name:
                    self.task[0].res_for_relations.append(Result(v, "<人物正确>", f"{name}的个人信息是{v}"))
                    return v

            # 输出：未找到人物的错误信息
            self.task[0].res_for_relations.append(Result(None, "<人物错误>", f"{name}人物无法找到，请检查"))

    def run_task(self, callback):
        """
        执行人物关系分析任务

        输入:
        - callback: callable - 回调函数

        输出:
        - 为任务对象中的每个关系名称搜索人物信息
        """
        try:
            # 输出：构建人物关系索引（如果未初始化）
            if RelationAnalyzeProcessor.relations is None:
                RelationAnalyzeProcessor.relations = self._build_name_index(self.task[0].persona.relation)
            # 输入处理：遍历所有关系名称进行搜索
            for name in self.task[0].relations:
                self._search(name)

            result = f"RelationAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的relations查询"
            callback(result)
        finally:
            with self.lock:
                # 输出：重置处理器状态
                self.is_busy = False
                self.task = None

class LocationAnalyzeProcessor(BaseProcessor):
    """
    地点分析处理器类（准备高德API请求）

    输入:
    - processor_id: str - 处理器标识符
    - next_queue: Queue - 下一阶段队列

    输出:
    - 类属性gaode_api: 共享的高德API队列
    - 类属性key: 高德API密钥
    - 类属性locations_url: 高德地理编码API模板
    """
    gaode_api = None

    def __init__(self, processor_id, next_queue):
        super().__init__(processor_id)
        # 输出：初始化共享的高德API队列
        if LocationAnalyzeProcessor.gaode_api is None:
            LocationAnalyzeProcessor.gaode_api = next_queue
        # 输出：设置本地API队列引用
        self.locations_api = LocationAnalyzeProcessor.gaode_api

    def run_task(self, call_back):
        """
        执行地点分析任务

        输入:
        - call_back: callable - 回调函数

        输出:
        - 为每个地点构建API请求URL并加入队列
        """
        try:
            # 输入处理：遍历所有地点
            for location in self.task[0].locations:
                # 输出：初始化地点结果字典
                self.task[0].res_for_locations[location["地点"]] = None
                # 输出：构建API请求URL并加入队列
                self.locations_api.add((self.task[0],
                                        ("geocode", location["地点"],
                                         location["城市"]),
                                        location["地点"]))

            result = f"LocationAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的locations的API组装"
            call_back(result)
        finally:
            with self.lock:
                # 输出：重置处理器状态
                self.is_busy = False
                self.task = None

class EventAnalyzeProcess(BaseProcessor):
    """
    事件分析处理器类

    输入:
    - processor_id: str - 处理器标识符
    - next_queue: Queue - 下一阶段队列
    - deepseek: Deepseek - Deepseek API客户端
    - calendar: dict - 日历数据
    - args: argparse.Namespace - 命令行参数

    输出:
    - 类属性calendar: 共享的日历数据
    - 类属性events_api: 共享的事件API队列
    - 类属性deepseek: 共享的Deepseek客户端
    - 类属性check_prompt: 共享的事件检查提示词
    """
    deepseek = None
    calendar = None
    events_api = None
    check_prompt = None

    def __init__(self, processor_id, next_queue, deepseek, calendar, args):
        super().__init__(processor_id)
        # 输出：初始化共享的日历数据
        if EventAnalyzeProcess.calendar is None:
            EventAnalyzeProcess.calendar = calendar
        # 输出：初始化共享的事件API队列
        if EventAnalyzeProcess.events_api is None:
            EventAnalyzeProcess.events_api = next_queue
        # 输出：初始化共享的Deepseek客户端
        if EventAnalyzeProcess.deepseek is None:
            EventAnalyzeProcess.deepseek = deepseek
        # 输出：加载事件检查提示词文件
        if EventAnalyzeProcess.check_prompt is None:
            with open(args.event_check, "r", encoding="utf-8") as file:
                EventAnalyzeProcess.check_prompt = PromptMessage(file.read())

    def run_task(self, callback):
        """
        执行事件分析任务

        输入:
        - callback: callable - 回调函数

        输出:
        - 构建时间窗口问题集，调用多线程处理，分析事件时间安排
        """
        try:
            questions = {}
            info = {}
            # 输入处理：遍历所有事件
            for i, event in enumerate(self.task[0].events):
                # 输入处理：构建前后2天的时间窗口
                for day in range(-2, 3):
                    try:
                        # 输出：计算目标日期
                        goal_day = another_day(event["时间"][:-2], day)
                        # 输出：按日期分组事件问题
                        if goal_day not in questions.keys():
                            questions[goal_day] = [event]
                        else:
                            questions[goal_day].append(event)
                        try:
                            # 输入：获取日历中的事件
                            res = self.calendar[event['时间'][:-2]].event
                        except KeyError:
                            # 输出：日期不在范围内的错误信息
                            print(f"<提取时间错误>{event['时间'][:-2]}不在本次范围内")
                        # 输出：按日期组织事件信息
                        if goal_day not in info.keys():
                            if res is not None:
                                info[goal_day] = res
                            else:
                                del questions[goal_day]
                    except UnboundLocalError as e:
                        # 输出：日期解析错误
                        print(f"<提取时间错误>因为出现错误{e}，所以{event['时间']}被跳过")

            # 输出：使用多线程处理事件验证
            results = process_with_threading(
                questions,
                info,
                EventAnalyzeProcess.deepseek,
                EventAnalyzeProcess.check_prompt,
                max_workers=10
                )

            # 输出：存储验证结果
            self.task[0].res_for_events = results

            # 输出：分析验证结果，判断事件安排正确性
            for event, dct in results.items():
                ans = []
                for day, res in dct.items():
                    if res == "Yes":
                        ans.append(day)
                # 输出：根据结果生成分析结论
                if event[-10:] in ans:
                    self.task[0].analyze_for_events.append(Result(None, "<事件安排正确>", f"{event[:-10]}确实在预测时间{event[-10:]}发生"))
                else:
                    if len(ans) != 0:
                        self.task[0].analyze_for_events.append(Result(None, "<事件安排疑似错误>", f"{event[:-10]}在预测时间{event[-10:]}未发生发生，在{ans}均有发生"))
                    else:
                        self.task[0].analyze_for_events.append(Result(None, "<事件安排错误>", f"{event[:-10]}在预测时间{event[-10:]}附近均未发生"))
            # 注释掉的旧代码（备用实现）
            # for k, v in questions.items():
            #     for item in info[k]:
            #         for one in v:
            #             EventAnalyzeProcess.events_api.add((self.task[0], (EventAnalyzeProcess.check_prompt.write_prompt(one["事件"], item)), k, v))

            result = f"EventAnalyzeProcess [{self.processor_id}] 完成{self.task[0].date}的events API组装"
            callback(result)
        finally:
            with self.lock:
                # 输出：重置处理器状态
                self.is_busy = False
                self.task = None

class AnalyzeProcessorCluster(BaseProcessorCluster):
    """
    分析处理器集群（整合关系、地点、事件分析）

    输入:
    - num_processors: int - 每类处理器的数量
    - task_queue: Queue - 任务队列
    - next_queue: dict - 下一阶段队列字典
    - deepseek: Deepseek - Deepseek API客户端
    - calendar: dict - 日历数据
    - args: argparse.Namespace - 命令行参数

    输出:
    - 创建三类分析处理器实例
    """
    def __init__(self, num_processors, task_queue, next_queue, deepseek, calendar, args):
        super().__init__(num_processors, task_queue, next_queue)
        # 输出：创建人物关系分析处理器
        self.relations_processors = [RelationAnalyzeProcessor(f"P{i}") for i in range(num_processors)]
        # 输出：创建地点分析处理器
        self.locations_processors = [LocationAnalyzeProcessor(f"P{i}", self.next_queue["locations"]) for i in range(num_processors)]
        # 输出：创建事件分析处理器
        self.events_processors = [EventAnalyzeProcess(f"P{i}", self.next_queue["events"], deepseek, calendar, args) for i in range(num_processors)]

    def _get_task(self, processors, pending_queue):
        """
        从等待队列获取任务分配给处理器

        输入:
        - processors: list - 处理器列表
        - pending_queue: Queue - 等待队列

        输出:
        - 将等待队列中的任务分配给空闲处理器
        """
        for p in processors:
            if not p.is_busy:
                try:
                    # 输入：从等待队列获取任务
                    p.assign_task(pending_queue.get(), self.mark_task_completed)
                    break
                except Empty:
                    break

    def dispatch_loop(self):
        """
        分发循环（创建三个等待队列分别处理）

        输出:
        - 将任务复制到三个等待队列，分别分配给三类处理器
        """
        # 输出：为每类处理器创建独立的等待队列
        pending_queue_1 = TaskQueueManager()
        pending_queue_2 = TaskQueueManager()
        pending_queue_3 = TaskQueueManager()

        while self.running.is_set():
            try:
                # 输入：从主队列获取任务
                task = self.task_queue.get()
                # 输出：将任务复制到三个等待队列
                pending_queue_1.add(task)
                pending_queue_2.add(task)
                pending_queue_3.add(task)
            except Empty:
                pass

            # 输出：分别从三个等待队列分配任务给三类处理器
            self._get_task(self.relations_processors, pending_queue_1)
            self._get_task(self.locations_processors, pending_queue_2)
            self._get_task(self.events_processors, pending_queue_3)

    def all_processors_idle(self):
        """
        检查所有处理器是否空闲

        输出:
        - bool: 三类处理器全部空闲返回True，否则返回False
        """
        return all(not p.is_busy for p in self.relations_processors) and all(not p.is_busy for p in self.locations_processors) and all(not p.is_busy for p in self.events_processors)

class APILocationAnalyzeProcessor(BaseProcessor):
    """
    高德API地点分析处理器类

    输入:
    - processor_id: str - 处理器标识符
    - next_queue: Queue - 下一阶段队列
    - limiter: Limiter - 限流器

    输出:
    - 类属性gaode_mid_stage: 共享的中间阶段队列
    - 类属性limiter: 共享的API限流器
    """
    gaode_mid_stage = None
    limiter = None
    limiter_lock = threading.Lock()

    def __init__(self, processor_id, next_queue, limiter):
        super().__init__(processor_id)
        # 输出：初始化共享的中间阶段队列
        if APILocationAnalyzeProcessor.gaode_mid_stage is None:
            APILocationAnalyzeProcessor.gaode_mid_stage = next_queue
        # 输出：初始化共享的API限流器
        if APILocationAnalyzeProcessor.limiter is None:
            APILocationAnalyzeProcessor.limiter = limiter

    def _completed(self):
        """
        检查地点查询是否完成

        输出:
        - bool: 所有地点查询完成返回True，否则返回False
        """
        return all(v is not None for v in self.task[0].res_for_locations.values())

    def run_task(self, callback):
        """
        执行高德API地点查询任务

        输入:
        - callback: callable - 回调函数

        输出:
        - 调用高德地理编码API查询地点坐标
        - 处理API限流和响应
        """
        try:
            # 输出：使用限流器控制API调用频率
            while True:
                try:
                    # 输入：尝试获取API调用许可
                    with APILocationAnalyzeProcessor.limiter_lock:
                        APILocationAnalyzeProcessor.limiter.try_acquire("locations api")
                    # 输出：发送HTTP请求
                    response = GEO_CLIENT.geocode(*self.task[1][1:])
                    break
                except BucketFullException as e:
                    # 输出：限流时等待剩余时间
                    remaining = e.meta_info.get("remaining_time", 0.34)
                    time.sleep(remaining + 0.01)
                except Exception as e:
                    response = {"status": "0", "error": str(e)}
                    break

            # 输出：处理API响应
            if response["status"] != "1":
                # 输出：API调用失败的处理
                self.task[0].res_for_locations[self.task[2]] = Result(response, "<地点错误>", f"{self.task[2]}可能不存在，报错信息为{response}")
            else:
                # 输出：API调用成功的处理
                if response["count"] > "1":
                    # 输出：多个候选地点的处理
                    self.task[0].res_for_locations[self.task[2]] = Result(response, "<地点正确但多个候选>", f"{self.task[2]}的地点信息是{response}")
                else:
                    # 输出：单个地点的处理
                    self.task[0].res_for_locations[self.task[2]] = Result(response, "<地点正确>", f"{self.task[2]}的地点信息是{response}")

            # 输出：检查是否完成所有地点查询
            if self._completed():
                # 输出：完成后加入中间阶段队列
                APILocationAnalyzeProcessor.gaode_mid_stage.add((self.task[0],))
                result = f"APILocationAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的最后一个locations请求"
            else:
                result = f"APILocationAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的一个locations请求"
            callback(result)
        finally:
            with self.lock:
                # 输出：重置处理器状态
                self.is_busy = False
                self.task = None

class APILocationAnalyzeProcessorCluster(BaseProcessorCluster):
    """
    高德API地点分析处理器集群

    输入:
    - num_processors: int - 处理器数量
    - task_queue: TaskQueueManager - 任务队列（带频率限制）
    - next_queue: Queue - 下一阶段队列

    输出:
    - 创建指定数量的APILocationAnalyzeProcessor实例
    """
    def __init__(self, num_processors, task_queue, next_queue):
        super().__init__(num_processors, task_queue, next_queue)
        # 输出：创建处理器实例列表，从任务队列获取频率配置
        self.processors = [APILocationAnalyzeProcessor(f"P{i}", self.next_queue, get_limiter(self.task_queue.frequency)) for i in range(num_processors)]

    def dispatch_loop(self):
        """
        分发循环

        输出:
        - 从任务队列获取任务分配给空闲处理器
        """
        while self.running.is_set():
            for p in self.processors:
                if not p.is_busy:
                    try:
                        # 输入：从队列获取任务
                        task = self.task_queue.get()
                        # 输出：分配任务给处理器
                        p.assign_task(task, self.mark_task_completed)
                        break
                    except Empty:
                        # 输出：队列为空时短暂休眠
                        time.sleep(DISPATCH_IDLE_SLEEP)
                        break

class PathAnalyzeProcessor(BaseProcessor):
    """
    路径分析处理器类（准备高德路径API请求）

    输入:
    - processor_id: str - 处理器标识符
    - next_queue: Queue - 下一阶段队列

    输出:
    - 类属性gaode_api: 共享的高德API队列
    - 类属性key: 高德API密钥
    - 类属性walking_url: 步行路径API模板
    - 类属性transit_url: 公共交通路径API模板
    - 类属性driving_url: 驾车路径API模板
    - 类属性bicyling_url: 骑行路径API模板
    """
    gaode_api = None

    def __init__(self, processor_id, next_queue):
        super().__init__(processor_id)
        # 输出：初始化共享的高德API队列
        if PathAnalyzeProcessor.gaode_api is None:
            PathAnalyzeProcessor.gaode_api = next_queue
        # 输出：设置本地API队列引用
        self.paths_api = PathAnalyzeProcessor.gaode_api

    def _get_location(self, location):
        """
        获取地点坐标

        输入:
        - location: str - 地点名称

        输出:
        - str: 地点坐标字符串（经纬度）

        异常:
        - KeyError: 地点查询失败时抛出
        """
        # 输入：检查地点查询结果状态
        if self.task[0].res_for_locations[location].info["status"] == "1":
            # 输出：返回坐标字符串
            return self.task[0].res_for_locations[location].info["geocodes"][0]["location"]
        else:
            # 输出：查询失败抛出异常
            raise KeyError(f"{location}的status不为1,错误为{self.task[0].res_for_locations[location].info}")

    def run_task(self, callback):
        """
        执行路径分析任务

        输入:
        - callback: callable - 回调函数

        输出:
        - 为每条路径构建相应API请求URL并加入队列
        """
        try:
            # 输入处理：遍历所有路径
            for path in self.task[0].paths:
                try:
                    # 输出：根据出行方式选择API模板
                    request_spec = None
                    if path["mode"] in ["步行", "徒步", "公共交通", "地铁",
                                        "公交", "火车", "飞机", "轮船", "高铁",
                                        "自驾车", "驾车", "自驾", "网约车",
                                        "骑车", "自行车", "电动车"]:
                        request_spec = ("route", path["mode"],
                                        self._get_location(path["from"]),
                                        self._get_location(path["to"]))
                    else:
                        # 输出：不支持出行方式的警告
                        print(f"意料之外的出行方式{path['mode']}")
                    # 输出：将请求描述加入队列
                    self.paths_api.add((self.task[0], request_spec, path))
                except KeyError as e:
                    # 输出：地点坐标获取失败的错误
                    self.task[0].paths_error -= 1
                    print(f"PathAnalyzeProcessor [{self.processor_id}] 意外的地名错误{e}")

            result = f"PathAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的paths的API组装"
            callback(result)
        finally:
            with self.lock:
                # 输出：重置处理器状态
                self.is_busy = False
                self.task = None

class PathAnalyzeProcessorCluster(BaseProcessorCluster):
    """
    路径分析处理器集群

    输入:
    - num_processors: int - 处理器数量
    - task_queue: Queue - 任务队列
    - next_queue: Queue - 下一阶段队列

    输出:
    - 创建指定数量的PathAnalyzeProcessor实例
    """
    def __init__(self, num_processors, task_queue, next_queue):
        super().__init__(num_processors, task_queue, next_queue)
        # 输出：创建处理器实例列表
        self.processors = [PathAnalyzeProcessor(f"P{i}", self.next_queue) for i in range(num_processors)]

class APIPathAnalyzeProcessor(BaseProcessor):
    """
    高德API路径分析处理器类

    输入:
    - processor_id: str - 处理器标识符
    - next_queue: Queue - 下一阶段队列
    - limiter: Limiter - 限流器

    输出:
    - 类属性paths_analyze_queue: 共享的路径分析队列
    - 类属性limiter: 共享的API限流器
    """
    paths_analyze_queue = None
    limiter = None
    limiter_lock = threading.Lock()

    def __init__(self, processor_id, next_queue, limiter):
        super().__init__(processor_id)
        # 输出：初始化共享的路径分析队列
        if APIPathAnalyzeProcessor.paths_analyze_queue is None:
            APIPathAnalyzeProcessor.paths_analyze_queue = next_queue
        # 输出：初始化共享的API限流器
        if APIPathAnalyzeProcessor.limiter is None:
            APIPathAnalyzeProcessor.limiter = limiter

    def _complete(self):
        return len(self.task[0].res_for_paths) == len(self.task[0].paths) + self.task[0].paths_error

    def run_task(self, callback):
        """
        执行高德API路径查询任务

        输入:
        - callback: callable - 回调函数

        输出:
        - 调用高德路径API查询距离和时间
        - 处理API限流和响应解析
        """
        try:
            # 输出：使用限流器控制API调用频率
            while True:
                try:
                    # 输入：尝试获取API调用许可
                    with APIPathAnalyzeProcessor.limiter_lock:
                        APIPathAnalyzeProcessor.limiter.try_acquire("paths api")
                    # 输出：发送HTTP请求
                    if self.task[1] is not None:
                        response = GEO_CLIENT.route(*self.task[1][1:])
                    else:
                        response = {}
                    break
                except BucketFullException as e:
                    # 输出：限流时等待剩余时间
                    remaining = e.meta_info.get("remaining_time", 0.2)
                    time.sleep(remaining)
                except Exception as e:
                    response = {"error": str(e)}
                    break
            # 输出：解析API响应中的距离和时间
            try:
                try:
                    # 尝试步行/驾车/骑行API格式
                    distance = response['route']['paths'][0]['distance']
                    duration = response['route']['paths'][0]['duration']
                except KeyError:
                    # 尝试公共交通API格式
                    distance = response['route']['transits'][0]['distance']
                    duration = response['route']['transits'][0]['duration']
            except Exception:
                # 输出：API响应格式错误的警告
                print(f"路径查询错误，内容为{response}")
                self.task[0].res_for_paths.append(Result(None, "<路径时间查询错误>", f"路径查询错误，内容为{response}"))
            else:
                # 输出：构建路径查询结果
                self.task[0].res_for_paths.append(Result(None, "<路径时间参考>", f"{self.task[2]['mode']}从{self.task[2]['from']}到{self.task[2]['to']}的参考距离为{distance}米，此时此刻出行参考时间为{duration}秒"))
            # 输出：将任务加入下一阶段队列
            if self._complete():
                APIPathAnalyzeProcessor.paths_analyze_queue.add((self.task[0],))
                result = f"APIPathAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的最后一次paths的查询"
            else:
                result = f"APIPathAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的paths的查询"
            callback(result)
        finally:
            with self.lock:
                # 输出：重置处理器状态
                self.is_busy = False
                self.task = None

class APIPathAnalyzeProcessorCluster(BaseProcessorCluster):
    """
    高德API路径分析处理器集群

    输入:
    - num_processors: int - 处理器数量
    - task_queue: TaskQueueManager - 任务队列（带频率限制）
    - next_queue: Queue - 下一阶段队列

    输出:
    - 创建指定数量的APIPathAnalyzeProcessor实例
    """
    def __init__(self, num_processors, task_queue, next_queue):
        super().__init__(num_processors, task_queue, next_queue)
        # 输出：创建处理器实例列表，从任务队列获取频率配置
        self.processors = [APIPathAnalyzeProcessor(f"P{i}", self.next_queue, get_limiter(self.task_queue.frequency)) for i in range(num_processors)]

    def dispatch_loop(self):
        """
        分发循环

        输出:
        - 从任务队列获取任务分配给空闲处理器
        """
        while self.running.is_set():
            for p in self.processors:
                if not p.is_busy:
                    try:
                        # 输入：从队列获取任务
                        task = self.task_queue.get()
                        # 输出：分配任务给处理器
                        p.assign_task(task, self.mark_task_completed)
                        break
                    except Empty:
                        # 输出：队列为空时短暂休眠
                        time.sleep(DISPATCH_IDLE_SLEEP)
                        break

class DeepseekPathAnalyzeProcess(BaseProcessor):
    """
    Deepseek路径分析处理器类（准备Deepseek路径验证请求）

    输入:
    - processor_id: str - 处理器标识符
    - next_queue: Queue - 下一阶段队列
    - args: argparse.Namespace - 命令行参数

    输出:
    - 类属性deepseek_path_api: 共享的Deepseek API队列
    - 类属性paths_prompt: 共享的路径验证提示词
    """
    deepseek_path_api = None
    paths_prompt = None

    def __init__(self, processor_id, next_queue, args):
        super().__init__(processor_id)
        # 输出：初始化共享的Deepseek API队列
        if DeepseekPathAnalyzeProcess.deepseek_path_api is None:
            DeepseekPathAnalyzeProcess.deepseek_path_api = next_queue
        # 输出：加载路径验证提示词文件
        if DeepseekPathAnalyzeProcess.paths_prompt is None:
            with open(args.verify_1, "r", encoding="utf-8") as file:
                DeepseekPathAnalyzeProcess.paths_prompt = PromptMessage(file.read())

    def run_task(self, callback):
        """
        执行Deepseek路径分析任务

        输入:
        - callback: callable - 回调函数

        输出:
        - 组装路径验证提示词并加入Deepseek API队列
        """
        try:
            # 输出：将路径查询结果合并为字符串
            paths_info = "\n".join([item.text for item in self.task[0].res_for_paths])
            # 输出：构建路径验证提示词并加入队列
            DeepseekPathAnalyzeProcess.deepseek_path_api.add((self.task[0], DeepseekPathAnalyzeProcess.paths_prompt.write_prompt(self.task[0].paths, self.task[0].persona.location_message, paths_info, self.task[0].event)))

            result = f"DeepseekPathAnalyzeProcess [{self.processor_id}] 完成{self.task[0].date}的deepseek paths的API组装"
            callback(result)
        finally:
            with self.lock:
                # 输出：重置处理器状态
                self.is_busy = False
                self.task = None

class DeepseekPathAnalyzeProcessCluster(BaseProcessorCluster):
    """
    Deepseek路径分析处理器集群

    输入:
    - num_processors: int - 处理器数量
    - task_queue: Queue - 任务队列
    - next_queue: Queue - 下一阶段队列
    - args: argparse.Namespace - 命令行参数

    输出:
    - 创建指定数量的DeepseekPathAnalyzeProcess实例
    """
    def __init__(self, num_processors, task_queue, next_queue, args):
        super().__init__(num_processors, task_queue, next_queue)
        # 输出：创建处理器实例列表
        self.processors = [DeepseekPathAnalyzeProcess(f"P{i}", self.next_queue, args) for i in range(num_processors)]

class APIDeepseekPathAnalyzeProcess(BaseProcessor):
    """
    Deepseek API路径分析处理器类

    输入:
    - processor_id: str - 处理器标识符
    - deepseek: Deepseek - Deepseek API客户端

    输出:
    - 类属性deepseek: 共享的Deepseek客户端
    """
    deepseek = None

    def __init__(self, processor_id, deepseek):
        super().__init__(processor_id)
        # 输出：初始化共享的Deepseek客户端
        if APIDeepseekPathAnalyzeProcess.deepseek is None:
            APIDeepseekPathAnalyzeProcess.deepseek = deepseek

    def run_task(self, callback):
        """
        执行Deepseek API路径验证任务

        输入:
        - callback: callable - 回调函数

        输出:
        - 调用Deepseek API验证路径合理性
        """
        try:
            # 输入：调用Deepseek API
            response = APIDeepseekPathAnalyzeProcess.deepseek.ask(self.task[1])
            # 输出：存储路径分析结果
            self.task[0].analyze_for_paths = response

            result = f"APIDeepseekPathAnalyzeProcess [{self.processor_id}] 完成{self.task[0].date}的deepseek paths的询问"
            callback(result)
        finally:
            with self.lock:
                # 输出：重置处理器状态
                self.is_busy = False
                self.task = None

class APIDeepseekPathAnalyzeProcessCluster(BaseProcessorCluster):
    """
    Deepseek API路径分析处理器集群

    输入:
    - num_processors: int - 处理器数量
    - task_queue: Queue - 任务队列
    - next_queue: Queue - 下一阶段队列
    - deepseek: Deepseek - Deepseek API客户端

    输出:
    - 创建指定数量的APIDeepseekPathAnalyzeProcess实例
    """
    def __init__(self, num_processors, task_queue, next_queue, deepseek):
        super().__init__(num_processors, task_queue, next_queue)
        # 输出：创建处理器实例列表
        self.processors = [APIDeepseekPathAnalyzeProcess(f"P{i}", deepseek) for i in range(num_processors)]

class APIEventAnalyzeProcess(BaseProcessor):
    """
    API事件分析处理器类（使用Deepseek进行事件验证）

    输入:
    - processor_id: str - 处理器标识符
    - deepseek: Deepseek - Deepseek API客户端

    输出:
    - 类属性deepseek: 共享的Deepseek客户端
    """
    deepseek = None
    def __init__(self, processor_id, deepseek):
        super().__init__(processor_id)
        # 输出：初始化共享的Deepseek客户端
        if APIEventAnalyzeProcess.deepseek is None:
            APIEventAnalyzeProcess.deepseek = deepseek

    def run_task(self, callback):
        """
        执行API事件分析任务

        输入:
        - callback: callable - 回调函数

        输出:
        - 调用Deepseek API进行事件时间验证
        - 分析并存储验证结果
        """
        try:
            # 输入：调用Deepseek API进行事件验证
            response = APIEventAnalyzeProcess.deepseek.ask(self.task[1])

            # 输出：从响应中提取验证结果
            result_match = re.search(r"<results>(.*?)</results>", response)
            result = result_match.group(1).split() if result_match else []

            # 输出：构建事件验证结果字典
            results = {}
            for res, event in zip(result, self.task[3]):
                event_key = event["事件"] + event["时间"][:-2]
                if event_key not in results.keys():
                    results[event_key] = {self.task[2]: res}
                else:
                    if self.task[2] in results[event_key].keys():
                        # 输出：优先保留"Yes"结果
                        if results[event_key][self.task[2]] == "No" and res == "Yes":
                            results[event_key][self.task[2]] = res
                    else:
                        results[event_key][self.task[2]] = res

            # 输出：存储验证结果
            self.task[0].res_for_events = results

            # 输出：分析验证结果，生成结论
            for event, dct in results.items():
                ans = []
                for day, res in dct.items():
                    if res == "Yes":
                        ans.append(day)
                # 输出：根据验证结果判断事件安排正确性
                if event[-10:] in ans:
                    self.task[0].analyze_for_events.append(f"<事件安排正确>{event[:-10]}确实在预测时间{event[-10:]}发生")
                else:
                    if len(ans) != 0:
                        self.task[0].analyze_for_events.append(f"<事件安排疑似错误>{event[:-10]}在预测时间{event[-10:]}未发生发生，在{ans}均有发生")
                    else:
                        self.task[0].analyze_for_events.append(f"<事件安排错误>{event[:-10]}在预测时间{event[-10:]}附近均未发生")

            result = f"APIEventAnalyzeProcess [{self.processor_id}] 完成{self.task[0].date}的Event API的询问"
            callback(result)
        finally:
            with self.lock:
                # 输出：重置处理器状态
                self.is_busy = False
                self.task = None

class APIEventAnalyzeProcessCluster(BaseProcessorCluster):
    """
    API事件分析处理器集群

    输入:
    - num_processors: int - 处理器数量
    - task_queue: Queue - 任务队列
    - next_queue: Queue - 下一阶段队列
    - deepseek: Deepseek - Deepseek API客户端

    输出:
    - 创建指定数量的APIEventAnalyzeProcess实例
    """
    def __init__(self, num_processors, task_queue, next_queue, deepseek):
        super().__init__(num_processors, task_queue, next_queue)
        # 输出：创建处理器实例列表
        self.processors = [APIEventAnalyzeProcess(f"P{i}", deepseek) for i in range(num_processors)]