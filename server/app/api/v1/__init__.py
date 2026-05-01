"""V1 API router aggregation.

Collects all v1 routers (auth, admin, health) into a single APIRouter.
"""

from fastapi import APIRouter

from app.api.v1 import admin, auth, health

api_v1 = APIRouter()
api_v1.include_router(auth.router)
api_v1.include_router(admin.router)
api_v1.include_router(health.router)
