from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from evaluation.logic_consistency.code.internal.tasks import Task
import re
import threading
import logging
from pyrate_limiter import Duration, Rate, Limiter


def get_limiter(frequency):
    """
    创建限流器

    输入:
    - frequency: int - 每秒允许的最大请求频率

    输出:
    - Limiter对象: 用于限制API调用频率
    """
    # 输入：设置限流规则（频率/秒）
    rate = Rate(frequency, Duration.SECOND)
    # 输出：创建限流器实例
    limiter = Limiter(rate)
    return limiter

def get_logger(args):
    """
    配置并获取logger对象

    输入:
    - args: 参数对象 - 包含 log_file 等配置

    输出:
    - logger: logging.Logger - 配置好的日志记录器
    """
    # 添加自定义日志级别DETAIL
    DETAIL_LEVEL_NUM = 15  # 输入：定义自定义日志级别数值
    logging.addLevelName(DETAIL_LEVEL_NUM, "DETAIL")  # 输出：注册新日志级别名称
    logging.DETAIL = DETAIL_LEVEL_NUM  # 输出：设置为模块属性

    # 定义detail日志方法
    def detail(self, message, *args, **kws):
        """输出detail级别日志的辅助方法"""
        if self.isEnabledFor(DETAIL_LEVEL_NUM):
            self._log(DETAIL_LEVEL_NUM, message, args, **kws)

    # 输出：将detail方法添加到Logger类
    logging.Logger.detail = detail

    # 关闭pyrate_limiter的日志输出
    logging.getLogger("pyrate_limiter").setLevel(logging.WARNING)

    # 输入：创建logger实例
    logger = logging.getLogger(__name__)

    # 输出：配置日志系统基础设置
    logging.basicConfig(
        filename=args.log_file,  # 输入：日志文件路径
        filemode='a',  # 输入：追加模式
        level=logging.DETAIL,  # 输入：日志级别
        format='%(asctime)s - %(levelname)s - %(message)s',  # 输入：日志格式
        datefmt='%Y-%m-%d %H:%M:%S',  # 输入：时间格式
        encoding="utf-8"  # 输入：文件编码
    )

    return logger

def another_day(date, days):
    """
    计算指定天数后的日期

    输入:
    - date: str - 起始日期，格式为"YYYY-MM-DD"
    - days: int - 要增加的天数

    输出:
    - str: 计算后的日期，格式为"YYYY-MM-DD"
    """
    # 输入处理：字符串转datetime对象
    # 输出：计算新日期并格式化为字符串
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")

def preprocess(args, eventM, personaM, extract_queue):
    """
    预处理函数，创建任务日历并加入队列

    输入:
    - args: 参数对象 - 包含起止日期
    - eventM: 事件管理器对象
    - personaM: 人物画像管理器对象
    - extract_queue: 任务队列

    输出:
    - dict: 日期到任务的映射字典
    """
    calendar = {}
    date = args.start_date

    # 循环处理日期范围内的每一天
    while date <= args.end_date:
        try:
            # 输出：为每个日期创建任务
            task = Task(date, eventM.simplied_message[date], personaM)
            # 输出：将任务添加到日历字典
            calendar[date] = task
            # 输出：将任务元组添加到处理队列
            extract_queue.add((task,))
        except KeyError:
            print(f"{date}不存在，已被跳过")
        # 输入处理：计算下一天
        date = another_day(date, 1)


    return calendar

# 创建线程锁用于同步日志输出
_log_lock = threading.Lock()  # 输出：全局线程锁对象

def process_single_item(k, item, events, deepseek, check_prompt):
    """
    处理单个任务的工作函数（无日志版本）

    输入:
    - k: str - 任务键标识
    - item: Any - 要处理的单个项目
    - events: list - 事件列表
    - deepseek: API客户端 - 用于发送请求
    - check_prompt: 提示模板对象

    输出:
    - dict: 局部处理结果字典
    """
    local_results = {}

    try:
        # 遍历所有事件进行处理
        for event in events:
            # 输入处理：生成提示词并调用API
            response = deepseek.ask(check_prompt.write_prompt(event["事件"], item))

            # 输出处理：从响应中提取结果
            result_match = re.search(r"<results>(.*?)</results>", response)
            result = result_match.group(1).split() if result_match else []

            # 处理每个结果
            for res in result:
                # 生成唯一键：事件+时间（去除最后2字符）
                key = event["事件"] + event["时间"][:-2]
                if key not in local_results:
                    # 输出：初始化该键的结果
                    local_results[key] = {k: res}
                else:
                    if k in local_results[key]:
                        # 输出：优先保留"Yes"结果
                        if local_results[key][k] == "No" and res == "Yes":
                            local_results[key][k] = res
                    else:
                        # 输出：添加新结果
                        local_results[key][k] = res
    except Exception as e:
        pass  # 静默处理错误，不输出额外日志

    return local_results

def merge_results(results_list):
    """
    合并所有线程的结果

    输入:
    - results_list: list[dict] - 多个局部结果字典的列表

    输出:
    - dict: 合并后的最终结果字典
    """
    final_results = {}

    # 遍历所有局部结果
    for local_results in results_list:
        for key, value in local_results.items():
            if key not in final_results:
                # 输出：初始化新键
                final_results[key] = value
            else:
                # 输出：合并处理相同键的结果
                for k, res in value.items():
                    if k in final_results[key]:
                        # 优先保留"Yes"结果
                        if final_results[key][k] == "No" and res == "Yes":
                            final_results[key][k] = res
                    else:
                        final_results[key][k] = res

    return final_results

def process_with_threading(questions, info, deepseek, check_prompt, max_workers=8):
    """
    使用多线程处理 - 不添加额外日志

    输入:
    - questions: dict - 问题字典，键为问题标识，值为问题内容
    - info: dict - 信息字典，包含待处理项目
    - deepseek: API客户端 - 用于发送请求
    - check_prompt: 提示模板对象
    - max_workers: int - 最大线程数（默认8）

    输出:
    - dict: 处理后的结果字典
    """
    # 准备所有任务
    tasks = []
    for k, v in questions.items():
        try:
            if k in info:
                for item in info[k]:
                    # 输出：创建任务元组（键，项目，事件列表）
                    tasks.append((k, item, v))
        except KeyError:
            pass  # 保持原有的静默处理

    # 使用线程池处理
    results_list = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务到线程池
        future_to_task = {
            executor.submit(process_single_item, k, item, events, deepseek, check_prompt): (k, item)
            for k, item, events in tasks
        }

        # 收集结果
        for future in as_completed(future_to_task):
            try:
                # 输出：获取单个任务的结果
                result = future.result()
                results_list.append(result)
            except Exception:
                pass  # 静默处理错误

    # 输出：合并所有结果
    results = merge_results(results_list)

    return results