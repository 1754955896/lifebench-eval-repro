"""This module processes evaluation tasks."""

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


GEO_CLIENT = geo


DISPATCH_IDLE_SLEEP = 0.05


class BaseProcessor:


    def __init__(self, processor_id):

        self.processor_id = processor_id

        self.task = None

        self.is_busy = False

        self.lock = threading.Lock()

        self.thread = None

    def assign_task(self, task, callback):


        with self.lock:

            if self.is_busy:
                return False


            self.task = task
            self.is_busy = True


            self.thread = threading.Thread(target=self.run_task, args=(callback,))
            self.thread.start()
            return True

class BaseProcessorCluster:


    def __init__(self, num_processors, task_queue, next_queue):

        self.processors = None

        self.task_queue = task_queue

        self.next_queue = next_queue


        self.running = threading.Event()

        self.dispatcher_thread = None

    def start(self):


        self.running.set()

        self.dispatcher_thread = threading.Thread(target=self.dispatch_loop)
        self.dispatcher_thread.start()

    def dispatch_loop(self):


        while self.running.is_set():
            for p in self.processors:
                if not p.is_busy:
                    try:

                        task = self.task_queue.get()

                        p.assign_task(task, self.mark_task_completed)
                    except Empty:

                        time.sleep(DISPATCH_IDLE_SLEEP)
                        break

    def mark_task_completed(self, result):


        print(result)

    def stop(self):


        self.running.clear()

    def all_processors_idle(self):


        return all(not p.is_busy for p in self.processors)

class ExtractProcessor(BaseProcessor):


    extract_apis = None
    locations_prompt = None
    events_prompt = None
    people_prompt = None

    def __init__(self, processor_id, extract_apis, args):
        super().__init__(processor_id)

        if ExtractProcessor.extract_apis is None:
            ExtractProcessor.extract_apis = extract_apis

        if ExtractProcessor.locations_prompt is None:
            with open(args.locations_extraction, "r", encoding="utf-8") as file:
                ExtractProcessor.locations_prompt = PromptMessage(file.read())

        if ExtractProcessor.events_prompt is None:
            with open(args.events_extraction, "r", encoding="utf-8") as file:
                ExtractProcessor.events_prompt = PromptMessage(file.read())

        if ExtractProcessor.people_prompt is None:
            with open(args.people_extraction, "r", encoding="utf-8") as file:
                ExtractProcessor.people_prompt = PromptMessage(file.read())

    def run_task(self, callback):


        try:

            ExtractProcessor.extract_apis["locations_queue"].add((self.task[0], ExtractProcessor.locations_prompt.write_prompt(self.task[0].persona.location_message, self.task[0].event)))

            ExtractProcessor.extract_apis["events_queue"].add((self.task[0], ExtractProcessor.events_prompt.write_prompt(self.task[0].event)))

            ExtractProcessor.extract_apis["relations_queue"].add((self.task[0], ExtractProcessor.people_prompt.write_prompt(self.task[0].event)))


            result = f"ExtractProcessor [{self.processor_id}] 完成{self.task[0].date}的提取Prompt组装"
            callback(result)
        finally:
            with self.lock:

                self.is_busy = False
                self.task = None

class ExtractProcessorCluster(BaseProcessorCluster):


    def __init__(self, num_processors, queue, extract_apis, args):
        super().__init__(num_processors, queue, extract_apis)

        self.processors = [ExtractProcessor(f"P{i}", self.next_queue, args) for i in range(num_processors)]

