import hashlib, hmac, os, base64
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Depends, Header, HTTPException
from config import SECRET_KEY, TOKEN_MAX_AGE

_ser = URLSafeTimedSerializer(SECRET_KEY, salt="region-signal-session")

def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"

def verify_password(pw: str, stored: str) -> bool:
    try:
        _, s, d = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(s), 200_000)
        return hmac.compare_digest(dk, base64.b64decode(d))
    except Exception:
        return False

def make_token(user: dict) -> str:
    return _ser.dumps(user)

# NB: custom header on purpose - the Databricks Apps proxy intercepts the standard Authorization header
def current_user(x_app_token: str = Header(default="")) -> dict:
    if not x_app_token:
        raise HTTPException(401, "Not logged in (no session token received)")
    try:
        return _ser.loads(x_app_token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "Session expired - please log in again")

def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(403, "Admin only")
    return user