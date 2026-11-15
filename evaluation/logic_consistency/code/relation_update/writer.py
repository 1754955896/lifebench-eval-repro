"""
人际关系合理性评估:关系写入模型层(Relation Writer)

Writer 根据选择器已确认的身份与分支生成受限增量补丁:

- 只处理来源事件的 EXISTING_UPDATE / NEW_RELATION 案例;
- 写入案例携带选择器已确认的 selector_route / matched_person_id /
  candidate_key / selector_evidence_event_ids,Writer 校验器强制补丁
  操作与路由一致(职责绑定),Writer 不得重新判断身份;
- 不生成 person_id(新增对象的 person_id 由程序分配);
- 补丁必须通过 schema 与引用校验,否则携带校验错误修复一次;
- 噪声事件即使被选择器判为更新路由,也不进入 Writer(由管线过滤)。
"""
from __future__ import annotations

from evaluation.logic_consistency.code.relation_update.schema import validate_writer_response
from evaluation.logic_consistency.code.relation_update.selector import run_model_round


class RelationWriter:
    """写入器: 为一批写入案例生成受限增量补丁"""

    def __init__(self, client, store, prompts, opts: dict):
        self.client = client
        self.store = store
        self.prompts = prompts
        self.opts = opts

    def write(self, owner: str, date: str, library: dict,
              write_cases: list, meta: dict):
        """
        返回合法 patches 列表;协议失败返回 None(错误已落 errors.jsonl)。

        write_cases 必须携带 selector_route / matched_person_id /
        candidate_key / selector_evidence_event_ids(校验器据此执行
        职责与证据绑定)。
        同一天同一 candidate_key 的多个新增案例的合并由管线在应用补丁
        时完成(程序分支),Writer 只负责逐案例生成补丁。
        """
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