class APIExtractProcessor(BaseProcessor):


    deepseek = None
    analyze_queue = None

    def __init__(self, processor_id, next_queue, deepseek):
        super().__init__(processor_id)

        if APIExtractProcessor.deepseek is None:
            APIExtractProcessor.deepseek = deepseek

        if APIExtractProcessor.analyze_queue is None:
            APIExtractProcessor.analyze_queue = next_queue

    def assign_task(self, task, num, callback):


        with self.lock:
            if self.is_busy:
                return False

            self.task = task
            self.is_busy = True

            self.thread = threading.Thread(target=self.run_task, args=(num, callback,))
            self.thread.start()
            return True

    def run_task(self, num, callback):


        try:

            response = self.deepseek.ask(self.task[1])


            if num % 3 == 0:

                locations_match = re.search(r"<locations>(.*?)</locations>", response).group(1).strip()

                locations_items = re.findall(r"\((.*?),\s*(.*?)\)", locations_match)

                locations = [{"地点": loc, "城市": city} for loc, city in locations_items]


                paths_text = re.search(r"<paths>(.*?)</paths>", response, re.S).group(1).strip()

                paths_items = re.findall(r"\((.*?),\s*(.*?),\s*(.*?)\)", paths_text)

                paths = [{"from": p[0], "to": p[1], "mode": p[2]} for p in paths_items]


                self.task[0].locations = locations
                self.task[0].paths = paths

                result = f"APIExtractProcessor [{self.processor_id}] 完成{self.task[0].date}的locations和paths的提取"
            elif num % 3 == 1:

                event_text = re.search(r"<events>(.*?)</events>", response, re.S).group(1).strip()

                event_items = re.findall(r"\((.*?),\s*(.*?)\)", event_text)

                events = [{"事件": event, "时间": time} for event, time in event_items]


                self.task[0].events = events

                result = f"APIExtractProcessor [{self.processor_id}] 完成{self.task[0].date}的events的提取"
            else:

                names_match = re.search(r"<names>(.*?)</names>", response)

                names = names_match.group(1).split() if names_match else []


                self.task[0].relations = names

                result = f"APIExtractProcessor [{self.processor_id}] 完成{self.task[0].date}的relations的提取"


            if self.task[0].check_stage_1():
                APIExtractProcessor.analyze_queue.add((self.task[0],))

            callback(result)

        finally:
            with self.lock:

                self.is_busy = False
                self.task = None

class APIExtractProcessorCluster(BaseProcessorCluster):


    def __init__(self, num_processors, task_queue, next_queue, deepseek, args):
        super().__init__(num_processors, task_queue, next_queue)

        self.locations_queue = self.task_queue["locations_queue"]
        self.events_queue = self.task_queue["events_queue"]
        self.relations_queue = self.task_queue["relations_queue"]

        self.processors = [APIExtractProcessor(f"P{i}", next_queue, deepseek) for i in range(num_processors)]

    def dispatch_loop(self):


        count = 0
        while self.running.is_set():
            for p in self.processors:
                if not p.is_busy:
                    try:

                        if count % 3 == 0:
                            task = self.locations_queue.get()
                        elif count % 3 == 1:
                            task = self.events_queue.get()
                        elif count % 3 == 2:
                            task = self.relations_queue.get()

                        p.assign_task(task, count % 3, self.mark_task_completed)
                        count += 1
                    except Empty:
                        time.sleep(DISPATCH_IDLE_SLEEP)
                        break

class RelationAnalyzeProcessor(BaseProcessor):


    relations = None

    def __init__(self, processor_id):
        super().__init__(processor_id)

    def _build_name_index(self, nested_list):


        index = {}

        def flatten(lst):

            for item in lst:
                if isinstance(item, list):
                    yield from flatten(item)
                elif isinstance(item, dict):
                    yield item


        for d in flatten(nested_list):
            name = d.get("name")
            if name:

                index[name] = d

        return index

    def _search(self, name):


        try:

            self.task[0].res_for_relations.append(Result(RelationAnalyzeProcessor.relations[name], "<人物正确>", f"{name}的个人信息是{RelationAnalyzeProcessor.relations[name]}"))
            return RelationAnalyzeProcessor.relations[name]
        except KeyError:

            for k, v in RelationAnalyzeProcessor.relations.items():
                if v["nickname"] == name or v["relation"] == name:
                    self.task[0].res_for_relations.append(Result(v, "<人物正确>", f"{name}的个人信息是{v}"))
                    return v


            self.task[0].res_for_relations.append(Result(None, "<人物错误>", f"{name}人物无法找到，请检查"))

    def run_task(self, callback):


        try:

            if RelationAnalyzeProcessor.relations is None:
                RelationAnalyzeProcessor.relations = self._build_name_index(self.task[0].persona.relation)

            for name in self.task[0].relations:
                self._search(name)

            result = f"RelationAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的relations查询"
            callback(result)
        finally:
            with self.lock:

                self.is_busy = False
                self.task = None

