# Shared Evaluation Utilities

Evaluation scripts use this package to load and validate inputs, calculate metrics, and write results.

| Module | Purpose |
| --- | --- |
| `data_loader.py` | Find person directories and load inputs |
| `input_schema.py`, `context.py` | Validate and organize events, people, and time data |
| `metrics.py` | Calculate statistics |
| `storage.py`, `signatures.py` | Read and write results and check input signatures |
| `config.py`, `runner.py` | Load configuration and run evaluations |
| `model_adapter.py`, `behavior.py` | Parse model output and behavior data |

After installing the root `requirements.txt`, import the modules you need:

```python
from tools.evaluation_common.data_loader import discover_persons
from tools.evaluation_common.storage import write_json
```
