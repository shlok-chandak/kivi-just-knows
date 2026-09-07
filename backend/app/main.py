from fastapi import FastAPI

from app.api import events, health

app = FastAPI(title="Kivi Semantic Memory")

app.include_router(health.router)
app.include_router(events.router)