class LocationAnalyzeProcessor(BaseProcessor):


    gaode_api = None

    def __init__(self, processor_id, next_queue):
        super().__init__(processor_id)

        if LocationAnalyzeProcessor.gaode_api is None:
            LocationAnalyzeProcessor.gaode_api = next_queue

        self.locations_api = LocationAnalyzeProcessor.gaode_api

    def run_task(self, call_back):


        try:

            for location in self.task[0].locations:

                self.task[0].res_for_locations[location["地点"]] = None

                self.locations_api.add((self.task[0],
                                        ("geocode", location["地点"],
                                         location["城市"]),
                                        location["地点"]))

            result = f"LocationAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的locations的API组装"
            call_back(result)
        finally:
            with self.lock:

                self.is_busy = False
                self.task = None

class EventAnalyzeProcess(BaseProcessor):


    deepseek = None
    calendar = None
    events_api = None
    check_prompt = None

    def __init__(self, processor_id, next_queue, deepseek, calendar, args):
        super().__init__(processor_id)

        if EventAnalyzeProcess.calendar is None:
            EventAnalyzeProcess.calendar = calendar

        if EventAnalyzeProcess.events_api is None:
            EventAnalyzeProcess.events_api = next_queue

        if EventAnalyzeProcess.deepseek is None:
            EventAnalyzeProcess.deepseek = deepseek

        if EventAnalyzeProcess.check_prompt is None:
            with open(args.event_check, "r", encoding="utf-8") as file:
                EventAnalyzeProcess.check_prompt = PromptMessage(file.read())

    def run_task(self, callback):


        try:
            questions = {}
            info = {}

            for i, event in enumerate(self.task[0].events):

                for day in range(-2, 3):
                    try:

                        goal_day = another_day(event["时间"][:-2], day)

                        if goal_day not in questions.keys():
                            questions[goal_day] = [event]
                        else:
                            questions[goal_day].append(event)
                        try:

                            res = self.calendar[event['时间'][:-2]].event
                        except KeyError:

                            print(f"<提取时间错误>{event['时间'][:-2]}不在本次范围内")

                        if goal_day not in info.keys():
                            if res is not None:
                                info[goal_day] = res
                            else:
                                del questions[goal_day]
                    except UnboundLocalError as e:

                        print(f"<提取时间错误>因为出现错误{e}，所以{event['时间']}被跳过")


            results = process_with_threading(
                questions,
                info,
                EventAnalyzeProcess.deepseek,
                EventAnalyzeProcess.check_prompt,
                max_workers=10
                )


            self.task[0].res_for_events = results


            for event, dct in results.items():
                ans = []
                for day, res in dct.items():
                    if res == "Yes":
                        ans.append(day)

                if event[-10:] in ans:
                    self.task[0].analyze_for_events.append(Result(None, "<事件安排正确>", f"{event[:-10]}确实在预测时间{event[-10:]}发生"))
                else:
                    if len(ans) != 0:
                        self.task[0].analyze_for_events.append(Result(None, "<事件安排疑似错误>", f"{event[:-10]}在预测时间{event[-10:]}未发生发生，在{ans}均有发生"))
                    else:
                        self.task[0].analyze_for_events.append(Result(None, "<事件安排错误>", f"{event[:-10]}在预测时间{event[-10:]}附近均未发生"))


            result = f"EventAnalyzeProcess [{self.processor_id}] 完成{self.task[0].date}的events API组装"
            callback(result)
        finally:
            with self.lock:

                self.is_busy = False
                self.task = None

