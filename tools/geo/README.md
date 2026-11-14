# geo — 高德地理工具

封装高德地理编码、POI、逆地理编码与路径查询，并提供地理编码缓存。
评估方法只依赖本包导出的函数，不直接调用底层 API。

## 凭据

- `config/config.json` 的 `geo.gaode_api_key`；
- 环境变量 `GAODE_API_KEY` 优先覆盖。

缺失凭据时，缓存命中仍可离线使用；缓存未命中的真实请求会抛出
`GeoError`，不会静默返回空结果。

## 接口

```python
from tools.geo import (
    geocode, route, geocode_photos, request_json,
    geocode_url, route_url, poi_search_url, regeo_url,
    load_geocode_cache, save_geocode_cache, GeoError,
)
```

- `geocode(address, city)`：地址转坐标，返回高德 JSON。
- `route(mode, origin, destination)`：路径查询；未知出行方式返回 `{}`。
- `geocode_photos(photos, cache_path)`：照片 POI 批量地理编码，输出
  `{"city|poi": (lat, lng)}` 并写缓存。
- `request_json(url)`：统一 GET + JSON 解析与错误包装。
- URL 构造函数供需要自行限流的旧处理器使用。

## 失败语义

网络错误、非 JSON、HTTP 错误统一抛出 `GeoError`；调用方决定记录为
`<地点错误>`/日志失败还是中止。旧时空校验处理器会把地图失败记录为
离散标签并继续收尾。
