from fastapi import FastAPI
from app.routers import auth

app = FastAPI(title="Fitness Agent API", version="0.1.0")

app.include_router(auth.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
