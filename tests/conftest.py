import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AI_PROVIDER", "deterministic")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-long-enough-32-bytes")
os.environ.setdefault("JWT_REQUIRED", "false")
