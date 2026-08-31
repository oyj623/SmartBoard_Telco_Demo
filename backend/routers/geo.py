"""
Map geometry.

The choropleth renderer fetches the state polygons from here and joins them to
whatever result is on the panel, on the `state_code` property. The model never
touches this endpoint — it names `map_regions` and a metric, and every decision
about geometry is made in code we own.

The file is served whole rather than filtered per role. State *outlines* are
public information — they are on every map ever printed — and the numbers
painted onto them are what the tenancy predicate protects. A regional manager
sees all sixteen shapes and data for four of them, which is also the honest
picture of what they are allowed to know.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..auth import get_current_user
from ..database import User

router = APIRouter()

GEOJSON = Path(__file__).resolve().parent.parent.parent / "data" / "my_states.geojson"


@router.get("/states")
def states(_: User = Depends(get_current_user)):
    if not GEOJSON.exists():
        raise HTTPException(404, "my_states.geojson is missing — run `python data/build_geo.py`")
    return FileResponse(
        GEOJSON,
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )
