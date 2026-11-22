from fastapi import APIRouter
from .service import fetch, publish

router = APIRouter()


@router.get('/api/release/{run_id}')
def conclusion(run_id: str):
    return fetch(run_id)


@router.post('/api/release/{run_id}')
def regenerate(run_id: str):
    return publish(run_id)
