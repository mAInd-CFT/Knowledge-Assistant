from fastapi import APIRouter
from app.core.client_auth import issue_client_token

router = APIRouter(prefix="/client", tags=["client"])


@router.post("/bootstrap")
def bootstrap_client():
    """Issue a random signed browser identity; not corporate authentication."""
    owner_id, token = issue_client_token()
    return {"client_id": owner_id, "client_token": token}
