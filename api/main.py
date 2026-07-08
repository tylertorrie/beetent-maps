"""Bee Tent Maps — engine API.

Wraps the tested Python engine (maketentgrid.py) so the web app can compute
shelter positions / crew routes without re-porting the heavy geometry to JS.
The desktop app stays authoritative; this is a thin, stateless calculator.

Run locally:
    pip install -r api/requirements.txt
    uvicorn api.main:app --reload --port 8787
"""
import sys
from pathlib import Path
from typing import Any, Dict, List

# Import the engine from the repo root (one level up from api/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import maketentgrid  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

app = FastAPI(title="Bee Tent Maps engine", version="0.1.0")
# TODO: lock allow_origins to the deployed web app's origin before production.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"],
    allow_methods=["POST", "GET"], allow_headers=["*"],
)


class FieldReq(BaseModel):
    field: Dict[str, Any]          # a field's `data` blob (current_field dict)
    use_metric: bool = True


@app.get("/health")
def health():
    return {"ok": True, "engine": "maketentgrid"}


@app.post("/tents")
def tents(req: FieldReq):
    """Planned shelter positions, NW-snake order. Mirrors the desktop grid."""
    positions: List = maketentgrid.get_tent_positions(req.field, use_metric=req.use_metric)
    return {"count": len(positions), "positions": [[la, lo] for la, lo in positions]}


@app.post("/crew-route")
def crew_route(req: FieldReq):
    """Estimated crew travel line (route lat/lon + total metres)."""
    route, total_m = maketentgrid.crew_route(req.field, use_metric=req.use_metric)
    return {"total_m": total_m, "route": [[la, lo] for la, lo in route]}
