import os
import sys
from pathlib import Path

os.environ.setdefault("KARMASAKSHI_PUBLIC_DEMO", "1")
os.environ.setdefault("KARMASAKSHI_DATA_DIR", "/tmp/karmasakshi-data")

root = Path(__file__).resolve().parents[1]
src = root / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from karmasakshi.api.app import create_app

app = create_app()
