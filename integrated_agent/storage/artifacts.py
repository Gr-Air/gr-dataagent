from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", value).strip("._")
    return cleaned[:120] or "artifact.bin"


@dataclass(frozen=True)
class StoredArtifact:
    artifact_id: str
    filename: str
    path: Path
    mime_type: str
    size_bytes: int


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def save(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str | None = None,
    ) -> StoredArtifact:
        if not content:
            raise ValueError("generated artifact is empty")
        artifact_id = uuid4().hex
        safe_name = _safe_filename(Path(filename).name)
        artifact_dir = self.root / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        path = artifact_dir / safe_name
        path.write_bytes(content)
        resolved_mime = (
            mime_type
            or mimetypes.guess_type(safe_name)[0]
            or "application/octet-stream"
        )
        return StoredArtifact(
            artifact_id=artifact_id,
            filename=safe_name,
            path=path,
            mime_type=resolved_mime,
            size_bytes=len(content),
        )

    def resolve(self, artifact_id: str, filename: str) -> StoredArtifact | None:
        if re.fullmatch(r"[0-9a-f]{32}", artifact_id) is None:
            return None
        safe_name = _safe_filename(Path(filename).name)
        path = (self.root / artifact_id / safe_name).resolve()
        if self.root not in path.parents or not path.is_file():
            return None
        return StoredArtifact(
            artifact_id=artifact_id,
            filename=safe_name,
            path=path,
            mime_type=(
                mimetypes.guess_type(safe_name)[0]
                or "application/octet-stream"
            ),
            size_bytes=path.stat().st_size,
        )
