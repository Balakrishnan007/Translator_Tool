# -*- coding: utf-8 -*-
"""Opens the actual connection to Postgres.

`engine` is the pool of real connections to the database. A `Session` is one
"conversation" with it: opened to do some work, then closed. `get_session()`
is written as a generator so it can later be used directly as a FastAPI
dependency (`Depends(get_session)`), which expects exactly this shape.
"""

import os
from collections.abc import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found. Check that backend/.env exists and contains it.")

engine = create_engine(DATABASE_URL)
# expire_on_commit=False: by default SQLAlchemy expires every loaded object
# after each commit(), forcing a re-fetch on next attribute access. Fine
# inside one unbroken session, but our CRUD functions each commit and hand
# objects back to be used afterward (confirmed real: this caused a
# DetachedInstanceError on exactly that pattern). Standard fix.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
