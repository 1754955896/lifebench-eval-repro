"""This module defines evaluation tasks."""

def serialize(obj):

    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj


    if isinstance(obj, (list, tuple)):
        return [serialize(x) for x in obj]


    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}


    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return serialize(obj.to_dict())


    if hasattr(obj, "__dict__"):
        return {
            k: serialize(v)
            for k, v in obj.__dict__.items()
            if not k.startswith("_")
        }

    raise TypeError(f"{type(obj)} is not JSON serializable")


class Task:


    def __init__(self, date, event, persona):

        self.name = persona.origin_message["name"]
        self.date = date
        self.event = event
        self.persona = persona


        self.events = None
        self.locations = None
        self.relations = None
        self.paths = None
        self.paths_error = 0


        self.res_for_events = None
        self.res_for_locations = {}
        self.res_for_relations = []
        self.res_for_paths = []


        self.analyze_for_paths = None
        self.analyze_for_events = []

    def check_stage_1(self):


        if None not in [self.events, self.locations, self.relations, self.paths]:
            return True
        else:
            return False

    def to_dict(self):
        return {
            "name": self.name,
            "date": self.date,


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


    def __init__(self, info, prefix, text):

        self.info = info
        self.prefix = prefix
        self.text = text

    def __str__(self):


        return self.prefix + self.text + "\n"

    def to_dict(self):
        return {
            "info": self.info,
            "prefix": self.prefix,
            "text": self.text
        }
