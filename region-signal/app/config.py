import os, time
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo(os.getenv("BUSINESS_TZ", "America/Chicago"))   # Central time
WINDOW_HOURS = float(os.getenv("WINDOW_HOURS", "3"))               # app is editable for this long
APP_STARTED_AT = time.time()                                       # process start = start of the session window
DEADLINE = APP_STARTED_AT + WINDOW_HOURS * 3600
SECRET_KEY = os.getenv("APP_SECRET_KEY", "dev-only-change-me")     # put in a Databricks secret
TOKEN_MAX_AGE = int(WINDOW_HOURS * 3600) + 600
