# Shared Tools

Evaluation scripts use these packages for shared functions:

| Package | Purpose |
| --- | --- |
| [llm/](llm/README.md) | Language model calls |
| [geo/](geo/README.md) | Geocoding and route queries |
| [evaluation_common/](evaluation_common/README.md) | Input loading, validation, metrics, and output |

After installing the root `requirements.txt`, import them in Python:

```python
from tools.llm import chat_completion
from tools.geo import geocode, route
from tools.evaluation_common.data_loader import discover_persons
```
