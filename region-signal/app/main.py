import re
import time
from datetime import datetime, timezone
from pathlib import Path

import os
import secrets
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import and_, delete, desc, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

import config
from db import get_db, AppUser, ShortTermMaterialDFU, MidTermMaterialDFU, MarketForecast, UserForecastAccess, UserRegionAccess
from auth import verify_password, hash_password, make_token, current_user, admin_user

app = FastAPI(title="Region Forecast Signal")
STARTED_AT_DT = datetime.fromtimestamp(config.APP_STARTED_AT, tz=timezone.utc)
TYPES = ("SHORT", "MID")
NO_ACCESS = "Insufficient privileges, contact admin"
FCST_NAME = {"SHORT": "ShortTerm", "MID": "MidTerm"}          # value stored in the backing table (FcstType)
GDO = {"SHORT": ShortTermMaterialDFU, "MID": MidTermMaterialDFU}   # POC copies of the GDO-094 DFU tables

def window_open() -> bool:
    return time.time() < config.DEADLINE

# ---------- access helpers ----------
def user_types(db: Session, user_id: str) -> list[str]:
    rows = set(db.scalars(select(UserForecastAccess.forecast_type).where(UserForecastAccess.user_id == user_id)))
    return [t for t in TYPES if t in rows]

def scope_markets(db: Session, ftype: str) -> list[str]:
    """select distinct CountryCode from <ShortTerm|MidTerm>MaterialDFU where ForecastEngineActiveFlag = 1
    (re-read on every call, so new markets appear on their own)."""
    g = GDO[ftype]
    return list(db.scalars(select(g.country_code).where(g.active_flag == 1).distinct().order_by(g.country_code)))

def allowed_regions(db: Session, u: AppUser, ftype: str) -> list[str]:
    """Row-level security: GDO-094 scope for the horizon, limited to the markets this user may see."""
    markets = scope_markets(db, ftype)
    if u.all_regions:
        return markets
    mine = set(db.scalars(select(UserRegionAccess.region_code).where(UserRegionAccess.user_id == u.user_id)))
    return [m for m in markets if m in mine]

def acting_user(db: Session, tok: dict) -> AppUser:
    """Re-check the signed-in user against the database on every call (deactivated users lose access at once)."""
    u = db.scalar(select(AppUser).where(AppUser.user_id == tok["user_id"], AppUser.is_active.is_(True)))
    if not u:
        raise HTTPException(401, "Session expired - please log in again")
    return u

def forecast_user(db: Session, tok: dict, ftype: str) -> AppUser:
    ftype = (ftype or "").upper()
    if ftype not in TYPES:
        raise HTTPException(400, "Unknown forecast type")
    u = acting_user(db, tok)
    if u.is_admin or ftype not in user_types(db, u.user_id):
        raise HTTPException(403, NO_ACCESS)
    return u

# ---------- models ----------
class LoginReq(BaseModel):
    access: str                      # ADMIN | SHORT | MID

ALLOW_DEMO_USER = os.environ.get("ALLOW_DEMO_USER", "").lower() in ("1", "true", "yes")
EMAIL_RE = r"[^@\s]+@[^@\s]+\.[^@\s]+"

def signed_in_identity(request: Request) -> tuple[str | None, str | None]:
    """Who is signed in. Databricks Apps (behind Okta/SSO) adds the signed-in user's email as a request header.
    POC only: with ALLOW_DEMO_USER=true, the X-Demo-User header (sent from ?as=<user_id>) picks a user instead."""
    if ALLOW_DEMO_USER and request.headers.get("x-demo-user"):
        return None, request.headers["x-demo-user"].strip().lower()
    email = request.headers.get("x-forwarded-email") or request.headers.get("x-forwarded-preferred-username")
    return (email.strip().lower() if email else None), None

class RegionChoice(BaseModel):
    region: str
    checked: bool

class SubmitReq(BaseModel):
    type: str
    regions: list[RegionChoice]

# ---------- endpoints ----------
@app.get("/api/status")
def status():
    left = max(0, int(config.DEADLINE - time.time()))
    return {"open": left > 0, "seconds_left": left,
            "started_at": STARTED_AT_DT.isoformat(),
            "deadline": datetime.fromtimestamp(config.DEADLINE, tz=timezone.utc).isoformat()}

@app.post("/api/login")
def login(req: LoginReq, request: Request, db: Session = Depends(get_db)):
    access = req.access.strip().upper()
    if access not in ("ADMIN",) + TYPES:
        raise HTTPException(400, "Select an access type")
    email, demo_uid = signed_in_identity(request)
    if not email and not demo_uid:
        raise HTTPException(401, "We could not identify you from your sign-in. Please contact admin.")
    q = select(AppUser).where(AppUser.is_active.is_(True))
    q = q.where(AppUser.user_id == demo_uid) if demo_uid else q.where(func.lower(AppUser.email) == email)
    chosen, types = None, []
    for u in db.scalars(q.order_by(AppUser.user_id)):             # one email may belong to an admin and a user account
        t = user_types(db, u.user_id)
        if (u.is_admin if access == "ADMIN" else (not u.is_admin and access in t)):
            chosen, types = u, t
            break
    if not chosen:
        raise HTTPException(403, NO_ACCESS)
    u = chosen
    login_time = datetime.now(timezone.utc).isoformat()
    user = {"user_id": u.user_id, "user_name": u.user_name, "is_admin": u.is_admin, "login_time": login_time}
    return {"token": make_token(user), **user, "email": u.email, "view": access, "types": types}

