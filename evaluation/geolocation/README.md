# Geolocation Evaluation

These scripts read NPZ inputs in the local `data/` directory and generate geolocation comparison tables. Install the dependencies in `evaluation/geolocation/requirements.txt`.

Run from the repository root:

```bash
pip install -r evaluation/geolocation/requirements.txt
python evaluation/geolocation/reproduce_table.py
python evaluation/geolocation/reproduce_descriptive_table.py
```

`reproduce_table.py` writes `table.json`, and `reproduce_descriptive_table.py` writes `descriptive_table.json` in this directory. Neither script requires a model or map service key.
