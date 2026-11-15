class BaseMessage:
    """
    消息基类

    输入:
    - origin_message: Any - 原始消息数据

    输出:
    - self.origin_message: 存储原始消息数据
    """
    def __init__(self, origin_message):
        # 输入：存储原始消息数据
        self.origin_message = origin_message


class PersonaMessage(BaseMessage):
    """
    人物画像消息类

    输入:
    - origin_message: dict - 原始人物画像数据，包含姓名、地址、关系等信息

    输出:
    - self.location_message: 位置信息字典
    - self.relation: 人物关系信息
    - self.background_message: 背景信息字典
    """
    def __init__(self, origin_message):
        super().__init__(origin_message)
        # 输出：提取位置信息
        self.location_message = self._location_extract()
        # 输出：存储关系信息
        self.relation = self.origin_message["relation"]
        # 输出：提取背景信息
        self.background_message = self._background_extract()

    def _location_extract(self):
        """
        从原始数据中提取位置信息

        输出:
        - dict: 包含姓名、城市、家庭地址、工作地点的位置信息字典
        """
        return {
            "姓名": self.origin_message["name"],  # 输入：姓名
            "城市": self.origin_message["home_address"]["city"],  # 输入：城市
            "家": self.origin_message["home_address"]["street_name"] + self.origin_message["home_address"]["street_number"],  # 输入：家庭详细地址
            "工作地点": self.origin_message["workplace"]["street_name"] + self.origin_message["workplace"]["street_number"]  # 输入：工作详细地址
        }

    def _background_extract(self):
        """
        提取背景信息（排除位置和关系信息）

        输出:
        - dict: 背景信息字典，包含除出生日期、地址、关系外的所有字段
        """
        dit = {}
        for k, v in self.origin_message.items():
            # 输入过滤：排除特定字段
            if k not in ["birth", "home_address", "workplace", "relation"]:
                dit[k] = v  # 输出：添加背景信息
        return dit

    def to_dict(self):
        return {
            "origin_message": self.origin_message,
            "location_message": self.location_message,
            "relation": self.relation,
            "background_message": self.background_message
        }

class EventMessage(BaseMessage):
    """
    事件消息类

    输入:
    - origin_message: dict - 原始事件数据，包含日期信息

    输出:
    - self.simplied_message: 简化后的事件消息字典
    """
    def __init__(self, origin_message):
        super().__init__(origin_message)
        # 输出：简化事件数据格式
        self.simplied_message = self._simplify()

    def _simplify(self):
        """
        简化事件数据格式

        输入:
        - self.origin_message["date"]: list - 包含日期事件条目的列表

        输出:
        - dict: 扁平化的事件字典，键为日期，值为对应事件信息
        """
        return {
            k: v
            for item in self.origin_message["date"]  # 输入：遍历日期条目
            for k, v in item.items()  # 输入：展开每个条目的键值对
        }


class InfoMessage(BaseMessage):
    """
    信息消息类（用于累积额外信息）

    输入:
    - origin_message: str - 原始信息字符串

    输出:
    - self.info_message: str - 累积的信息字符串（初始为空）
    """
    def __init__(self, origin_message):
        super().__init__(origin_message)
        # 输出：初始化信息字符串
        self.info_message = ""

    def add_extra_info(self, info):
        """
        添加额外信息

        输入:
        - info: Any - 要添加的信息（会自动转换为字符串）

        输出:
        - self.info_message: 更新后的信息字符串
        """
        # 输入：将info转换为字符串并追加
        self.info_message += "\n" + str(info)  # 输出：更新信息字符串


class FeedbackMessage(BaseMessage):
    """
    反馈消息类（包含警告信息）

    输入:
    - origin_message: str - 原始反馈信息

    输出:
    - self.warning_message: str - 累积的警告信息
    - 可通过merge_message()方法合并原始消息和警告信息
    """
    def __init__(self, origin_message):
        super().__init__(origin_message)
        # 输出：初始化警告信息字符串
        self.warning_message = ""

    def add_extra_info(self, info):
        """
        添加额外信息到原始消息

        输入:
        - info: Any - 要添加的信息（会自动转换为字符串）

        输出:
        - self.origin_message: 更新后的原始消息
        """
        # 输入：将info转换为字符串并追加到原始消息
        self.origin_message += "\n" + str(info)  # 输出：更新原始消息

    def add_warning(self, warning):
        """
        添加警告信息

        输入:
        - warning: Any - 警告信息（会自动转换为字符串）

        输出:
        - self.warning_message: 更新后的警告信息字符串
        """
        # 输入：将warning转换为字符串并追加
        self.warning_message += "\n" + str(warning)  # 输出：更新警告信息

    def merge_message(self):
        """
        合并原始消息和警告信息

        输出:
        - str: 合并后的完整消息字符串
        """
        # 输出：将原始消息和警告信息合并
        return self.origin_message + "\n" + self.warning_message


class PromptMessage(BaseMessage):
    """
    提示词消息类（用于格式化提示词模板）

    输入:
    - origin_message: str - 提示词模板字符串，可能包含格式化占位符

    输出:
    - 通过write_prompt()方法生成格式化后的提示词
    """
    def __init__(self, origin_message):
        super().__init__(origin_message)

    def write_prompt(self, *args):
        """
        格式化提示词模板

        输入:
        - *args: 可变参数 - 用于填充模板的格式化参数

        输出:
        - str: 格式化后的提示词字符串
        """
        # 输入：使用args参数格式化模板字符串
        # 输出：返回格式化后的提示词
        return self.origin_message.format(*args)