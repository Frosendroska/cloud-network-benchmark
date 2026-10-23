"""Deterministic F06-only Azure response and expiry fixtures."""
import json
from pathlib import Path

RESPONSE_FIXTURES=json.loads((Path(__file__).parent/"responses.json").read_text(encoding="utf-8"))
