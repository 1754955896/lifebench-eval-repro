# evaluation_common — 评估方法共享层

只放被多个方法使用、或目标规范要求集中管理的代码。本包不得依赖任何
`evaluation.<method>` 包。

| 模块 | 职责 |
| --- | --- |
| `input_schema.py` | lineage 角色、时间段、persona section、daily_event/persona/event_tree 校验 |
| `context.py` | 时间解析、大纲索引、persona/事件构造、按日分组、token 估算 |
| `data_loader.py` | 照片/短信/邮件加载与人物目录发现 |
| `storage.py` | `PersonOutputStore`、JSON/JSONL 原子读写、续跑完成集合 |
| `signatures.py` | 文件/对象稳定哈希、manifest 签名一致性检查与迁移 |
| `model_adapter.py` | 旧评估客户端合同、预算客户端、模型 JSON 解析 |
| `metrics.py` | JS 散度、熵、Wasserstein、Gini、haversine、bootstrap |
| `config.py` | `config.json` 读取与方法配置优先级合并 |
| `runner.py` | 方法入口共用的人物编排与 metrics 落盘 |
| `behavior.py` | 照片/SMS 行为分析的参照文件解析与逐人编排 |

约定：

- 方法包可以导入本层与自身模块，不得导入其他方法包；
- 本层不解析 CLI、不启动线程池、不发起网络请求、不写输出文件；
- 导入本层不需要模型凭据；
- 旧评估适配器只经 `tools.llm` 调用模型，不创建私有客户端。
