from fastapi import FastAPI

from app.api.websockets.events import router as events_router
from app.bootstrap import lifespan

app = FastAPI(lifespan=lifespan)

app.include_router(events_router)