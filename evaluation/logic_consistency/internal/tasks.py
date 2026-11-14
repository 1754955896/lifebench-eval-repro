def serialize(obj):
    # 1. 基础类型
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    # 2. list / tuple
    if isinstance(obj, (list, tuple)):
        return [serialize(x) for x in obj]

    # 3. dict
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}

    # 4. 自定义对象：优先 to_dict
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return serialize(obj.to_dict())

    # 5. 兜底：普通对象（不推荐，但防炸）
    if hasattr(obj, "__dict__"):
        return {
            k: serialize(v)
            for k, v in obj.__dict__.items()
            if not k.startswith("_")
        }

    raise TypeError(f"{type(obj)} is not JSON serializable")


class Task:
    """
    任务类，表示一个具体的处理任务

    输入:
    - date: str - 任务对应的日期
    - event: Any - 事件数据
    - persona: PersonaMessage - 人物画像数据

    输出:
    - 初始化任务的各种属性和状态字段
    """
    def __init__(self, date, event, persona):
        # 输入：存储任务的基本信息
        self.name = persona.origin_message["name"]
        self.date = date        # 任务日期
        self.event = event      # 原始事件数据
        self.persona = persona  # 人物画像数据

        # 输出：信息提取阶段的结果字段（初始化为空）
        self.events = None      # 提取出的事件列表
        self.locations = None   # 提取出的地点列表
        self.relations = None   # 提取出的人物关系列表
        self.paths = None       # 提取出的路径列表
        self.paths_error = 0 # 遇到错误的路径

        # 输出：分析阶段的详细结果字段
        self.res_for_events = None           # 事件分析结果
        self.res_for_locations = {}          # 地点分析结果（字典格式，键为地点名）
        self.res_for_relations = []          # 人物关系分析结果（列表格式）
        self.res_for_paths = []              # 路径分析结果（列表格式）

        # 输出：最终分析结论字段
        self.analyze_for_paths = None        # 路径分析结论
        self.analyze_for_events = []         # 事件分析结论（列表格式）

    def check_stage_1(self):
        """
        检查第一阶段（信息提取）是否完成

        输出:
        - bool: 如果所有信息提取字段都非空，返回True，否则返回False
        """
        # 输入检查：判断四个提取字段是否都已填充
        if None not in [self.events, self.locations, self.relations, self.paths]:
            return True  # 输出：所有提取完成
        else:
            return False  # 输出：仍有未完成的提取

    def to_dict(self):
        return {
            "name": self.name,
            "date": self.date,
            # "event": self.event,
            # "persona": self.persona.to_dict(),

            "events" : self.events,
            "locations" : self.locations,
            "relations" : self.relations,
            "paths" : self.paths,

            "res_for_events": serialize(self.res_for_events),
            "res_for_locations": serialize(self.res_for_locations),
            "res_for_relations": serialize(self.res_for_relations),
            "res_for_paths": serialize(self.res_for_paths),

            "analyze_for_paths": serialize(self.analyze_for_paths),
            "analyze_for_events": serialize(self.analyze_for_events)
        }

    def write_log(self, logger):
        logger.info(f"==================================================")
        logger.info(f"===================={self.date}====================")
        logger.info(f"==================================================")
        logger.info(f"====================人物一致性====================")
        for item in self.res_for_relations:
            logger.info(str(item))
        logger.info(f"====================事件一致性====================")
        for item in self.analyze_for_events:
            logger.info(str(item))
        logger.info(f"====================地点真实性====================")
        for k, v in self.res_for_locations.items():
            logger.info(str(v) )
        logger.info(f"====================行程查询====================")
        for item in self.res_for_paths:
            logger.info(str(item))
        logger.info(f"====================行程真实性====================")
        logger.info(self.analyze_for_paths)

class Result:
    """
    结果类，封装任务分析的具体结果

    输入:
    - info: Any - 原始信息数据（如API响应等）
    - prefix: str - 结果前缀标识符（如"<人物正确>", "<地点错误>"等）
    - text: str - 结果描述文本

    输出:
    - 创建标准化的结果对象，便于格式化和存储
    """
    def __init__(self, info, prefix, text):
        # 输入：存储结果的三部分信息
        self.info = info    # 原始数据（可后续使用）
        self.prefix = prefix  # 结果类型/状态标识
        self.text = text    # 人类可读的描述文本

    def __str__(self):
        """
        格式化结果对象为字符串

        输出:
        - str: 格式为"前缀 + 文本 + 换行符"的字符串表示
        """
        # 输出：组合所有信息为格式化字符串
        return self.prefix + self.text + "\n"

    def to_dict(self):
        return {
            "info": self.info,
            "prefix": self.prefix,
            "text": self.text
        }