from fastapi import FastAPI, Depends
from app.routers import auth, profile, exercises, foods, chat, workouts
from app.deps import get_current_user
from app.models.user import User

app = FastAPI(title="Fitness Agent API", version="0.1.0")

app.include_router(auth.router, prefix="/api/v1")
app.include_router(profile.router, prefix="/api/v1")
app.include_router(exercises.router, prefix="/api/v1")
app.include_router(foods.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(workouts.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/v1/me")
async def me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "email": current_user.email}
