"""Where GAIA's truck trades. Weather and events are looked up around each pitch.

ASSUMPTION, to confirm with the owner: one pitch at Broadway Market, Hackney (the seed data has a market-day
pitch there). Add the real pitches here; everything downstream loops over PITCHES.
"""
from typing import List, TypedDict


class Pitch(TypedDict):
    id: str  # slug, stored with each weather day / event
    name: str
    lat: float
    lon: float


PITCHES: List[Pitch] = [
    {"id": "broadway-market", "name": "Broadway Market, Hackney", "lat": 51.5367, "lon": -0.0614},
]
