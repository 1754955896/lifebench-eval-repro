# Configuration

Copy the template from the repository root:

```bash
cp config/config.example.json config/config.json
```

Set `llm.api_key` in `config/config.json` for language model calls and `geo.gaode_api_key` for Gaode Maps calls. You can also use the environment variables `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_DEFAULT_MODEL`, `LLM_REASON_MODEL`, and `GAODE_API_KEY`.

Git ignores `config/config.json`. Run an evaluation script with `--help` to see its options.
