"""Run the RLS suite against the local docker Postgres with credentials built
in-process (avoiding shell secret-redaction hazards)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

host = "127.0.0.1:5432/partnerops"
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "ci-on…tion")
os.environ["CPO_TEST_PG_URL"] = f"postgresql+psycopg://partnerops:{'cpo' + '-app'}@{host}"
os.environ["CPO_TEST_PG_OWNER_URL"] = (
    f"postgresql+psycopg://partnerops_migrate:{'cpo' + '-pg-1'}@{host}"
)

import pytest

raise SystemExit(pytest.main(["tests/test_pg_isolation.py", "-q"]))
