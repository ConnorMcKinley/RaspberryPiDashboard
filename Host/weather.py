# weather.py
import requests
from datetime import datetime

LAT, LON = 41.8781, -87.6298

def get_chicago_weekly():
    """
    Returns a list of 7 days, each with (date, max, min, code, icon, desc, uv_index_max, snow_sum).
    """
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&daily=temperature_2m_max,temperature_2m_min,weathercode,uv_index_max,snowfall_sum" # Added snowfall_sum
        "&temperature_unit=fahrenheit"
        "&precipitation_unit=inch" # Ensure snow comes in inches
        "&timezone=America/Chicago"
    )
    r = requests.get(url, timeout=10) # Increased timeout slightly
    r.raise_for_status()
    data = r.json()
    days = data['daily']['time']
    temps_max = data['daily']['temperature_2m_max']
    temps_min = data['daily']['temperature_2m_min']
    codes = data['daily']['weathercode']
    uv_indices_max = data['daily']['uv_index_max'] # Get UV data
    snow_sums = data['daily']['snowfall_sum'] # Get Snow data

    results = []
    # API returns 7 days by default, but good to be safe with min()
    for i in range(min(7, len(days))):
        results.append({
            "date": days[i],
            "max": int(round(temps_max[i])),
            "min": int(round(temps_min[i])),
            "code": codes[i],
            "icon": weather_icon_for_code(codes[i]),
            "desc": weather_desc_for_code(codes[i]),
            "uv_index_max": uv_indices_max[i] if uv_indices_max[i] is not None else None, # Add UV index
            "snow_sum": snow_sums[i] if snow_sums[i] is not None else 0.0, # Add Snow sum (inches)
        })
    return results

def weather_icon_for_code(code):
    code = int(code)
    if code in [0]:    return "sun"
    if code in [1]:    return "mostly_sun"
    if code in [2,3]:  return "cloud"
    if code in [45,48]:return "fog"
    if code in range(51,68): return "drizzle"
    if code in range(80,83): return "shower"
    if code in [61,63,65,66,67,80,81,82]: return "rain"
    if code in range(71,77): return "snow"
    if code in [95,96,99]: return "storm"
    return "unknown"

def weather_desc_for_code(code):
    code = int(code)
    return {
        0: "Clear",
        1: "Mostly Clear",
        2: "Partly Cloudy",
        3: "Cloudy",
        45: "Fog",
        48: "Fog",
        51: "Drizzle",
        53: "Drizzle",
        55: "Drizzle",
        56: "Freezing Drizzle",
        57: "Freezing Drizzle",
        61: "Rain",
        63: "Rain",
        65: "Rain",
        66: "Freezing Rain",
        67: "Freezing Rain",
        71: "Snow",
        73: "Snow",
        75: "Snow",
        77: "Snow Grains",
        80: "Rain Showers",
        81: "Rain Showers",
        82: "Rain Showers",
        85: "Snow Showers",
        86: "Snow Showers",
        95: "Thunderstorm",
        96: "Thunderstorm",
        99: "Thunderstorm",
    }.get(code, "N/A")