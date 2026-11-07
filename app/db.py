import warnings

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from . import config
from .models import Base, Currency

# SQLite stores NUMERIC as float internally; values are quantised to 6 dp on the way in,
# and PostgreSQL (the real target) is exact. Silence the dialect warning for the mock.
warnings.filterwarnings("ignore", message="Dialect sqlite.*does \\*not\\* support Decimal")


def make_session_factory(url: str | None = None):
    url = url or config.DATABASE_URL
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool
            kwargs["poolclass"] = StaticPool
        else:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)
    seed_currencies(sf)
    return sf


def seed_currencies(sf):
    with sf() as s:
        if s.scalars(select(Currency)).first() is None:
            for i, (code, name) in enumerate(config.DEFAULT_CURRENCIES):
                s.add(Currency(code=code, name=name, enabled=True, sort_order=i))
            s.commit()
