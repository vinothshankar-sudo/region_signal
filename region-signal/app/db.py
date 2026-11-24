"""SQLAlchemy engine + ORM models for Lakebase Postgres.

In Databricks Apps the PG* env vars come from the bound Postgres resource and the password is a
short-lived OAuth token, fetched fresh each time the pool opens a connection.
Locally set DATABASE_URL (or PGPASSWORD together with the PG* vars)."""
import os
from datetime import date, datetime

from sqlalchemy import (SmallInteger, BigInteger, Boolean, CHAR, Date, DateTime, ForeignKey, Integer, String,
                        create_engine, event, func)
from sqlalchemy.engine import URL
from sqlalchemy import PrimaryKeyConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

_ws = None


def _oauth_token() -> str:
    global _ws
    from databricks.sdk import WorkspaceClient
    _ws = _ws or WorkspaceClient()
    return _ws.config.oauth_token().access_token      # SDK caches and refreshes it


def _make_engine():
    if os.getenv("DATABASE_URL"):                      # local development
        url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
        return create_engine(url, pool_pre_ping=True)
    url = URL.create("postgresql+psycopg",
                     username=os.environ["PGUSER"], host=os.environ["PGHOST"],
                     port=int(os.getenv("PGPORT", "5432")), database=os.environ["PGDATABASE"],
                     query={"sslmode": os.getenv("PGSSLMODE", "require")})
    eng = create_engine(url, pool_pre_ping=True, pool_recycle=1800, pool_size=5, max_overflow=5)

    @event.listens_for(eng, "do_connect")
    def _inject_password(dialect, conn_rec, cargs, cparams):
        cparams["password"] = os.getenv("PGPASSWORD") or _oauth_token()

    return eng


engine = _make_engine()
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    """FastAPI dependency: one session per request."""
    with SessionLocal() as session:
        yield session


class Base(DeclarativeBase):
    pass


class AppUser(Base):                                   # table 3: login
    __tablename__ = "app_users"
    user_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    all_regions: Mapped[bool] = mapped_column(Boolean, default=False)
    password_hash: Mapped[str] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MasterRegion(Base):                              # table 2: master region list
    __tablename__ = "master_region"
    region_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    region_name: Mapped[str | None] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(50))


class RegionForecast(Base):                            # table 1: SCD type 2
    __tablename__ = "region_forecast"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(50))
    user_name: Mapped[str] = mapped_column(String(200))
    login_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_date: Mapped[date] = mapped_column(Date)
    region: Mapped[str] = mapped_column(String(10), ForeignKey("master_region.region_code"))
    forecast_type: Mapped[str] = mapped_column(String(10), default="SHORT")
    check_status: Mapped[bool] = mapped_column(Boolean)
    latest_flag: Mapped[str] = mapped_column(CHAR(1))


class UserForecastAccess(Base):                        # which forecast types a user may open
    __tablename__ = "user_forecast_access"
    user_id: Mapped[str] = mapped_column(String(50), ForeignKey("app_users.user_id"), primary_key=True)
    forecast_type: Mapped[str] = mapped_column(String(10), primary_key=True)


class UserRegionAccess(Base):                          # which regions a user may see
    __tablename__ = "user_region_access"
    user_id: Mapped[str] = mapped_column(String(50), ForeignKey("app_users.user_id"), primary_key=True)
    region_code: Mapped[str] = mapped_column(String(10), primary_key=True)


class _Dfu(Base):                                      # POC copy of GDO-094 ShortTerm/MidTermMaterialDFU
    __abstract__ = True
    dfu_id: Mapped[int] = mapped_column("dfuid", BigInteger, primary_key=True, autoincrement=True)
    material_code: Mapped[str] = mapped_column("materialcode", String(30))
    country_code: Mapped[str] = mapped_column("countrycode", String(10))
    active_flag: Mapped[int] = mapped_column("forecastengineactiveflag", SmallInteger, default=1)


class ShortTermMaterialDFU(_Dfu):
    __tablename__ = "shorttermmaterialdfu"
    __table_args__ = {"schema": "glbl_ibp_gold"}


class MidTermMaterialDFU(_Dfu):
    __tablename__ = "midtermmaterialdfu"
    __table_args__ = {"schema": "glbl_ibp_gold"}


class MarketForecast(Base):                            # backing table, SCD type 2 (latest_flag = 'Y' is current)
    __tablename__ = "market_forecast"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(10))
    is_current_week_fcst: Mapped[bool] = mapped_column(Boolean)
    generation_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[str] = mapped_column(String(50))
    fcst_type: Mapped[str] = mapped_column(String(10))
    latest_flag: Mapped[str] = mapped_column(CHAR(1))
