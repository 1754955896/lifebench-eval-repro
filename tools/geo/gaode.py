"""This module accesses geographic services."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

from tools.evaluation_common.config import REPO_ROOT, load_config

GAODE_GEO_URL = (
    "https://restapi.amap.com/v3/geocode/geo"
    "?address={}&city={}&sig=&output=JSON&callback&key={}")
GAODE_POI_URL = (
    "https://restapi.amap.com/v3/place/text"
    "?keywords={}&types=&city={}&citylimit=&children=1&offset=20&page=1"
    "&extensions=all&sig&output=JSON&callback&key={}")
GAODE_REGEO_URL = (
    "https://restapi.amap.com/v3/geocode/regeo"
    "?location={}&radius=0&extensions=base&output=JSON&callback&parameters"
    "&key={}")
GAODE_ROUTE_URLS = {
    "walking": (
        "https://restapi.amap.com/v3/direction/walking"
        "?origin={}&destination={}&origin_id&destination_id&alternative_route"
        "&show_fields&sig&isindoor=&output=JSON&callback&key={}"),
    "transit": (
        "https://restapi.amap.com/v3/direction/transit/integrated"
        "?origin={}&destination={}&origin_id&destination_id&alternative_route"
        "&show_fields&sig&isindoor=&output=JSON&callback&key={}"),
    "driving": (
        "https://restapi.amap.com/v3/direction/driving"
        "?origin={}&destination={}&origin_id&destination_id&alternative_route"
        "&show_fields&sig&isindoor=&output=JSON&callback&key={}"),
    "bicycling": (
        "https://restapi.amap.com/v4/direction/bicycling"
        "?origin={}&destination={}&origin_id&destination_id&alternative_route"
        "&show_fields&sig&isindoor=&output=JSON&callback&key={}"),
}

WALKING_MODES = {"步行", "徒步"}
TRANSIT_MODES = {"公共交通", "地铁", "公交", "火车", "飞机", "轮船", "高铁"}
DRIVING_MODES = {"自驾车", "驾车", "自驾", "网约车"}
BICYCLING_MODES = {"骑车", "自行车", "电动车"}


class GeoError(RuntimeError):
    pass


def get_api_key() -> str:
    key = os.environ.get("GAODE_API_KEY")
    if key:
        return key
    config = load_config()
    return ((config.get("geo") or {}).get("gaode_api_key") or "").strip()


def require_api_key() -> str:
    key = get_api_key()
    if not key:
        raise GeoError(
            "未配置高德 API key：请在 config/config.json 的 geo.gaode_api_key "
            "中填写，或设置环境变量 GAODE_API_KEY")
    return key


def geocode_url(address: str, city: str = "") -> str:
    return GAODE_GEO_URL.format(address, city, require_api_key())


def poi_search_url(keyword: str, city: str = "") -> str:
    return GAODE_POI_URL.format(keyword, city, require_api_key())


def regeo_url(location: str) -> str:
    return GAODE_REGEO_URL.format(location, require_api_key())


def route_url(mode: str, origin: str, destination: str) -> str | None:

    if mode in WALKING_MODES:
        kind = "walking"
    elif mode in TRANSIT_MODES:
        kind = "transit"
    elif mode in DRIVING_MODES:
        kind = "driving"
    elif mode in BICYCLING_MODES:
        kind = "bicycling"
    else:
        return None
    return GAODE_ROUTE_URLS[kind].format(
        origin, destination, require_api_key())


def geocode(address: str, city: str = "", timeout: float = 10.0) -> dict:

    return request_json(geocode_url(address, city), timeout=timeout)


def route(mode: str, origin: str, destination: str,
          timeout: float = 10.0) -> dict:

    url = route_url(mode, origin, destination)
    if url is None:
        return {}
    return request_json(url, timeout=timeout)


def request_json(url: str, timeout: float = 10.0) -> dict:

    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise GeoError(f"高德请求失败: {exc}") from exc
    if not isinstance(data, dict):
        raise GeoError("高德返回内容不是 JSON 对象")
    return data


def load_geocode_cache(cache_path: str) -> dict:
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    return {}


def save_geocode_cache(cache: dict, cache_path: str) -> None:
    directory = os.path.dirname(os.path.abspath(cache_path))
    os.makedirs(directory, exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cache_path)


def geocode_photos(photos: list, cache_path: str,
                   qps_sleep: float = 0.35) -> dict:


    cache = load_geocode_cache(cache_path)
    poi_coords = {}
    for photo in photos:
        loc = photo.get("location", {})
        if not isinstance(loc, dict):
            continue
        city = loc.get("city", "")
        poi = loc.get("poi", "")
        province = loc.get("province", "")
        district = loc.get("district", "")
        street = loc.get("streetName", "")
        number = loc.get("streetNumber", "")
        key = f"{city}|{poi}"

        if key in cache and cache[key] is not None:
            coords = cache[key]
            if coords:
                poi_coords[key] = (coords[0], coords[1])
            continue

        address = f"{province}{city}{district}{street}{number}{poi}"
        url = geocode_url(address, city)
        try:
            data = request_json(url)
            if data.get("status") == "1" and data.get("geocodes"):
                lng, lat = data["geocodes"][0]["location"].split(",")
                cache[key] = [float(lat), float(lng)]
                poi_coords[key] = (float(lat), float(lng))
            else:
                cache[key] = None
        except GeoError:
            cache[key] = None
        if qps_sleep:
            time.sleep(qps_sleep)
    save_geocode_cache(cache, cache_path)
    return poi_coords


def path_search_url(mode: str, origin_location: str,
                    destination_location: str) -> str | None:

    return route_url(mode, origin_location, destination_location)


def default_cache_path(name: str = "gaode_geocode_cache.json") -> str:
    return str(Path(REPO_ROOT) / "output" / name)


__all__ = [
    "GeoError", "get_api_key", "require_api_key", "geocode_url",
    "poi_search_url", "regeo_url", "route_url", "request_json",
    "load_geocode_cache", "save_geocode_cache", "geocode_photos",
    "path_search_url", "default_cache_path", "WALKING_MODES",
    "TRANSIT_MODES", "DRIVING_MODES", "BICYCLING_MODES",
]
