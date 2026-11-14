"""共享数据加载与人物目录发现。

供评估方法复用，各函数均接收文件/目录路径参数。
"""
import json
import os
from datetime import datetime


def load_photo_data(yw_path, atm_path):
    """
    加载 yuxiaowen 与 ATM 照片数据

    输入:
    - yw_path: str - yuxiaowen 照片数据文件路径
    - atm_path: str - ATM 图像批量结果文件路径

    输出:
    - tuple: (yw, atm) 两个照片列表
    """
    with open(yw_path, "r", encoding="utf-8") as f:
        yw = json.load(f)
    with open(atm_path, "r", encoding="utf-8") as f:
        atm = json.load(f)
    return yw, atm


def load_yw_labels(labels_path):
    """
    加载 yuxiaowen 场景标注（label_yw_scenes.py 的输出）

    输入:
    - labels_path: str - 场景标注文件路径

    输出:
    - dict: {照片idx: 标注dict}，标注含 indoor_outdoor / scene_type 字段
    """
    with open(labels_path, 'r', encoding='utf-8') as f:
        labels = json.load(f)
    return {l['idx']: l for l in labels}


def load_sms(path):
    """
    加载 yuxiaowen 短信数据

    输入:
    - path: str - 短信数据文件路径

    输出:
    - list: 消息列表，每条含 datetime/hour/dow/date/content/msg_type/contact 字段
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    messages = []
    for m in raw:
        dt_str = m.get('datetime', '')
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except:
            continue
        messages.append({
            'datetime': dt,
            'hour': dt.hour,
            'dow': dt.weekday(),  # 0=Mon
            'date': dt.date(),
            'content': m.get('message_content', ''),
            'msg_type': m.get('message_type', ''),
            'contact': m.get('contactName', ''),
        })
    return messages


def discover_persons(data_dir, required_files=("daily_event.json",)):
    """发现人物目录。

    - 若 ``data_dir`` 自身包含全部 required_files，视为单个人物目录，
      以目录 basename 作为人物名；
    - 否则扫描其直接子目录，返回包含全部 required_files 的人物目录。

    输出: [(name, path), ...]，按名称排序。
    """
    data_dir = os.path.abspath(data_dir)
    if not os.path.isdir(data_dir):
        raise FileNotFoundError(f"数据目录不存在: {data_dir}")

    def has_all(directory):
        return all(os.path.exists(os.path.join(directory, name))
                   for name in required_files)

    if has_all(data_dir):
        name = os.path.basename(data_dir.rstrip(os.sep)) or "person"
        return [(name, data_dir)]

    persons = []
    for name in sorted(os.listdir(data_dir)):
        path = os.path.join(data_dir, name)
        if os.path.isdir(path) and has_all(path):
            persons.append((name, path))
    return persons


def require_person_file(person_dir, relative, explicit=None, label=None):
    """解析人物文件路径；显式路径优先，否则按人物目录相对路径。

    缺失时抛出 FileNotFoundError，并明确指出缺哪个文件。
    """
    path = explicit or os.path.join(person_dir, relative)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"缺少{label or relative}文件: {path}")
    return path


def load_emails(path):
    """
    加载 ATM 邮件数据

    输入:
    - path: str - 邮件数据文件路径

    输出:
    - list: 消息列表，每条含 datetime/hour/dow/date/content 字段
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    messages = []
    for e in raw:
        dt_str = e.get('timestamp', '')
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except:
            continue
        messages.append({
            'datetime': dt,
            'hour': dt.hour,
            'dow': dt.weekday(),
            'date': dt.date(),
            'content': e.get('detail', ''),
        })
    return messages
