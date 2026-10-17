"""Global test setup. Environment variables are set here, before any app module is
imported, because config.settings is created at import time."""

import os

os.environ.setdefault("DATA_API_KEY", "test-data-key")
os.environ.setdefault("AZURE_FOUNDRY_ENDPOINT", "https://example.invalid")
os.environ.setdefault("AZURE_FOUNDRY_KEY", "test-foundry-key")
# Overwrite (never setdefault): a developer's .env or shell must not be able to point
# the tests at a real database. Integration setup additionally refuses any database
# whose name does not end in "_test".
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://flwn:flwn@localhost:5432/flwn_test"
)
