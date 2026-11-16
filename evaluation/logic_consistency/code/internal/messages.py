"""This module builds evaluation messages."""

class BaseMessage:


    def __init__(self, origin_message):

        self.origin_message = origin_message


class PersonaMessage(BaseMessage):


    def __init__(self, origin_message):
        super().__init__(origin_message)

        self.location_message = self._location_extract()

        self.relation = self.origin_message["relation"]

        self.background_message = self._background_extract()

    def _location_extract(self):


        return {
            "姓名": self.origin_message["name"],
            "城市": self.origin_message["home_address"]["city"],
            "家": self.origin_message["home_address"]["street_name"] + self.origin_message["home_address"]["street_number"],
            "工作地点": self.origin_message["workplace"]["street_name"] + self.origin_message["workplace"]["street_number"]
        }

    def _background_extract(self):


        dit = {}
        for k, v in self.origin_message.items():

            if k not in ["birth", "home_address", "workplace", "relation"]:
                dit[k] = v
        return dit

    def to_dict(self):
        return {
            "origin_message": self.origin_message,
            "location_message": self.location_message,
            "relation": self.relation,
            "background_message": self.background_message
        }

class EventMessage(BaseMessage):


    def __init__(self, origin_message):
        super().__init__(origin_message)

        self.simplied_message = self._simplify()

    def _simplify(self):


        return {
            k: v
            for item in self.origin_message["date"]
            for k, v in item.items()
        }


class InfoMessage(BaseMessage):


    def __init__(self, origin_message):
        super().__init__(origin_message)

        self.info_message = ""

    def add_extra_info(self, info):


        self.info_message += "\n" + str(info)


class FeedbackMessage(BaseMessage):


    def __init__(self, origin_message):
        super().__init__(origin_message)

        self.warning_message = ""

    def add_extra_info(self, info):


        self.origin_message += "\n" + str(info)

    def add_warning(self, warning):


        self.warning_message += "\n" + str(warning)

    def merge_message(self):


        return self.origin_message + "\n" + self.warning_message


class PromptMessage(BaseMessage):


    def __init__(self, origin_message):
        super().__init__(origin_message)

    def write_prompt(self, *args):


        return self.origin_message.format(*args)
