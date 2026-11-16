"""This module loads evaluation data."""

import json
import os
from datetime import datetime


def load_photo_data(yw_path, atm_path):


    with open(yw_path, "r", encoding="utf-8") as f:
        yw = json.load(f)
    with open(atm_path, "r", encoding="utf-8") as f:
        atm = json.load(f)
    return yw, atm


def load_yw_labels(labels_path):


    with open(labels_path, 'r', encoding='utf-8') as f:
        labels = json.load(f)
    return {l['idx']: l for l in labels}


def load_sms(path):


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
            'dow': dt.weekday(),
            'date': dt.date(),
            'content': m.get('message_content', ''),
            'msg_type': m.get('message_type', ''),
            'contact': m.get('contactName', ''),
        })
    return messages


def discover_persons(data_dir, required_files=("daily_event.json",)):


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


    path = explicit or os.path.join(person_dir, relative)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"缺少{label or relative}文件: {path}")
    return path


def load_emails(path):


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
