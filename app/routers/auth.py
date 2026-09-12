from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import hashlib

from app.core.database import get_db
from app.core.deps import get_current_shop
from app.core.security import hash_pin, verify_pin, create_access_token, create_refresh_token, decode_token
from app.models.models import Shop, RefreshToken
from app.schemas.schemas import ShopRegister, ShopLogin, TokenRefresh, TokenResponse, ShopResponse, ShopInfoResponse


router = APIRouter(prefix="/auth", tags=["auth"])


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("/register", response_model=ShopResponse, status_code=status.HTTP_201_CREATED)
async def register(shop_data: ShopRegister, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Shop).where(Shop.phone_number == shop_data.phone_number))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phone number already registered"
        )

    shop = Shop(
        owner_name=shop_data.owner_name,
        phone_number=shop_data.phone_number,
        pin_hash=hash_pin(shop_data.pin)
    )
    db.add(shop)
    await db.flush()

    access_token = create_access_token(str(shop.id))
    refresh_token = create_refresh_token(str(shop.id))

    refresh_token_record = RefreshToken(
        shop_id=shop.id,
        token_hash=hash_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )
    db.add(refresh_token_record)

    return ShopResponse(
        shop_id=shop.id,
        access_token=access_token,
        refresh_token=refresh_token
    )


@router.post("/login", response_model=TokenResponse)
async def login(login_data: ShopLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Shop).where(Shop.phone_number == login_data.phone_number))
    shop = result.scalar_one_or_none()

    if shop is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or PIN"
        )

    if shop.locked_until and shop.locked_until > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Account locked. Try again after {shop.locked_until}"
        )

    if not verify_pin(login_data.pin, shop.pin_hash):
        shop.failed_login_count += 1
        if shop.failed_login_count >= 5:
            shop.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or PIN"
        )

    shop.failed_login_count = 0
    shop.locked_until = None

    access_token = create_access_token(str(shop.id))
    refresh_token = create_refresh_token(str(shop.id))

    refresh_token_record = RefreshToken(
        shop_id=shop.id,
        token_hash=hash_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )
    db.add(refresh_token_record)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_data: TokenRefresh, db: AsyncSession = Depends(get_db)):
    payload = decode_token(refresh_data.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )

    token_hash_value = hash_token(refresh_data.refresh_token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash_value,
            RefreshToken.revoked_at.is_(None)
        )
    )
    refresh_record = result.scalar_one_or_none()

    if refresh_record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked or not found"
        )

    if refresh_record.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired"
        )

    refresh_record.revoked_at = datetime.now(timezone.utc)

    new_access_token = create_access_token(str(refresh_record.shop_id))
    new_refresh_token = create_refresh_token(str(refresh_record.shop_id))

    new_refresh_record = RefreshToken(
        shop_id=refresh_record.shop_id,
        token_hash=hash_token(new_refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30)
    )
    db.add(new_refresh_record)

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(refresh_data: TokenRefresh, db: AsyncSession = Depends(get_db)):
    token_hash_value = hash_token(refresh_data.refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash_value)
    )
    refresh_record = result.scalar_one_or_none()

    if refresh_record:
        refresh_record.revoked_at = datetime.now(timezone.utc)


@router.get("/me", response_model=ShopInfoResponse)
async def get_me(current_shop: Shop = Depends(get_current_shop)):
    return ShopInfoResponse(
        shop_id=current_shop.id,
        owner_name=current_shop.owner_name,
        phone_number=current_shop.phone_number
    )

