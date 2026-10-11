from fastapi import FastAPI

from app.api.routes.route_planning import router as route_planning_router
from app.api.websockets.events import router as events_router
from app.bootstrap import lifespan

app = FastAPI(lifespan=lifespan)

app.include_router(events_router)
app.include_router(route_planning_router)