@app.get("/api/regions")
def regions(type: str = Query(...), user: dict = Depends(current_user), db: Session = Depends(get_db)):
    """The user's markets for one forecast type, ALWAYS in the default state (all checked = TRUE).
    `last` = this user's own latest submission during THIS app run (never carried over to the next run)."""
    u = forecast_user(db, user, type)
    ftype = type.upper()
    mine = allowed_regions(db, u, ftype)
    # this user's own most recent submission of this run (a user never sees another user's rows here)
    mine_q = [MarketForecast.user_id == u.user_id, MarketForecast.fcst_type == FCST_NAME[ftype],
              MarketForecast.generation_timestamp >= STARTED_AT_DT]
    last_ts = db.scalar(select(func.max(MarketForecast.generation_timestamp)).where(*mine_q))
    rows = db.execute(select(MarketForecast).where(*mine_q, MarketForecast.generation_timestamp == last_ts)).scalars().all() if last_ts else []
    order = {m: i for i, m in enumerate(mine)}
    last = [{"market": r.market, "is_current_week_fcst": r.is_current_week_fcst, "user_id": r.user_id,
             "generation_timestamp": r.generation_timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
             "fcst_type": r.fcst_type} for r in sorted(rows, key=lambda r: order.get(r.market, 999))]
    return {"regions": [{"region": m, "checked": True} for m in mine], "type": ftype, "last": last}

@app.post("/api/submit")
def submit(req: SubmitReq, user: dict = Depends(current_user), db: Session = Depends(get_db)):
    if not window_open():
        raise HTTPException(403, "The submission window is closed. Updates are no longer allowed.")
    ftype = req.type.upper()
    now = datetime.now(timezone.utc)
    with db.begin():                                               # one transaction, rolled back on any error
        u = forecast_user(db, user, ftype)
        db.execute(text("SELECT pg_advisory_xact_lock(727001)"))   # serialise concurrent submits
        active = set(allowed_regions(db, u, ftype))                # only the markets this user may see
        sent = {r.region: r.checked for r in req.regions}
        if set(sent) != active:
            raise HTTPException(400, "The market list has changed - please reload the page and try again")
        for market, checked in sent.items():                       # SCD2: expire the old row, insert the new current row
            db.execute(update(MarketForecast).where(MarketForecast.market == market,
                                                    MarketForecast.fcst_type == FCST_NAME[ftype],
                                                    MarketForecast.latest_flag == "Y").values(latest_flag="N"))
            db.add(MarketForecast(market=market, is_current_week_fcst=checked, generation_timestamp=now,
                                  user_id=u.user_id, fcst_type=FCST_NAME[ftype], latest_flag="Y"))
    return {"ok": True, "submitted_time": now.isoformat(), "count": len(sent),
            "false_count": sum(1 for v in sent.values() if not v)}

# ---------- admin: market scope (edits the POC copies of the GDO-094 DFU tables) ----------
class MarketReq(BaseModel):
    region_code: str
    st_active: bool = True
    mt_active: bool = True

@app.get("/api/admin/regions")
def admin_list(user: dict = Depends(admin_user), db: Session = Depends(get_db)):
    st = dict(db.execute(select(ShortTermMaterialDFU.country_code, func.max(ShortTermMaterialDFU.active_flag)).group_by(ShortTermMaterialDFU.country_code)).all())
    mt = dict(db.execute(select(MidTermMaterialDFU.country_code, func.max(MidTermMaterialDFU.active_flag)).group_by(MidTermMaterialDFU.country_code)).all())
    return [{"region_code": m, "st_active": st.get(m) == 1, "mt_active": mt.get(m) == 1} for m in sorted(set(st) | set(mt))]

@app.post("/api/admin/regions")
def admin_add(req: MarketReq, user: dict = Depends(admin_user), db: Session = Depends(get_db)):
    code = req.region_code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{1,10}", code):
        raise HTTPException(400, "Market code: 1-10 letters/digits (e.g. FR)")
    with db.begin():
        for g, active in ((ShortTermMaterialDFU, req.st_active), (MidTermMaterialDFU, req.mt_active)):
            n = db.execute(update(g).where(g.country_code == code).values(active_flag=1 if active else 0)).rowcount
            if not n and active:                                   # new market: add one DFU row
                db.add(g(material_code=f"MAT-{code}-1", country_code=code, active_flag=1))
    return {"ok": True, "region_code": code}

