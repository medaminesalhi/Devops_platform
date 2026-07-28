from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import uuid
import zipfile

from dataclasses import (
    asdict,
    dataclass,
)

from pathlib import (
    Path,
    PurePosixPath,
)

from typing import Any

from flask import current_app

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


class ArchiveProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 400,
    ) -> None:
        super().__init__(message)

        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass
class ArchiveValidationResult:
    source_type: str

    original_name: str
    size_bytes: int
    sha256: str

    entry_count: int
    uncompressed_bytes: int
    top_level_entries: list[str]

    stored_name: str | None = None
    storage_path: str | None = None

    validation_method: str = "zip_inspection"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ArchiveSourceProvider:
    def validate_upload(
        self,
        archive_file: FileStorage | None,
    ) -> ArchiveValidationResult:
        original_name = self.validate_file_name(
            archive_file
        )

        with tempfile.TemporaryDirectory(
            prefix="piximind-zip-check-"
        ) as temporary_directory:
            temporary_path = (
                Path(temporary_directory)
                / "source.zip"
            )

            size_bytes, sha256 = self.copy_upload(
                archive_file=archive_file,
                destination=temporary_path,
            )

            inspection = self.inspect_archive(
                temporary_path
            )

        return ArchiveValidationResult(
            source_type="zip",
            original_name=original_name,
            size_bytes=size_bytes,
            sha256=sha256,
            entry_count=inspection["entry_count"],
            uncompressed_bytes=(
                inspection["uncompressed_bytes"]
            ),
            top_level_entries=(
                inspection["top_level_entries"]
            ),
        )

    def store_upload(
        self,
        archive_file: FileStorage | None,
    ) -> ArchiveValidationResult:
        original_name = self.validate_file_name(
            archive_file
        )

        archive_root = Path(
            current_app.config[
                "PROJECT_ARCHIVE_ROOT"
            ]
        )

        archive_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        stored_name = f"{uuid.uuid4().hex}.zip"

        final_path = archive_root / stored_name

        temporary_path = archive_root / (
            f".{stored_name}.uploading"
        )

        try:
            size_bytes, sha256 = self.copy_upload(
                archive_file=archive_file,
                destination=temporary_path,
            )

            inspection = self.inspect_archive(
                temporary_path
            )

            os.replace(
                temporary_path,
                final_path,
            )

        except Exception:
            temporary_path.unlink(
                missing_ok=True
            )

            final_path.unlink(
                missing_ok=True
            )

            raise

        return ArchiveValidationResult(
            source_type="zip",
            original_name=original_name,
            size_bytes=size_bytes,
            sha256=sha256,
            entry_count=inspection["entry_count"],
            uncompressed_bytes=(
                inspection["uncompressed_bytes"]
            ),
            top_level_entries=(
                inspection["top_level_entries"]
            ),
            stored_name=stored_name,
            storage_path=str(final_path),
        )

    def remove_stored_archive(
        self,
        storage_path: str | None,
    ) -> None:
        if not storage_path:
            return

        try:
            Path(storage_path).unlink(
                missing_ok=True
            )

        except OSError:
            current_app.logger.exception(
                "Impossible de supprimer l'archive %s.",
                storage_path,
            )

    def validate_file_name(
        self,
        archive_file: FileStorage | None,
    ) -> str:
        if (
            archive_file is None
            or not archive_file.filename
        ):
            raise ArchiveProviderError(
                "ARCHIVE_REQUIRED",
                "Sélectionnez une archive ZIP.",
            )

        original_name = secure_filename(
            archive_file.filename
        )

        if not original_name:
            raise ArchiveProviderError(
                "INVALID_ARCHIVE_NAME",
                "Le nom de l'archive est invalide.",
            )

        if not original_name.lower().endswith(".zip"):
            raise ArchiveProviderError(
                "INVALID_ARCHIVE_EXTENSION",
                "Seuls les fichiers .zip sont acceptés.",
            )

        return original_name

    def copy_upload(
        self,
        *,
        archive_file: FileStorage | None,
        destination: Path,
    ) -> tuple[int, str]:
        if archive_file is None:
            raise ArchiveProviderError(
                "ARCHIVE_REQUIRED",
                "Sélectionnez une archive ZIP.",
            )

        maximum_bytes = int(
            current_app.config[
                "PROJECT_ARCHIVE_MAX_BYTES"
            ]
        )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        digest = hashlib.sha256()

        size_bytes = 0

        try:
            archive_file.stream.seek(0)

        except (AttributeError, OSError):
            pass

        with destination.open("wb") as output:
            while True:
                chunk = archive_file.stream.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                size_bytes += len(chunk)

                if size_bytes > maximum_bytes:
                    raise ArchiveProviderError(
                        "ARCHIVE_TOO_LARGE",
                        (
                            "L'archive dépasse la taille "
                            "maximale autorisée."
                        ),
                        413,
                    )

                digest.update(chunk)

                output.write(chunk)

        try:
            archive_file.stream.seek(0)

        except (AttributeError, OSError):
            pass

        if size_bytes == 0:
            raise ArchiveProviderError(
                "EMPTY_ARCHIVE",
                "L'archive ZIP est vide.",
            )

        return size_bytes, digest.hexdigest()

    def inspect_archive(
        self,
        archive_path: Path,
    ) -> dict[str, Any]:
        maximum_entries = int(
            current_app.config[
                "PROJECT_ARCHIVE_MAX_ENTRIES"
            ]
        )

        maximum_uncompressed = int(
            current_app.config[
                "PROJECT_ARCHIVE_MAX_UNCOMPRESSED_BYTES"
            ]
        )

        maximum_ratio = float(
            current_app.config[
                "PROJECT_ARCHIVE_MAX_COMPRESSION_RATIO"
            ]
        )

        if not zipfile.is_zipfile(archive_path):
            raise ArchiveProviderError(
                "INVALID_ZIP_ARCHIVE",
                "Le fichier fourni n'est pas un ZIP valide.",
            )

        entry_count = 0
        uncompressed_bytes = 0
        compressed_bytes = 0

        top_level_entries: set[str] = set()

        try:
            with zipfile.ZipFile(archive_path) as archive:
                entries = archive.infolist()

                if not entries:
                    raise ArchiveProviderError(
                        "EMPTY_ARCHIVE",
                        "L'archive ZIP ne contient aucun fichier.",
                    )

                if len(entries) > maximum_entries:
                    raise ArchiveProviderError(
                        "ARCHIVE_TOO_MANY_ENTRIES",
                        (
                            "L'archive contient trop de fichiers "
                            "pour être traitée en sécurité."
                        ),
                    )

                for entry in entries:
                    self.validate_entry(entry)

                    if entry.is_dir():
                        continue

                    entry_count += 1

                    uncompressed_bytes += (
                        entry.file_size
                    )

                    compressed_bytes += (
                        entry.compress_size
                    )

                    if (
                        uncompressed_bytes
                        > maximum_uncompressed
                    ):
                        raise ArchiveProviderError(
                            "ARCHIVE_UNCOMPRESSED_TOO_LARGE",
                            (
                                "Le contenu décompressé dépasse "
                                "la limite autorisée."
                            ),
                        )

                    normalized_name = (
                        entry.filename
                        .replace("\\", "/")
                        .lstrip("/")
                    )

                    first_part = PurePosixPath(
                        normalized_name
                    ).parts[0]

                    if first_part:
                        top_level_entries.add(
                            first_part
                        )

        except zipfile.BadZipFile as error:
            raise ArchiveProviderError(
                "INVALID_ZIP_ARCHIVE",
                "Le fichier ZIP est corrompu.",
            ) from error

        if entry_count == 0:
            raise ArchiveProviderError(
                "EMPTY_ARCHIVE",
                "L'archive ZIP ne contient aucun fichier.",
            )

        effective_compressed = max(
            compressed_bytes,
            1,
        )

        compression_ratio = (
            uncompressed_bytes
            / effective_compressed
        )

        if compression_ratio > maximum_ratio:
            raise ArchiveProviderError(
                "SUSPICIOUS_ARCHIVE_COMPRESSION",
                (
                    "Le taux de compression de l'archive "
                    "est anormalement élevé."
                ),
            )

        return {
            "entry_count": entry_count,
            "uncompressed_bytes":
                uncompressed_bytes,
            "top_level_entries": sorted(
                top_level_entries
            )[:20],
        }

    def validate_entry(
        self,
        entry: zipfile.ZipInfo,
    ) -> None:
        raw_name = entry.filename

        if not raw_name or "\x00" in raw_name:
            raise ArchiveProviderError(
                "UNSAFE_ARCHIVE_ENTRY",
                "L'archive contient un chemin invalide.",
            )

        normalized_name = raw_name.replace(
            "\\",
            "/",
        )

        if (
            normalized_name.startswith("/")
            or re.match(
                r"^[a-zA-Z]:",
                normalized_name,
            )
        ):
            raise ArchiveProviderError(
                "UNSAFE_ARCHIVE_ENTRY",
                (
                    "L'archive contient un chemin absolu "
                    "non autorisé."
                ),
            )

        path = PurePosixPath(normalized_name)

        if ".." in path.parts:
            raise ArchiveProviderError(
                "UNSAFE_ARCHIVE_ENTRY",
                (
                    "L'archive contient un chemin dangereux "
                    "avec '..'."
                ),
            )

        unix_mode = entry.external_attr >> 16

        if stat.S_ISLNK(unix_mode):
            raise ArchiveProviderError(
                "ARCHIVE_SYMLINK_NOT_ALLOWED",
                (
                    "Les liens symboliques ne sont pas "
                    "autorisés dans l'archive."
                ),
            )

        if entry.flag_bits & 0x1:
            raise ArchiveProviderError(
                "ENCRYPTED_ARCHIVE_NOT_ALLOWED",
                (
                    "Les archives ZIP chiffrées ne sont "
                    "pas prises en charge."
                ),
            )


archive_source_provider = ArchiveSourceProvider()