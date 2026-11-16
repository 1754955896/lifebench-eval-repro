# Geolocation Tools

This package provides Gaode geocoding, POI lookup, and route queries. Before making online requests, set `geo.gaode_api_key` or `GAODE_API_KEY` as described in [Configuration](../../config/README.md).

```python
from tools.geo import geocode, route, geocode_photos, GeoError
```

`geocode(address, city)` returns a geocoding result. `route(mode, origin, destination)` returns a route query result. `geocode_photos(photos, cache_path)` geocodes photo locations in batches and reads or writes a cache. Network, HTTP, and response parsing errors raise `GeoError`.
