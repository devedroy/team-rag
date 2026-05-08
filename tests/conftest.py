import os

# Set default DATABASE_URL for test environment before any app imports
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://teamrag:teamrag@localhost:5432/teamrag")
