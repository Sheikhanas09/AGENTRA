import os

from dotenv import load_dotenv
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

load_dotenv()

# ── bcrypt ki jagah sha256_crypt use karo ──
pwd_context = CryptContext(
    schemes=["sha256_crypt"],
    deprecated="auto"
)

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

# ══════════════════════════════════════════════
# JWT ka secret
# ══════════════════════════════════════════════
# Pehle yeh do jagah likha tha: yahan (asal, istemal hone wala) aur
# `app/config.py` mein — jo kabhi import hi nahi hota tha. Koi config.py
# mein secret badal kar samajhta ke JWT ka key badal gaya, halanke kuch
# nahi hota. Ab wo file hata di gayi hai, secret sirf yahan se aata hai.
#
# `.env` mein SECRET_KEY rakhein to wohi chalega. Na rakhein to neeche
# wali default value chalti hai — magar startup par warning aati hai,
# taake yeh baat chup chaap na guzar jaye.
_FALLBACK_SECRET = "your-secret-key-change-this-in-production"

SECRET_KEY = os.getenv("SECRET_KEY", "").strip() or _FALLBACK_SECRET
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

if SECRET_KEY == _FALLBACK_SECRET:
    print(
        "[security] WARNING: SECRET_KEY .env mein set nahi — default key "
        "chal rahi hai. Production se pehle `.env` mein SECRET_KEY dalein "
        "(dhyan rahe: badalte hi sab users ek dafa logout ho jayenge)."
    )

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token invalid or expired",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("user_id")
        role: str = payload.get("role")
        if user_id is None:
            raise credentials_exception
        return {"user_id": user_id, "role": role}
    except JWTError:
        raise credentials_exception