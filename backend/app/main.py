from fastapi import FastAPI

from app.api import health

app = FastAPI(title="Kivi Semantic Memory")

app.include_router(health.router)
