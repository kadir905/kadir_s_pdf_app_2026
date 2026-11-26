"""AstroPDF Studio - Firebase API.

This service is intentionally separate from pdf_studio.py.  The Flutter app
and the Windows desktop app can use the same authenticated API without
exposing a Firebase service-account key to a client application.

Required environment variables:
    FIREBASE_SERVICE_ACCOUNT: absolute path to the Firebase service-account JSON
    FIREBASE_STORAGE_BUCKET: Firebase Storage bucket name

Run locally:
    uvicorn firebase_api:app --reload --port 8000
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import firebase_admin
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth, credentials, firestore, storage
from pydantic import BaseModel, Field


SERVICE_ACCOUNT = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
STORAGE_BUCKET = os.environ.get("FIREBASE_STORAGE_BUCKET", "").strip()

if not SERVICE_ACCOUNT:
    raise RuntimeError(
        "FIREBASE_SERVICE_ACCOUNT ortam değişkeni ayarlanmadı. "
        "Firebase service-account JSON dosyasının yolunu belirtin."
    )

if not Path(SERVICE_ACCOUNT).is_file():
    raise RuntimeError(f"Firebase service-account dosyası bulunamadı: {SERVICE_ACCOUNT}")

if not firebase_admin._apps:
    options = {"projectId": "derscalismauygulamam"}
    if STORAGE_BUCKET:
        options["storageBucket"] = STORAGE_BUCKET
    firebase_admin.initialize_app(credentials.Certificate(SERVICE_ACCOUNT), options)

db = firestore.client()
bucket = storage.bucket() if STORAGE_BUCKET else None
app = FastAPI(title="AstroPDF Studio Firebase API", version="1.0.0")
security = HTTPBearer(auto_error=False)

# Flutter Android geliştirme ortamı için açık bırakılmıştır. Üretimde yalnızca
# kullanılan web/masaüstü istemci adresleriyle sınırlandırılmalıdır.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


class NoteCreate(BaseModel):
    title: str = Field(default="Yeni not", max_length=200)
    content: str = Field(default="", max_length=200_000)
    pdf_id: str | None = Field(default=None, max_length=200)
    page_number: int | None = Field(default=None, ge=1)
    tags: list[str] = Field(default_factory=list, max_length=30)


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=200_000)
    pdf_id: str | None = Field(default=None, max_length=200)
    page_number: int | None = Field(default=None, ge=1)
    tags: list[str] | None = Field(default=None, max_length=30)


def current_user(
    token: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    """Verify the Firebase ID token sent by Flutter or the desktop client."""
    if token is None or token.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Giriş gerekli")
    try:
        return auth.verify_id_token(token.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz veya süresi dolmuş Firebase oturumu",
        ) from exc


def note_ref(note_id: str):
    """Use the existing top-level notes collection in this Firebase project."""
    return db.collection("notes").document(note_id)


def note_data(snapshot) -> dict[str, Any]:
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    # Keep the existing Turkish Firestore fields while exposing a stable API
    # shape for the Flutter client.
    data.setdefault("title", data.get("baslik", ""))
    data.setdefault("content", data.get("icerik", ""))
    data.setdefault("lesson_id", data.get("dersId"))
    data.setdefault("created_at", data.get("createdAt"))
    return data


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "astropdf-firebase-api"}


@app.get("/notes")
def list_notes(user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    # This project already stores notes in /notes.  Sorting in Python keeps
    # older documents without updatedAt visible as well.
    snapshots = list(db.collection("notes").stream())
    records = [note_data(snapshot) for snapshot in snapshots]
    records.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
    return records


@app.post("/notes", status_code=status.HTTP_201_CREATED)
def create_note(note: NoteCreate, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    note_id = uuid.uuid4().hex
    payload = {
        "baslik": note.title,
        "icerik": note.content,
        "dersId": note.pdf_id,
        "pageNumber": note.page_number,
        "tags": note.tags,
        "createdAt": now,
        "updatedAt": now,
        "ownerUid": user["uid"],
    }
    ref = note_ref(note_id)
    ref.set(payload)
    return note_data(ref.get())


@app.patch("/notes/{note_id}")
def update_note(
    note_id: str,
    note: NoteUpdate,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    ref = note_ref(note_id)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail="Not bulunamadı")
    input_changes = {key: value for key, value in note.model_dump().items() if value is not None}
    field_names = {"title": "baslik", "content": "icerik", "pdf_id": "dersId", "page_number": "pageNumber"}
    changes = {field_names.get(key, key): value for key, value in input_changes.items()}
    changes["updatedAt"] = datetime.now(timezone.utc)
    ref.update(changes)
    return note_data(ref.get())


@app.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: str, user: dict[str, Any] = Depends(current_user)) -> Response:
    ref = note_ref(note_id)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail="Not bulunamadı")
    ref.delete()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/files", status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, str]:
    if bucket is None:
        raise HTTPException(
            status_code=503,
            detail="Firebase Storage etkin değil. Firebase Console'da Storage'ı etkinleştirin.",
        )
    if not file.filename:
        raise HTTPException(status_code=400, detail="Dosya adı bulunamadı")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".pdf", ".txt", ".docx", ".xlsx", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(status_code=415, detail="Bu dosya türü desteklenmiyor")
    object_path = f"users/{user['uid']}/files/{uuid.uuid4().hex}{suffix}"
    blob = bucket.blob(object_path)
    blob.upload_from_file(file.file, content_type=file.content_type or "application/octet-stream")
    return {
        "storage_path": object_path,
        "download_url": f"/files/{object_path}",
        "original_name": file.filename,
    }


@app.get("/files/{object_path:path}")
def download_file(object_path: str, user: dict[str, Any] = Depends(current_user)) -> Response:
    if bucket is None:
        raise HTTPException(
            status_code=503,
            detail="Firebase Storage etkin değil. Firebase Console'da Storage'ı etkinleştirin.",
        )
    expected_prefix = f"users/{user['uid']}/files/"
    if not object_path.startswith(expected_prefix):
        raise HTTPException(status_code=403, detail="Bu dosyaya erişim izniniz yok")
    blob = bucket.blob(object_path)
    if not blob.exists():
        raise HTTPException(status_code=404, detail="Dosya bulunamadı")
    content = blob.download_as_bytes()
    return Response(content=content, media_type=blob.content_type or "application/octet-stream")
