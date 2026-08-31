from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import Base, engine
from .routers import auth, board, geo

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Nusatel API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(geo.router, prefix="/api/geo", tags=["geo"])

# The SmartBoard-driven AI Board: the model names metrics from a catalog and
# emits typed dashboard commands. See backend/routers/board.py.
app.include_router(board.router, prefix="/api/board", tags=["board"])


class SPAStaticFiles(StaticFiles):
    """
    Static files with a single-page-app fallback.

    React Router owns paths like /board, which exist only in the browser. Plain
    StaticFiles 404s on them, so the app would work when you clicked a link and
    break when you pasted a URL or hit refresh. Anything that is not a real file
    falls through to index.html and lets the router decide.
    """

    async def get_response(self, path: str, scope):
        from starlette.exceptions import HTTPException as StarletteHTTPException

        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


# Mounted last — it is a catch-all. Absent before the first `python start.py`,
# which is fine: the API still serves and Vite's dev server proxies to it.
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists() and any(STATIC_DIR.iterdir()):
    app.mount("/", SPAStaticFiles(directory=STATIC_DIR, html=True), name="static")