class AnalyzeProcessorCluster(BaseProcessorCluster):


    def __init__(self, num_processors, task_queue, next_queue, deepseek, calendar, args):
        super().__init__(num_processors, task_queue, next_queue)

        self.relations_processors = [RelationAnalyzeProcessor(f"P{i}") for i in range(num_processors)]

        self.locations_processors = [LocationAnalyzeProcessor(f"P{i}", self.next_queue["locations"]) for i in range(num_processors)]

        self.events_processors = [EventAnalyzeProcess(f"P{i}", self.next_queue["events"], deepseek, calendar, args) for i in range(num_processors)]

    def _get_task(self, processors, pending_queue):


        for p in processors:
            if not p.is_busy:
                try:

                    p.assign_task(pending_queue.get(), self.mark_task_completed)
                    break
                except Empty:
                    break

    def dispatch_loop(self):


        pending_queue_1 = TaskQueueManager()
        pending_queue_2 = TaskQueueManager()
        pending_queue_3 = TaskQueueManager()

        while self.running.is_set():
            try:

                task = self.task_queue.get()

                pending_queue_1.add(task)
                pending_queue_2.add(task)
                pending_queue_3.add(task)
            except Empty:
                pass


            self._get_task(self.relations_processors, pending_queue_1)
            self._get_task(self.locations_processors, pending_queue_2)
            self._get_task(self.events_processors, pending_queue_3)

    def all_processors_idle(self):


        return all(not p.is_busy for p in self.relations_processors) and all(not p.is_busy for p in self.locations_processors) and all(not p.is_busy for p in self.events_processors)

class APILocationAnalyzeProcessor(BaseProcessor):


    gaode_mid_stage = None
    limiter = None
    limiter_lock = threading.Lock()

    def __init__(self, processor_id, next_queue, limiter):
        super().__init__(processor_id)

        if APILocationAnalyzeProcessor.gaode_mid_stage is None:
            APILocationAnalyzeProcessor.gaode_mid_stage = next_queue

        if APILocationAnalyzeProcessor.limiter is None:
            APILocationAnalyzeProcessor.limiter = limiter

    def _completed(self):


        return all(v is not None for v in self.task[0].res_for_locations.values())

    def run_task(self, callback):


        try:

            while True:
                try:

                    with APILocationAnalyzeProcessor.limiter_lock:
                        APILocationAnalyzeProcessor.limiter.try_acquire("locations api")

                    response = GEO_CLIENT.geocode(*self.task[1][1:])
                    break
                except BucketFullException as e:

                    remaining = e.meta_info.get("remaining_time", 0.34)
                    time.sleep(remaining + 0.01)
                except Exception as e:
                    response = {"status": "0", "error": str(e)}
                    break


            if response["status"] != "1":

                self.task[0].res_for_locations[self.task[2]] = Result(response, "<地点错误>", f"{self.task[2]}可能不存在，报错信息为{response}")
            else:

                if response["count"] > "1":

                    self.task[0].res_for_locations[self.task[2]] = Result(response, "<地点正确但多个候选>", f"{self.task[2]}的地点信息是{response}")
                else:

                    self.task[0].res_for_locations[self.task[2]] = Result(response, "<地点正确>", f"{self.task[2]}的地点信息是{response}")


            if self._completed():

                APILocationAnalyzeProcessor.gaode_mid_stage.add((self.task[0],))
                result = f"APILocationAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的最后一个locations请求"
            else:
                result = f"APILocationAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的一个locations请求"
            callback(result)
        finally:
            with self.lock:

                self.is_busy = False
                self.task = None

class APILocationAnalyzeProcessorCluster(BaseProcessorCluster):


    def __init__(self, num_processors, task_queue, next_queue):
        super().__init__(num_processors, task_queue, next_queue)

        self.processors = [APILocationAnalyzeProcessor(f"P{i}", self.next_queue, get_limiter(self.task_queue.frequency)) for i in range(num_processors)]

    def dispatch_loop(self):


        while self.running.is_set():
            for p in self.processors:
                if not p.is_busy:
                    try:

                        task = self.task_queue.get()

                        p.assign_task(task, self.mark_task_completed)
                        break
                    except Empty:

                        time.sleep(DISPATCH_IDLE_SLEEP)
                        break

