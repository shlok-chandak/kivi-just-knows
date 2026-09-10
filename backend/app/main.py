from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    ask,
    events,
    health,
    ignored,
    memories,
    profile,
    recall,
    stream,
    traces,
)

app = FastAPI(title="Kivi Semantic Memory")

# A frontend served by its own dev server is a different origin. Local hosts
# only: this is a single-user system on a laptop, and a wildcard would let
# any page a browser opens read the memory.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(ask.router)
app.include_router(events.router)
app.include_router(ignored.router)
app.include_router(memories.router)
app.include_router(profile.router)
app.include_router(recall.router)
app.include_router(stream.router)
app.include_router(traces.router)
