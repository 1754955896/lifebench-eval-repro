# Beijing trajectory table reproduction

This folder contains only the inputs, code, and output for the five-row comparison of LifeBench, WorldMove, and CoPB against ChinaMobile, Tencent POI, and GeoLife.

Run from the repository root:

```powershell
pip install -r evaluation/geolocation/requirements.txt
python evaluation/geolocation/reproduce_table.py
```

The script writes `evaluation/geolocation/table.json` and prints the 45 values in the comparison table. For each reference and method, it uses 265 daily trajectories (the number of fully observed LifeBench days), repeats with seeds 100–149, keeps only half-hour slots observed in both the generated and reference trajectories, and reports the mean base-2 Jensen–Shannon divergence across repeats. The output also includes the standard deviation. All inputs are already in `data/`.

To reproduce the full-dataset descriptive table (mean / median for five features, plus mean valid slots per nonempty day), run:

```powershell
python evaluation/geolocation/reproduce_descriptive_table.py
```

This writes `evaluation/geolocation/descriptive_table.json` and prints the formatted table.

The five features are jump length, daily distance, radius of gyration, dwell time, and daily visited locations. Lower values are better. The standardized reference datasets can have missing slots (`-1`); daily distance sums displacements between adjacent observed slots.
