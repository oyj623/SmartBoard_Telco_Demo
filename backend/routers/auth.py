from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from ..auth import create_access_token, get_current_user, verify_password
from ..database import RegionAssignment, User, get_db

router = APIRouter()


def _state_codes(db: Session, user: User) -> list[str]:
    return [
        a.state_code
        for a in db.query(RegionAssignment).filter(RegionAssignment.user_id == user.id).all()
    ]


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    codes = _state_codes(db, user)
    return {
        "access_token": create_access_token(user.id, user.role, codes),
        "token_type": "bearer",
        "role": user.role,
        "name": user.name,
        "username": user.username,
        "state_codes": codes,
    }


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "name": user.name,
        "state_codes": _state_codes(db, user),
    }
