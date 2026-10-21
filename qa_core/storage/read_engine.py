"""Finite SQL driver timeouts for read-side services."""
import sqlalchemy as sa

def bounded_dsn(dsn: str) -> str:
    url = sa.engine.make_url(dsn)
    if url.drivername.startswith('mysql+pymysql'):
        url = url.update_query_dict({'connect_timeout': '3', 'read_timeout': '5', 'write_timeout': '5'})
    return url.render_as_string(hide_password=False)

def read_engine(dsn: str):
    return sa.create_engine(bounded_dsn(dsn), pool_pre_ping=True)