class PathAnalyzeProcessor(BaseProcessor):


    gaode_api = None

    def __init__(self, processor_id, next_queue):
        super().__init__(processor_id)

        if PathAnalyzeProcessor.gaode_api is None:
            PathAnalyzeProcessor.gaode_api = next_queue

        self.paths_api = PathAnalyzeProcessor.gaode_api

    def _get_location(self, location):


        if self.task[0].res_for_locations[location].info["status"] == "1":

            return self.task[0].res_for_locations[location].info["geocodes"][0]["location"]
        else:

            raise KeyError(f"{location}的status不为1,错误为{self.task[0].res_for_locations[location].info}")

    def run_task(self, callback):


        try:

            for path in self.task[0].paths:
                try:

                    request_spec = None
                    if path["mode"] in ["步行", "徒步", "公共交通", "地铁",
                                        "公交", "火车", "飞机", "轮船", "高铁",
                                        "自驾车", "驾车", "自驾", "网约车",
                                        "骑车", "自行车", "电动车"]:
                        request_spec = ("route", path["mode"],
                                        self._get_location(path["from"]),
                                        self._get_location(path["to"]))
                    else:

                        print(f"意料之外的出行方式{path['mode']}")

                    self.paths_api.add((self.task[0], request_spec, path))
                except KeyError as e:

                    self.task[0].paths_error -= 1
                    print(f"PathAnalyzeProcessor [{self.processor_id}] 意外的地名错误{e}")

            result = f"PathAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的paths的API组装"
            callback(result)
        finally:
            with self.lock:

                self.is_busy = False
                self.task = None

class PathAnalyzeProcessorCluster(BaseProcessorCluster):


    def __init__(self, num_processors, task_queue, next_queue):
        super().__init__(num_processors, task_queue, next_queue)

        self.processors = [PathAnalyzeProcessor(f"P{i}", self.next_queue) for i in range(num_processors)]

class APIPathAnalyzeProcessor(BaseProcessor):


    paths_analyze_queue = None
    limiter = None
    limiter_lock = threading.Lock()

    def __init__(self, processor_id, next_queue, limiter):
        super().__init__(processor_id)

        if APIPathAnalyzeProcessor.paths_analyze_queue is None:
            APIPathAnalyzeProcessor.paths_analyze_queue = next_queue

        if APIPathAnalyzeProcessor.limiter is None:
            APIPathAnalyzeProcessor.limiter = limiter

    def _complete(self):
        return len(self.task[0].res_for_paths) == len(self.task[0].paths) + self.task[0].paths_error

    def run_task(self, callback):


        try:

            while True:
                try:

                    with APIPathAnalyzeProcessor.limiter_lock:
                        APIPathAnalyzeProcessor.limiter.try_acquire("paths api")

                    if self.task[1] is not None:
                        response = GEO_CLIENT.route(*self.task[1][1:])
                    else:
                        response = {}
                    break
                except BucketFullException as e:

                    remaining = e.meta_info.get("remaining_time", 0.2)
                    time.sleep(remaining)
                except Exception as e:
                    response = {"error": str(e)}
                    break

            try:
                try:

                    distance = response['route']['paths'][0]['distance']
                    duration = response['route']['paths'][0]['duration']
                except KeyError:

                    distance = response['route']['transits'][0]['distance']
                    duration = response['route']['transits'][0]['duration']
            except Exception:

                print(f"路径查询错误，内容为{response}")
                self.task[0].res_for_paths.append(Result(None, "<路径时间查询错误>", f"路径查询错误，内容为{response}"))
            else:

                self.task[0].res_for_paths.append(Result(None, "<路径时间参考>", f"{self.task[2]['mode']}从{self.task[2]['from']}到{self.task[2]['to']}的参考距离为{distance}米，此时此刻出行参考时间为{duration}秒"))

            if self._complete():
                APIPathAnalyzeProcessor.paths_analyze_queue.add((self.task[0],))
                result = f"APIPathAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的最后一次paths的查询"
            else:
                result = f"APIPathAnalyzeProcessor [{self.processor_id}] 完成{self.task[0].date}的paths的查询"
            callback(result)
        finally:
            with self.lock:

                self.is_busy = False
                self.task = None

class APIPathAnalyzeProcessorCluster(BaseProcessorCluster):


    def __init__(self, num_processors, task_queue, next_queue):
        super().__init__(num_processors, task_queue, next_queue)

        self.processors = [APIPathAnalyzeProcessor(f"P{i}", self.next_queue, get_limiter(self.task_queue.frequency)) for i in range(num_processors)]

    def dispatch_loop(self):


        while self.running.is_set():
            for p in self.processors:
                if not p.is_busy:
                    try:

                        task = self.task_queue.get()

                        p.assign_task(task, self.mark_task_completed)
                        break
                    except Empty:

                        time.sleep(DISPATCH_IDLE_SLEEP)
                        break

