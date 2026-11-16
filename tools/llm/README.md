# Language Model Tools

Install the root `requirements.txt` and set a model key as described in [Configuration](../../config/README.md). Then import:

```python
from tools.llm import chat_completion, llm_call, llm_call_j
```

`llm_call` returns text, `llm_call_j` requests JSON, and `chat_completion` accepts a full message list. `llm_call_reason` and `llm_call_reason_j` use the configured reasoning model. The key is checked when a request is made.
