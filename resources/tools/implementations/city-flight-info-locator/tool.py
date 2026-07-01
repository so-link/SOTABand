import json
import math
import time
import urllib.request
import urllib.parse
import urllib.error
import socket
from typing import Any, Dict, List

__all__ = ["execute"]

# Cache for OpenSky airport data
_AIRPORTS_CACHE = None

def _fetch_json(url: str, timeout: int = 15) -> dict:
    """Fetch JSON from URL and return parsed dict/list."""
    req = urllib.request.Request(url, headers={"User-Agent": "city-flight-info-locator/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body.decode())

def _get_airports() -> List[dict]:
    """Fetch the full airport list from OpenSky API once and cache it."""
    global _AIRPORTS_CACHE
    if _AIRPORTS_CACHE is None:
        url = "https://opensky-network.org/api/airports/"
        _AIRPORTS_CACHE = _fetch_json(url)
    return _AIRPORTS_CACHE

def _haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def _bbox_from_circle(lat, lon, radius_km):
    """
    Compute a bounding box approximating a circle with given radius (km) around a point.
    Returns (min_lat, max_lat, min_lon, max_lon).
    """
    one_deg_lat_km = 111.32
    one_deg_lon_km = 111.32 * math.cos(math.radians(lat))
    dlat = radius_km / one_deg_lat_km
    dlon = radius_km / one_deg_lon_km if one_deg_lon_km != 0 else 0
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon

def _get_flights(icao: str, limit: int) -> List[dict]:
    """
    Fetch upcoming departure flights from an airport using OpenSky API.
    Returns list of flight dicts (trimmed to limit).
    """
    # Time window: now to 24h later
    now = int(time.time())
    end = now + 86400
    url = f"https://opensky-network.org/api/flights/departure?airport={icao}&begin={now}&end={end}"
    data = _fetch_json(url)
    # The API returns a list of flights; we'll sort by firstSeen (departure time proxy)
    flights = sorted(data, key=lambda f: f.get("firstSeen", 0))
    # Trim to limit
    flights = flights[:limit]
    # Simplify to required fields
    simplified = []
    for f in flights:
        simplified.append({
            "callsign": f.get("callsign", "").strip(),
            "departure_airport": f.get("estDepartureAirport", ""),
            "arrival_airport": f.get("estArrivalAirport", ""),
            "first_seen": f.get("firstSeen", 0),
            "last_seen": f.get("lastSeen", 0),
            "number": f.get("number", "")
        })
    return simplified

def _get_nearby_aircraft(lat: float, lon: float, radius_km: float, limit: int = 200) -> List[dict]:
    """
    Fetch nearby aircraft state vectors from OpenSky API within a bounding box,
    then filter by exact circle distance. Return list of aircraft data.
    """
    lamin, lamax, lomin, lomax = _bbox_from_circle(lat, lon, radius_km)
    url = f"https://opensky-network.org/api/states/all?lamin={lamin:.4f}&lomin={lomin:.4f}&lamax={lamax:.4f}&lomax={lomax:.4f}"
    data = _fetch_json(url)
    states = data.get("states", [])
    aircraft_list = []
    for s in states:
        if len(s) < 6:
            continue
        icao24 = s[0]
        callsign = s[1].strip() if s[1] else ""
        origin_country = s[2]
        time_position = s[3]
        last_contact = s[4]
        lon_ac = s[5]
        lat_ac = s[6]
        baro_altitude = s[7]
        on_ground = s[8]
        velocity = s[9]
        true_track = s[10]
        vertical_rate = s[11]
        # Basic sanity check
        if lat_ac is None or lon_ac is None:
            continue
        # Circle filter
        dist = _haversine(lat, lon, lat_ac, lon_ac)
        if dist <= radius_km:
            aircraft_list.append({
                "icao24": icao24,
                "callsign": callsign,
                "origin_country": origin_country,
                "longitude": lon_ac,
                "latitude": lat_ac,
                "baro_altitude": baro_altitude,
                "on_ground": on_ground,
                "velocity": velocity,
                "true_track": true_track,
                "vertical_rate": vertical_rate,
                "distance_km": round(dist, 2)
            })
    # Sort by distance
    aircraft_list.sort(key=lambda x: x["distance_km"])
    return aircraft_list[:limit]

def execute(**kwargs) -> Dict[str, Any]:
    """
    City flight info and nearby aircraft locator using only OpenSky Network API.

    Args:
        city (str): City name, e.g. "Beijing", "Shanghai", "Chengdu". Required.
        radius_km (int or float): Radius in km for nearby aircraft search. Default 50.
        flight_limit (int): Maximum number of flights to return. Default 20.
        airport_code (str): Deprecated, kept for compatibility; ignored.

    Returns:
        dict: {"status": "success"|"failed", "message": str, "data": {...}}
    """
    try:
        # --- input validation ---
        if "city" not in kwargs or not kwargs["city"]:
            return {"status": "failed", "message": "Missing required parameter: city", "data": {}}
        city = str(kwargs["city"]).strip()
        if not city:
            return {"status": "failed", "message": "Parameter 'city' must be non-empty", "data": {}}

        radius_km = float(kwargs.get("radius_km", 50))
        if radius_km <= 0:
            return {"status": "failed", "message": "radius_km must be positive", "data": {}}

        flight_limit = int(kwargs.get("flight_limit", 20))
        if flight_limit < 1:
            return {"status": "failed", "message": "flight_limit must be at least 1", "data": {}}

        # airport_code is ignored; we now rely on OpenSky airport database

        # --- resolve airport using OpenSky API ---
        all_airports = _get_airports()
        city_lower = city.lower()
        # First try exact city match (case-insensitive)
        matched = [a for a in all_airports if a.get("city", "").lower() == city_lower]
        if not matched:
            # Fall back to partial match
            matched = [a for a in all_airports if city_lower in a.get("city", "").lower()]
        if not matched:
            return {"status": "failed", "message": f"No airport found for city '{city}' via OpenSky", "data": {}}

        # Use the first matching airport
        airport = matched[0]
        icao = airport.get("icao", "")
        if not icao:
            return {"status": "failed", "message": f"Invalid airport data for city '{city}'", "data": {}}

        # Coordinates from airport location
        location = airport.get("location", {})
        lat = location.get("latitude")
        lon = location.get("longitude")
        if lat is None or lon is None:
            return {"status": "failed", "message": f"Missing coordinates for airport {icao}", "data": {}}

        # IATA is not available in OpenSky airport list; leave empty
        iata = ""

        # --- fetch flights ---
        flights = _get_flights(icao, flight_limit)

        # --- fetch nearby aircraft ---
        nearby_aircraft = _get_nearby_aircraft(lat, lon, radius_km)

        return {
            "status": "success",
            "message": f"Data retrieved for city '{city}', airport ICAO: {icao}",
            "data": {
                "city": city,
                "latitude": lat,
                "longitude": lon,
                "airport": {"iata": iata, "icao": icao},
                "flights": flights,
                "nearby_aircraft": nearby_aircraft
            }
        }

    except urllib.error.URLError as e:
        # Friendly message for network-related errors
        return {
            "status": "failed",
            "message": "Network error: [Errno 101] Network is unreachable. Please check your internet connection.",
            "data": {}
        }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"Error processing request: {str(e)}",
            "data": {}
        }