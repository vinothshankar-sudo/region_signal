import os, time
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config
from db import connect
import re
from auth import verify_password, make_token, current_user, admin_user

app = FastAPI(title="Region Forecast Signal")
STARTED_AT_DT = datetime.fromtimestamp(config.APP_STARTED_AT, tz=timezone.utc)

def window_open() -> bool:
    return time.time() < config.DEADLINE

# ---------- models ----------
class LoginReq(BaseModel):
    user_id: str
    password: str

class RegionChoice(BaseModel):
    region: str
    checked: bool

class SubmitReq(BaseModel):
    regions: list[RegionChoice]

# ---------- endpoints ----------
@app.get("/api/status")
def status():
    left = max(0, int(config.DEADLINE - time.time()))
    return {"open": left > 0, "seconds_left": left,
            "started_at": STARTED_AT_DT.isoformat(),
            "deadline": datetime.fromtimestamp(config.DEADLINE, tz=timezone.utc).isoformat()}

@app.post("/api/login")
def login(req: LoginReq):
    with connect() as c:
        u = c.execute("SELECT user_id, user_name, is_admin, password_hash FROM app_users "
                      "WHERE user_id=%s AND is_active", (req.user_id.strip(),)).fetchone()
    if not u or not verify_password(req.password, u["password_hash"]):
        raise HTTPException(401, "Invalid user ID or password")
    login_time = datetime.now(timezone.utc).isoformat()
    user = {"user_id": u["user_id"], "user_name": u["user_name"], "is_admin": u["is_admin"], "login_time": login_time}
    return {"token": make_token(user), **user}

@app.get("/api/regions")
def regions(user: dict = Depends(current_user)):
    """Active regions. Default = all checked. If someone already submitted during THIS app run,
    show that last submission (state is kept while up, never carried over to the next run)."""
    with connect() as c:
        rows = c.execute("""
            SELECT m.region_code AS region, m.region_name,
                   COALESCE(f.check_status, TRUE) AS checked
            FROM master_region m
            LEFT JOIN region_forecast f
                   ON f.region = m.region_code AND f.latest_flag='Y' AND f.submitted_time >= %s
            WHERE m.is_active ORDER BY m.sort_order, m.region_code""", (STARTED_AT_DT,)).fetchall()
        last = c.execute("SELECT submitted_time AS t, user_name AS u FROM region_forecast "
                         "WHERE latest_flag='Y' AND submitted_time >= %s ORDER BY submitted_time DESC LIMIT 1",
                         (STARTED_AT_DT,)).fetchone() or {"t": None, "u": None}
    return {"regions": rows, "last_submitted_at": last["t"], "last_submitted_by": last["u"]}

@app.post("/api/submit")
def submit(req: SubmitReq, user: dict = Depends(current_user)):
    if not window_open():
        raise HTTPException(403, "The submission window is closed. Updates are no longer allowed.")
    now = datetime.now(timezone.utc)
    today = now.astimezone(config.BUSINESS_TZ).date()
    login_time = datetime.fromisoformat(user["login_time"])
    with connect() as c, c.transaction():
        c.execute("SELECT pg_advisory_xact_lock(727001)")            # serialise concurrent submits
        active = {r["region_code"] for r in
                  c.execute("SELECT region_code FROM master_region WHERE is_active").fetchall()}
        sent = {r.region: r.checked for r in req.regions}
        if set(sent) != active:
            raise HTTPException(400, "Submission must contain exactly the active regions")
        changed = []
        for region, checked in sent.items():
            cur = c.execute("SELECT id, check_status, submitted_date FROM region_forecast "
                            "WHERE region=%s AND latest_flag='Y' FOR UPDATE", (region,)).fetchone()
            if cur and cur["check_status"] == checked:
                continue                                  # unchanged -> leave the current row as it is
            changed.append(region)
            if cur and cur["submitted_date"] == today:
                # changed again on the same (Central) day -> overwrite today's row
                c.execute("""UPDATE region_forecast SET user_id=%s, user_name=%s, login_time=%s,
                             submitted_time=%s, check_status=%s WHERE id=%s""",
                          (user["user_id"], user["user_name"], login_time, now, checked, cur["id"]))
            else:
                # changed -> SCD2: expire old row, insert new current row
                if cur:
                    c.execute("UPDATE region_forecast SET latest_flag='N' WHERE id=%s", (cur["id"],))
                c.execute("""INSERT INTO region_forecast
                             (user_id,user_name,login_time,submitted_time,submitted_date,region,check_status,latest_flag)
                             VALUES (%s,%s,%s,%s,%s,%s,%s,'Y')""",
                          (user["user_id"], user["user_name"], login_time, now, today, region, checked))
    return {"ok": True, "submitted_time": now.isoformat(), "changed": changed, "unchanged": len(sent) - len(changed)}

# ---------- admin: manage regions (soft delete keeps history intact) ----------
class RegionReq(BaseModel):
    region_code: str
    region_name: str | None = None

@app.get("/api/admin/regions")
def admin_list(user: dict = Depends(admin_user)):
    with connect() as c:
        return c.execute("SELECT region_code, region_name, sort_order FROM master_region "
                         "WHERE is_active ORDER BY sort_order, region_code").fetchall()

@app.post("/api/admin/regions")
def admin_add(req: RegionReq, user: dict = Depends(admin_user)):
    code = req.region_code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{1,10}", code):
        raise HTTPException(400, "Region code: 1-10 letters/digits (e.g. FR)")
    with connect() as c:
        c.execute("""INSERT INTO master_region (region_code, region_name, sort_order, created_by)
                     VALUES (%s,%s,(SELECT COALESCE(max(sort_order),0)+10 FROM master_region),%s)
                     ON CONFLICT (region_code) DO UPDATE SET is_active=TRUE,
                     region_name=COALESCE(EXCLUDED.region_name, master_region.region_name)""",
                  (code, (req.region_name or code).strip(), user["user_id"]))
        c.commit()
    return {"ok": True, "region_code": code}

@app.delete("/api/admin/regions/{code}")
def admin_delete(code: str, user: dict = Depends(admin_user)):
    with connect() as c:
        n = c.execute("UPDATE master_region SET is_active=FALSE WHERE region_code=%s AND is_active", (code,)).rowcount
        c.commit()
    if not n:
        raise HTTPException(404, "Region not found")
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
