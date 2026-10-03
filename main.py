"""
CAM Tracking Kiosk server.

Run with ``python3 main.py`` (or ``uvicorn main:app``). Endpoints live in
``app/routers``; domain logic in ``business_logic.py`` and ``entry_parser.py``.
"""
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import config
from app.limiter import limiter
from app.routers import analytics, auth, cam_items, jobs, moves, priority, system
from database import init_database, migrate_database

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('cam_tracking.log'),
        logging.StreamHandler(sys.stdout)
    ]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and migrate the database on startup."""
    init_database()
    migrate_database()
    yield


app = FastAPI(title="CAM Tracking Kiosk API", version=system.APP_VERSION, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

for module in (auth, jobs, cam_items, moves, priority, analytics, system):
    app.include_router(module.router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/static/index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
