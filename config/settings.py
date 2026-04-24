import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent

load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = BASE_DIR / "uploads"

DATA_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR / 'audit.db'}"
RULES_FILE = BASE_DIR / "config" / "rules.yaml"
GUIDELINES_FILE = BASE_DIR / "config" / "guidelines.yaml"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")

# 优先使用智谱 GLM，如果没有则使用 Claude
USE_ZHIPU = bool(ZHIPU_API_KEY)

FILE_RETENTION_DAYS = 30
MAX_FILE_SIZE_MB = 20
