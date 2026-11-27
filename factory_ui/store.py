"""Estado local del usuario de Factory Studio (fuera de cualquier proyecto).

`~/.agentic-factory/` (o `FACTORY_HOME`): perfiles (nombre + roles) y proyectos recientes.
Los perfiles NO son autenticacion: identifican quien firma; los roles se siguen validando
contra los roles autorizados de cada gate del proyecto.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

MAX_RECENT = 10


class Profile(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    roles: list[str]

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 80 or re.search(r"[()\n]", v):
            raise ValueError("nombre invalido (1-80 caracteres, sin parentesis)")
        return v

    @field_validator("roles")
    @classmethod
    def _roles(cls, v: list[str]) -> list[str]:
        cleaned = [r.strip() for r in v if r.strip()]
        if not cleaned:
            raise ValueError("el perfil necesita al menos un rol")
        if any(re.search(r"[()\n]", r) or len(r) > 60 for r in cleaned):
            raise ValueError("rol invalido")
        return cleaned

    def signature(self, role: str) -> str:
        """Firma 'Nombre (Rol)' que valida factory_app.roles."""
        return f"{self.name} ({role})"


def home() -> Path:
    return Path(os.environ.get("FACTORY_HOME") or Path.home() / ".agentic-factory")


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else home()
        self._lock = threading.Lock()

    # -------------------------------------------------------- io
    def _read(self, name: str, default):
        path = self.root / name
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _write(self, name: str, data) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".{name}.tmp"
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.root / name)

    # -------------------------------------------------------- perfiles
    def profiles(self) -> list[Profile]:
        return [Profile.model_validate(p) for p in self._read("profiles.json", [])]

    def profile(self, profile_id: str) -> Profile | None:
        return next((p for p in self.profiles() if p.id == profile_id), None)

    def save_profile(self, profile: Profile) -> Profile:
        with self._lock:
            items = [p for p in self.profiles() if p.id != profile.id] + [profile]
            self._write("profiles.json", [p.model_dump() for p in items])
        return profile

    def delete_profile(self, profile_id: str) -> bool:
        with self._lock:
            items = self.profiles()
            kept = [p for p in items if p.id != profile_id]
            self._write("profiles.json", [p.model_dump() for p in kept])
        return len(kept) != len(items)

    # -------------------------------------------------------- recientes
    def recent(self) -> list[str]:
        return [p for p in self._read("recent.json", []) if isinstance(p, str)]

    def add_recent(self, path: Path) -> None:
        with self._lock:
            text = str(Path(path).resolve())
            items = [text] + [p for p in self.recent() if p != text]
            self._write("recent.json", items[:MAX_RECENT])