class DeepseekPathAnalyzeProcess(BaseProcessor):


    deepseek_path_api = None
    paths_prompt = None

    def __init__(self, processor_id, next_queue, args):
        super().__init__(processor_id)

        if DeepseekPathAnalyzeProcess.deepseek_path_api is None:
            DeepseekPathAnalyzeProcess.deepseek_path_api = next_queue

        if DeepseekPathAnalyzeProcess.paths_prompt is None:
            with open(args.verify_1, "r", encoding="utf-8") as file:
                DeepseekPathAnalyzeProcess.paths_prompt = PromptMessage(file.read())

    def run_task(self, callback):


        try:

            paths_info = "\n".join([item.text for item in self.task[0].res_for_paths])

            DeepseekPathAnalyzeProcess.deepseek_path_api.add((self.task[0], DeepseekPathAnalyzeProcess.paths_prompt.write_prompt(self.task[0].paths, self.task[0].persona.location_message, paths_info, self.task[0].event)))

            result = f"DeepseekPathAnalyzeProcess [{self.processor_id}] 完成{self.task[0].date}的deepseek paths的API组装"
            callback(result)
        finally:
            with self.lock:

                self.is_busy = False
                self.task = None

class DeepseekPathAnalyzeProcessCluster(BaseProcessorCluster):


    def __init__(self, num_processors, task_queue, next_queue, args):
        super().__init__(num_processors, task_queue, next_queue)

        self.processors = [DeepseekPathAnalyzeProcess(f"P{i}", self.next_queue, args) for i in range(num_processors)]

class APIDeepseekPathAnalyzeProcess(BaseProcessor):


    deepseek = None

    def __init__(self, processor_id, deepseek):
        super().__init__(processor_id)

        if APIDeepseekPathAnalyzeProcess.deepseek is None:
            APIDeepseekPathAnalyzeProcess.deepseek = deepseek

    def run_task(self, callback):


        try:

            response = APIDeepseekPathAnalyzeProcess.deepseek.ask(self.task[1])

            self.task[0].analyze_for_paths = response

            result = f"APIDeepseekPathAnalyzeProcess [{self.processor_id}] 完成{self.task[0].date}的deepseek paths的询问"
            callback(result)
        finally:
            with self.lock:

                self.is_busy = False
                self.task = None

class APIDeepseekPathAnalyzeProcessCluster(BaseProcessorCluster):


    def __init__(self, num_processors, task_queue, next_queue, deepseek):
        super().__init__(num_processors, task_queue, next_queue)

        self.processors = [APIDeepseekPathAnalyzeProcess(f"P{i}", deepseek) for i in range(num_processors)]

class APIEventAnalyzeProcess(BaseProcessor):


    deepseek = None
    def __init__(self, processor_id, deepseek):
        super().__init__(processor_id)

        if APIEventAnalyzeProcess.deepseek is None:
            APIEventAnalyzeProcess.deepseek = deepseek

    def run_task(self, callback):


        try:

            response = APIEventAnalyzeProcess.deepseek.ask(self.task[1])


            result_match = re.search(r"<results>(.*?)</results>", response)
            result = result_match.group(1).split() if result_match else []


            results = {}
            for res, event in zip(result, self.task[3]):
                event_key = event["事件"] + event["时间"][:-2]
                if event_key not in results.keys():
                    results[event_key] = {self.task[2]: res}
                else:
                    if self.task[2] in results[event_key].keys():

                        if results[event_key][self.task[2]] == "No" and res == "Yes":
                            results[event_key][self.task[2]] = res
                    else:
                        results[event_key][self.task[2]] = res


            self.task[0].res_for_events = results


            for event, dct in results.items():
                ans = []
                for day, res in dct.items():
                    if res == "Yes":
                        ans.append(day)

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

                self.is_busy = False
                self.task = None

class APIEventAnalyzeProcessCluster(BaseProcessorCluster):


    def __init__(self, num_processors, task_queue, next_queue, deepseek):
        super().__init__(num_processors, task_queue, next_queue)

        self.processors = [APIEventAnalyzeProcess(f"P{i}", deepseek) for i in range(num_processors)]
