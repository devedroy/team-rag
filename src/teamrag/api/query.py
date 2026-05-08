"""Query endpoint — Phase 0 stub (no vector search logic)."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5


class QueryResponse(BaseModel):
    chunks: list = []
    total: int = 0


@router.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    # Phase 0: unconditionally return empty results
    return QueryResponse(chunks=[], total=0)
