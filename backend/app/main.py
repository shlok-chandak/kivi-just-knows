from fastapi import FastAPI

from app.api import ask, events, health, ignored, memories, profile, recall, traces

app = FastAPI(title="Kivi Semantic Memory")

app.include_router(health.router)
app.include_router(ask.router)
app.include_router(events.router)
app.include_router(ignored.router)
app.include_router(memories.router)
app.include_router(profile.router)
app.include_router(recall.router)
app.include_router(traces.router)
