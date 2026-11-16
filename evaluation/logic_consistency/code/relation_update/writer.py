"""This module writes relation updates."""

from __future__ import annotations

from evaluation.logic_consistency.code.relation_update.schema import validate_writer_response
from evaluation.logic_consistency.code.relation_update.selector import run_model_round


class RelationWriter:


    def __init__(self, client, store, prompts, opts: dict):
        self.client = client
        self.store = store
        self.prompts = prompts
        self.opts = opts

    def write(self, owner: str, date: str, library: dict,
              write_cases: list, meta: dict):


        library_ids = {r["person_id"] for r in library["records"]}
        record_names = {r["person_id"]: r["name"]
                        for r in library["records"]}
        existing_names = {r["name"] for r in library["records"]}
        existing_aliases = {a for r in library["records"]
                            for a in r["aliases"]}

        def validate_fn(obj, chunk):
            return validate_writer_response(
                obj, owner, date, chunk, library_ids, record_names,
                existing_names, existing_aliases)

        return run_model_round(self.client, self.store, self.prompts,
                               "writer", owner, date, library, write_cases,
                               validate_fn, meta, self.opts)
