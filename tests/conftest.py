import os
from pathlib import Path

# Load .env before any app imports so GITHUB_TOKEN and other vars are available
# for module-level pytestmark skip conditions
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file, override=False)

# Set default DATABASE_URL for test environment before any app imports
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://teamrag:teamrag@localhost:5432/teamrag")
