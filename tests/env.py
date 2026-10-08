"""The environment tests run in. Import this before any app module.

Assigned, not defaulted, so a developer's shell or .env can never leak real credentials or a
real storage account into a test run (environment variables beat .env in the settings).
"""

import os

os.environ["DATA_API_KEY"] = "test-data-key"
os.environ["AZURE_FOUNDRY_ENDPOINT"] = "https://example.invalid"
os.environ["AZURE_FOUNDRY_KEY"] = "test-foundry-key"
os.environ["AZURE_STORAGE_ACCOUNT_URL"] = ""  # file tests inject a fake store
