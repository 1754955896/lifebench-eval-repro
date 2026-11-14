# tools

可复用的工具集合。每个子包封装一类外部能力，评估方法统一从这里取用，
不要在方法目录内重复实现。

| 子包 | 状态 | 说明 |
| --- | --- | --- |
| [`llm/`](llm/) | 可用 | LLM 调用（配置驱动、token 统计、JSON 模式、function calling、完整消息接口） |
| [`geo/`](geo/) | 可用 | 高德地理编码/POI/路径查询与坐标缓存 |
| [`evaluation_common/`](evaluation_common/) | 可用 | 评估方法共享层：输入 schema、上下文、数据加载、I/O、签名、模型适配、指标 |

## 用法

```python
from tools.llm import chat_completion, llm_call, llm_call_j
from tools.geo import geocode, route
from tools.evaluation_common.context import build_outline_index
```

## LLM 接口

- `llm_call(prompt, context)`：默认模型，返回文本。
- `llm_call_j(prompt, context)`：默认模型，JSON 模式。
- `llm_call_reason` / `llm_call_reason_j`：推理模型。
- `llm_agent(messages, tools, dispatch)`：function calling 循环。
- `chat_completion(messages, *, model, temperature, max_tokens,
  response_format, extra_body, timeout, sdk_max_retries, strip_think)`：
  完整 system/user/assistant 消息接口，返回
  `ChatCompletionResult(content, model_returned, finish_reason, usage)`。
  默认不改写原始内容；旧协议评估由适配器显式关闭 SDK 隐式重试。
- `get_token_usage()` / `reset_token_usage()`：进程级 token 统计。
- 无凭据时导入包、运行离线假模型测试均可；凭据只在真正调用前检查。

## 地理接口

- `geocode(address, city)` / `route(mode, origin, destination)`：返回高德
  原始 JSON。
- `geocode_photos(photos, cache_path)`：按照片 `location` 批量地理编码并
  读写缓存。
- 凭据：`config/config.json` 的 `geo.gaode_api_key`，可用环境变量
  `GAODE_API_KEY` 覆盖。

## 新增工具

在 `tools/` 下新建子包，并在 `tools/__init__.py` 中登记；外部依赖写进根
目录 `requirements.txt`，密钥类配置统一放进 `config/config.json`（不入库）。
