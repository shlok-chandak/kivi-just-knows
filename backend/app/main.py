from fastapi import FastAPI

from app.api import ask, events, health, profile, recall

app = FastAPI(title="Kivi Semantic Memory")

app.include_router(health.router)
app.include_router(ask.router)
app.include_router(events.router)
app.include_router(profile.router)
app.include_router(recall.router)