@app.delete("/api/admin/regions/{code}")
def admin_delete(code: str, user: dict = Depends(admin_user), db: Session = Depends(get_db)):
    with db.begin():
        n = sum(db.execute(delete(g).where(g.country_code == code)).rowcount for g in (ShortTermMaterialDFU, MidTermMaterialDFU))
    if not n:
        raise HTTPException(404, "Market not found")
    return {"ok": True}


# ---------- admin: manage users (soft delete keeps audit history) ----------
class UserReq(BaseModel):
    user_id: str
    user_name: str | None = None
    email: str | None = None
    types: list[str] = []
    all_regions: bool = False
    regions: list[str] = []

@app.get("/api/admin/users")
def admin_users(user: dict = Depends(admin_user), db: Session = Depends(get_db)):
    types: dict[str, list[str]] = {}
    for r in db.execute(select(UserForecastAccess.user_id, UserForecastAccess.forecast_type)):
        types.setdefault(r.user_id, []).append(r.forecast_type)
    regs: dict[str, list[str]] = {}
    for r in db.execute(select(UserRegionAccess.user_id, UserRegionAccess.region_code).order_by(UserRegionAccess.region_code)):
        regs.setdefault(r.user_id, []).append(r.region_code)
    out = []
    for u in db.scalars(select(AppUser).where(AppUser.is_active.is_(True)).order_by(AppUser.is_admin.desc(), AppUser.user_id)):
        out.append({"user_id": u.user_id, "user_name": u.user_name, "email": u.email, "is_admin": u.is_admin, "all_regions": u.all_regions,
                    "types": [t for t in TYPES if t in types.get(u.user_id, [])], "regions": regs.get(u.user_id, [])})
    return out

@app.post("/api/admin/users")
def admin_save_user(req: UserReq, user: dict = Depends(admin_user), db: Session = Depends(get_db)):
    uid = req.user_id.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{2,30}", uid):
        raise HTTPException(400, "User ID: 2-30 letters, digits, . _ -")
    ftypes = sorted({t.upper() for t in req.types})
    if not ftypes or any(t not in TYPES for t in ftypes):
        raise HTTPException(400, "Select Short-Term, Mid-Term or both")
    with db.begin():
        existing = db.get(AppUser, uid)
        if existing and existing.is_admin:
            raise HTTPException(400, "The admin user cannot be changed here")
        regions = sorted({r.strip().upper() for r in req.regions})
        if not req.all_regions:
            if not regions:
                raise HTTPException(400, "Select at least one region, or choose All regions")
            valid = set(db.scalars(select(ShortTermMaterialDFU.country_code))) | set(db.scalars(select(MidTermMaterialDFU.country_code)))
            if set(regions) - valid:
                raise HTTPException(400, "Unknown region: " + ", ".join(sorted(set(regions) - valid)))
        email = (req.email or "").strip().lower()
        if not email and not existing:
            raise HTTPException(400, "Email is required (it is how the user is recognised at sign-in)")
        if email:
            if not re.fullmatch(EMAIL_RE, email):
                raise HTTPException(400, "Enter a valid email address")
            clash = db.scalar(select(AppUser.user_id).where(func.lower(AppUser.email) == email, AppUser.is_admin.is_(False),
                                                            AppUser.is_active.is_(True), AppUser.user_id != uid))
            if clash:
                raise HTTPException(400, f"That email already belongs to user '{clash}'")
        name = (req.user_name or "").strip() or uid.capitalize()
        if existing:
            existing.user_name, existing.all_regions, existing.is_active = name, req.all_regions, True
            if email:
                existing.email = email
        else:
            db.add(AppUser(user_id=uid, user_name=name, is_admin=False, all_regions=req.all_regions,
                           email=email, password_hash=hash_password(secrets.token_hex(16)), is_active=True))   # password unused: sign-in is by identity
        db.flush()
        db.execute(delete(UserForecastAccess).where(UserForecastAccess.user_id == uid))
        db.execute(delete(UserRegionAccess).where(UserRegionAccess.user_id == uid))
        db.add_all([UserForecastAccess(user_id=uid, forecast_type=t) for t in ftypes])
        if not req.all_regions:
            db.add_all([UserRegionAccess(user_id=uid, region_code=r) for r in regions])
    return {"ok": True, "user_id": uid, "created": existing is None}

@app.delete("/api/admin/users/{uid}")
def admin_delete_user(uid: str, user: dict = Depends(admin_user), db: Session = Depends(get_db)):
    uid = uid.strip().lower()
    with db.begin():
        u = db.get(AppUser, uid)
        if not u or not u.is_active:
            raise HTTPException(404, "User not found")
        if u.is_admin:
            raise HTTPException(400, "The admin user cannot be removed")
        u.is_active = False                                   # keep the row: forecast history references the user
    return {"ok": True}

# ---------- static React build ----------
STATIC = Path(__file__).parent / "static"
if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")
    @app.get("/{path:path}")
    def spa(path: str):
        f = (STATIC / path).resolve()
        if path and f.is_file() and STATIC.resolve() in f.parents:   # e.g. /logo.png
            return FileResponse(f)
        return FileResponse(STATIC / "index.html")
