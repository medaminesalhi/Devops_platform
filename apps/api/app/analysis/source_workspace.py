from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import zipfile

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

from flask import current_app

from app.analysis.git_workspace import (
    GitWorkspaceError,
    git_workspace_manager,
)


class SourceWorkspaceError(RuntimeError):
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
class PreparedSource:
    source_path: Path
    source_type: str
    version: str
    display_name: str
    previous_version: str | None
    source_changed: bool
    branch_head: str | None


class SourceWorkspaceManager:
    @contextmanager
    def prepare(
        self,
        project: dict,
        *,
        commit_policy: str = "latest",
        requested_commit_sha: str | None = None,
    ) -> Iterator[PreparedSource]:
        source_type = str(
            project.get("source_type") or "git"
        ).lower()

        if source_type == "zip":
            with self.prepare_zip(project) as prepared:
                yield prepared
            return

        try:
            with git_workspace_manager.checkout(
                project=project,
                commit_policy=commit_policy,
                requested_commit_sha=requested_commit_sha,
            ) as checkout:
                previous_version = project.get(
                    "previous_analyzed_version"
                )

                yield PreparedSource(
                    source_path=checkout.source_path,
                    source_type="git",
                    version=checkout.analyzed_commit_sha,
                    display_name=(
                        project.get("repository_path")
                        or project.get("repository_url")
                        or project.get("name")
                        or "Repository Git"
                    ),
                    previous_version=previous_version,
                    source_changed=(
                        previous_version is not None
                        and previous_version
                        != checkout.analyzed_commit_sha
                    ),
                    branch_head=checkout.branch_head_sha,
                )

        except GitWorkspaceError as error:
            raise SourceWorkspaceError(
                error.code,
                error.message,
                400,
            ) from error

    @contextmanager
    def prepare_zip(
        self,
        project: dict,
    ) -> Iterator[PreparedSource]:
        archive_path_value = project.get(
            "archive_storage_path"
        )

        if not archive_path_value:
            raise SourceWorkspaceError(
                "ARCHIVE_PATH_MISSING",
                "Le chemin de l'archive du projet est absent.",
            )

        archive_path = Path(archive_path_value).resolve()

        if not archive_path.exists() or not archive_path.is_file():
            raise SourceWorkspaceError(
                "ARCHIVE_NOT_FOUND",
                "L'archive ZIP du projet est introuvable.",
                404,
            )

        expected_sha256 = str(
            project.get("archive_sha256") or ""
        ).strip().lower()

        actual_sha256 = self.sha256_file(archive_path)

        if expected_sha256 and actual_sha256 != expected_sha256:
            raise SourceWorkspaceError(
                "ARCHIVE_CHECKSUM_MISMATCH",
                (
                    "L'archive enregistrée a été modifiée depuis "
                    "sa validation. Importez-la de nouveau."
                ),
                409,
            )

        configured_root = (
            current_app.config.get("ANALYSIS_WORKSPACE_ROOT")
            or None
        )

        if configured_root:
            Path(configured_root).mkdir(
                parents=True,
                exist_ok=True,
            )

        with tempfile.TemporaryDirectory(
            prefix=f"piximind-analysis-{project['id']}-",
            dir=configured_root,
        ) as temporary_directory:
            workspace = Path(temporary_directory)
            extraction_path = workspace / "source"
            extraction_path.mkdir(parents=True, exist_ok=True)

            self.extract_archive_safely(
                archive_path=archive_path,
                destination=extraction_path,
            )

            effective_root = self.find_effective_root(
                extraction_path
            )

            previous_version = project.get(
                "previous_analyzed_version"
            )

            yield PreparedSource(
                source_path=effective_root,
                source_type="zip",
                version=actual_sha256,
                display_name=(
                    project.get("archive_original_name")
                    or archive_path.name
                ),
                previous_version=previous_version,
                source_changed=(
                    previous_version is not None
                    and previous_version != actual_sha256
                ),
                branch_head=None,
            )

    def extract_archive_safely(
        self,
        *,
        archive_path: Path,
        destination: Path,
    ) -> None:
        maximum_entries = int(
            current_app.config.get(
                "PROJECT_ARCHIVE_MAX_ENTRIES",
                20_000,
            )
        )

        maximum_uncompressed = int(
            current_app.config.get(
                "PROJECT_ARCHIVE_MAX_UNCOMPRESSED_BYTES",
                500 * 1024 * 1024,
            )
        )

        if not zipfile.is_zipfile(archive_path):
            raise SourceWorkspaceError(
                "INVALID_ZIP_ARCHIVE",
                "Le fichier enregistré n'est pas un ZIP valide.",
            )

        total_uncompressed = 0

        try:
            with zipfile.ZipFile(archive_path) as archive:
                entries = archive.infolist()

                if len(entries) > maximum_entries:
                    raise SourceWorkspaceError(
                        "ARCHIVE_TOO_MANY_ENTRIES",
                        "L'archive contient trop de fichiers.",
                    )

                for entry in entries:
                    self.validate_zip_entry(entry)

                    if entry.is_dir():
                        continue

                    total_uncompressed += entry.file_size

                    if total_uncompressed > maximum_uncompressed:
                        raise SourceWorkspaceError(
                            "ARCHIVE_UNCOMPRESSED_TOO_LARGE",
                            (
                                "La taille décompressée dépasse "
                                "la limite autorisée."
                            ),
                        )

                    normalized_name = entry.filename.replace(
                        "\\",
                        "/",
                    )

                    target_path = (
                        destination / normalized_name
                    ).resolve()

                    try:
                        target_path.relative_to(
                            destination.resolve()
                        )
                    except ValueError as error:
                        raise SourceWorkspaceError(
                            "UNSAFE_ARCHIVE_ENTRY",
                            "L'archive contient un chemin dangereux.",
                        ) from error

                    target_path.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    with archive.open(entry) as source_file:
                        with target_path.open("wb") as output_file:
                            while True:
                                chunk = source_file.read(1024 * 1024)
                                if not chunk:
                                    break
                                output_file.write(chunk)

        except zipfile.BadZipFile as error:
            raise SourceWorkspaceError(
                "INVALID_ZIP_ARCHIVE",
                "L'archive ZIP est corrompue.",
            ) from error

    def validate_zip_entry(
        self,
        entry: zipfile.ZipInfo,
    ) -> None:
        raw_name = entry.filename

        if not raw_name or "\x00" in raw_name:
            raise SourceWorkspaceError(
                "UNSAFE_ARCHIVE_ENTRY",
                "L'archive contient un chemin invalide.",
            )

        normalized_name = raw_name.replace("\\", "/")

        if (
            normalized_name.startswith("/")
            or re.match(r"^[a-zA-Z]:", normalized_name)
        ):
            raise SourceWorkspaceError(
                "UNSAFE_ARCHIVE_ENTRY",
                "Les chemins absolus sont interdits.",
            )

        if ".." in PurePosixPath(normalized_name).parts:
            raise SourceWorkspaceError(
                "UNSAFE_ARCHIVE_ENTRY",
                "Les chemins contenant '..' sont interdits.",
            )

        unix_mode = entry.external_attr >> 16

        if stat.S_ISLNK(unix_mode):
            raise SourceWorkspaceError(
                "ARCHIVE_SYMLINK_NOT_ALLOWED",
                "Les liens symboliques sont interdits.",
            )

        if entry.flag_bits & 0x1:
            raise SourceWorkspaceError(
                "ENCRYPTED_ARCHIVE_NOT_ALLOWED",
                "Les archives ZIP chiffrées ne sont pas prises en charge.",
            )

    def find_effective_root(
        self,
        extraction_path: Path,
    ) -> Path:
        visible_entries = [
            path
            for path in extraction_path.iterdir()
            if path.name not in {"__MACOSX", ".DS_Store"}
        ]

        root_markers = {
            "package.json",
            "requirements.txt",
            "pyproject.toml",
            "pom.xml",
            "go.mod",
            "composer.json",
            "Dockerfile",
        }

        if any(
            (extraction_path / marker).exists()
            for marker in root_markers
        ):
            return extraction_path

        if (
            len(visible_entries) == 1
            and visible_entries[0].is_dir()
        ):
            return visible_entries[0]

        return extraction_path

    def sha256_file(self, file_path: Path) -> str:
        digest = hashlib.sha256()

        with file_path.open("rb") as file_object:
            while True:
                chunk = file_object.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)

        return digest.hexdigest()


source_workspace_manager = SourceWorkspaceManager()
