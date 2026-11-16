"""This module provides evaluation functions."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from evaluation.logic_consistency.code.internal.tasks import Task
import re
import threading
import logging
from pyrate_limiter import Duration, Rate, Limiter


def get_limiter(frequency):


    rate = Rate(frequency, Duration.SECOND)

    limiter = Limiter(rate)
    return limiter

def get_logger(args):


    DETAIL_LEVEL_NUM = 15
    logging.addLevelName(DETAIL_LEVEL_NUM, "DETAIL")
    logging.DETAIL = DETAIL_LEVEL_NUM


    def detail(self, message, *args, **kws):

        if self.isEnabledFor(DETAIL_LEVEL_NUM):
            self._log(DETAIL_LEVEL_NUM, message, args, **kws)


    logging.Logger.detail = detail


    logging.getLogger("pyrate_limiter").setLevel(logging.WARNING)


    logger = logging.getLogger(__name__)


    logging.basicConfig(
        filename=args.log_file,
        filemode='a',
        level=logging.DETAIL,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        encoding="utf-8"
    )

    return logger

def another_day(date, days):


    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")

def preprocess(args, eventM, personaM, extract_queue):


    calendar = {}
    date = args.start_date


    while date <= args.end_date:
        try:

            task = Task(date, eventM.simplied_message[date], personaM)

            calendar[date] = task

            extract_queue.add((task,))
        except KeyError:
            print(f"{date}不存在，已被跳过")

        date = another_day(date, 1)


    return calendar


_log_lock = threading.Lock()

def process_single_item(k, item, events, deepseek, check_prompt):


    local_results = {}

    try:

        for event in events:

            response = deepseek.ask(check_prompt.write_prompt(event["事件"], item))


            result_match = re.search(r"<results>(.*?)</results>", response)
            result = result_match.group(1).split() if result_match else []


            for res in result:

                key = event["事件"] + event["时间"][:-2]
                if key not in local_results:

                    local_results[key] = {k: res}
                else:
                    if k in local_results[key]:

                        if local_results[key][k] == "No" and res == "Yes":
                            local_results[key][k] = res
                    else:

                        local_results[key][k] = res
    except Exception as e:
        pass

    return local_results

def merge_results(results_list):


    final_results = {}


    for local_results in results_list:
        for key, value in local_results.items():
            if key not in final_results:

                final_results[key] = value
            else:

                for k, res in value.items():
                    if k in final_results[key]:

                        if final_results[key][k] == "No" and res == "Yes":
                            final_results[key][k] = res
                    else:
                        final_results[key][k] = res

    return final_results

def process_with_threading(questions, info, deepseek, check_prompt, max_workers=8):


    tasks = []
    for k, v in questions.items():
        try:
            if k in info:
                for item in info[k]:

                    tasks.append((k, item, v))
        except KeyError:
            pass


    results_list = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:

        future_to_task = {
            executor.submit(process_single_item, k, item, events, deepseek, check_prompt): (k, item)
            for k, item, events in tasks
        }


        for future in as_completed(future_to_task):
            try:

                result = future.result()
                results_list.append(result)
            except Exception:
                pass


    results = merge_results(results_list)

    return results
