from __future__ import annotations

from contextlib import (
    contextmanager,
)

from dataclasses import (
    dataclass,
)

from pathlib import Path

from typing import Iterator

from app.analysis.git_workspace import (
    GitWorkspaceError,
    git_workspace_manager,
)

from app.analysis.source_workspace import (
    SourceWorkspaceError,
    source_workspace_manager,
)


class GenerationWorkspaceError(
    RuntimeError
):
    def __init__(
        self,
        code: str,
        message: str,
    ) -> None:
        super().__init__(message)

        self.code = code
        self.message = message


@dataclass
class GenerationSource:
    source_path: Path
    source_type: str
    version: str


class GenerationWorkspaceManager:
    @contextmanager
    def prepare(
        self,
        context: dict,
    ) -> Iterator[GenerationSource]:
        source_type = str(
            context.get("source_type")
            or "git"
        ).lower()

        confirmed_version = str(
            context.get(
                "confirmed_version"
            )
            or ""
        ).strip()

        if not confirmed_version:
            raise GenerationWorkspaceError(
                "CONFIRMED_VERSION_MISSING",
                (
                    "L'analyse confirmée ne contient "
                    "aucune version de source."
                ),
            )

        if source_type == "zip":
            try:
                with (
                    source_workspace_manager
                    .prepare_zip(context)
                ) as prepared:
                    if (
                        prepared.version
                        != confirmed_version
                    ):
                        raise (
                            GenerationWorkspaceError(
                                "ARCHIVE_VERSION_MISMATCH",
                                (
                                    "L'archive disponible ne "
                                    "correspond pas à "
                                    "l'analyse confirmée."
                                ),
                            )
                        )

                    yield GenerationSource(
                        source_path=
                            prepared.source_path,

                        source_type="zip",

                        version=
                            prepared.version,
                    )

            except SourceWorkspaceError as error:
                raise GenerationWorkspaceError(
                    error.code,
                    error.message,
                ) from error

            return

        project_for_checkout = {
            **context,

            "last_source_commit_sha":
                confirmed_version,
        }

        try:
            with git_workspace_manager.checkout(
                project=project_for_checkout,
                commit_policy="validated",
            ) as checkout:
                if (
                    checkout.analyzed_commit_sha
                    != confirmed_version
                ):
                    raise GenerationWorkspaceError(
                        "GIT_VERSION_MISMATCH",
                        (
                            "Le commit téléchargé ne "
                            "correspond pas à "
                            "l'analyse confirmée."
                        ),
                    )

                yield GenerationSource(
                    source_path=
                        checkout.source_path,

                    source_type="git",

                    version=
                        checkout
                        .analyzed_commit_sha,
                )

        except GitWorkspaceError as error:
            raise GenerationWorkspaceError(
                error.code,
                error.message,
            ) from error


generation_workspace_manager = (
    GenerationWorkspaceManager()
)