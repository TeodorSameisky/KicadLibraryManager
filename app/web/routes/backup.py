"""Downloading a copy of the database.

The daily copies live on the same disk as the database, which protects against
a bad deploy or an upload deleted by mistake and not against losing the disk.
This is how a librarian puts a copy somewhere that is not this server.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.db.backup import BackupError, Backups, copy_database
from app.web.deps import get_backups, require_librarian

router = APIRouter(prefix="/api/v1", tags=["backup"], dependencies=[Depends(require_librarian)])


@router.get("/backup")
def download_backup(backups: Backups = Depends(get_backups)) -> FileResponse:
    """A copy of the database as it is now, made for this request.

    A plain function: copying a database blocks, so it runs on a worker thread
    and not on the event loop everything else is waiting on.
    """
    handle, name = tempfile.mkstemp(prefix="klm-backup-", suffix=".db")
    os.close(handle)
    copy = Path(name)
    try:
        copy_database(backups.database, copy)
    except BackupError:
        copy.unlink(missing_ok=True)
        raise

    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    return FileResponse(
        copy,
        media_type="application/vnd.sqlite3",
        filename=f"library-manager-{stamp}.db",
        # Gone as soon as it has been sent: nothing is left behind to find.
        background=BackgroundTask(lambda: copy.unlink(missing_ok=True)),
        headers={"Cache-Control": "no-store"},
    )
