from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    ask,
    episodes,
    evaluation,
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
app.include_router(episodes.router)
app.include_router(evaluation.router)
app.include_router(events.router)
app.include_router(ignored.router)
app.include_router(memories.router)
app.include_router(profile.router)
app.include_router(recall.router)
app.include_router(stream.router)
app.include_router(traces.router)


# The built frontend, when there is one. Mounted after every router, so a
# route can never be shadowed by a file that happens to share its name --
# and absent entirely on a checkout that has not run the build, which is
# the normal state of the repository since the output is not committed.
STATIC = Path(__file__).resolve().parent / "static"

if (STATIC / "index.html").is_file():
    app.mount(
        "/assets", StaticFiles(directory=STATIC / "assets"), name="assets"
    )

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    # Icons and the like sit at the build root rather than under /assets.
    # Named individually: a catch-all here would serve any file in the
    # directory to anyone who guessed its name.
    ROOT_FILES = (
        "favicon-32.png",
        "favicon-512.png",
        "apple-touch-icon.png",
    )

    @app.get("/{filename}", include_in_schema=False)
    def root_file(filename: str) -> FileResponse:
        if filename not in ROOT_FILES:
            raise HTTPException(status_code=404)
        return FileResponse(STATIC / filename)


@app.exception_handler(404)
async def not_found(request: Request, exc: Exception) -> Response:
    """A page for people, JSON for everything else.

    A browser that lands on a mistyped address should be told so in the
    app's own voice. A client calling the API should keep getting the
    shape it parses, so the Accept header decides rather than the path.
    """
    page = STATIC / "404.html"
    wants_html = "text/html" in request.headers.get("accept", "")
    if wants_html and page.is_file():
        return FileResponse(page, status_code=404)
    # Keep whatever the route said. "event not found" tells a caller which
    # of the ids they sent was wrong; "Not Found" makes them guess.
    detail = getattr(exc, "detail", None) or "Not Found"
    return JSONResponse({"detail": detail}, status_code=404)
