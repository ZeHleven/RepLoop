from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.services.demo import create_visit, require_demo, reset_visit, session_response, visit_for_user

router = APIRouter(prefix="/demo", tags=["demo"], dependencies=[Depends(require_demo)])


class DemoSessionRequest(BaseModel):
    # Browser-generated cryptographic secret; only its hash is stored server-side.
    request_key: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/sessions", status_code=201)
async def create_session(body: DemoSessionRequest, request: Request, response: Response,
                         db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return await create_visit(db, body.request_key, request.client.host if request.client else "unknown")


@router.get("/session")
async def get_session(response: Response, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    result = session_response(await visit_for_user(db, user.id))
    result.pop("access_token")
    result.pop("refresh_token")
    return result


@router.post("/reset")
async def reset_session(response: Response, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return await reset_visit(db, user.id)
