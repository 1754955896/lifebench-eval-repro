# LifeBench Evaluation Reproduction

This repository contains input data and scripts for geolocation, event diversity, interest drift, logic consistency, and time use evaluation.

## Setup

Run from the repository root:

```bash
pip install -r requirements.txt
cp config/config.example.json config/config.json
```

For scripts that call a language model or map service, set the credentials described in [Configuration](config/README.md). Offline scripts do not require credentials.

## Directories

| Directory | Contents |
| --- | --- |
| [evaluation/](evaluation/README.md) | Evaluation scripts, input formats, and commands |
| [data/](data/README.md) | Person and ablation data |
| [tools/](tools/README.md) | Language model, map, and shared evaluation utilities |
| [config/](config/README.md) | Configuration template and environment variables |
| [git log/](git%20log/README.md) | LifeBench repository commit history |

Run evaluation commands from the repository root. Write generated files to `output/`, which Git ignores. See [Evaluation](evaluation/README.md) for commands.
