from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.analysis.git_workspace import GitWorkspaceError, git_workspace_manager
from app.analysis.repository import find_project_source


@dataclass(frozen=True)
class SourceFreshness:
    status: str
    generation_commit: str
    current_commit: str | None
    branch: str | None
    message: str

    @property
    def outdated(self) -> bool:
        return self.status == "outdated"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "outdated": self.outdated}


def inspect_source_freshness(
    *,
    project_id: int,
    generation_commit: str | None,
) -> SourceFreshness:
    """Compare une génération immuable avec la source distante actuelle.

    Cette fonction ne remplace jamais le commit de la génération. Un nouveau
    commit doit déclencher une nouvelle analyse puis une nouvelle génération.
    """
    expected = str(generation_commit or "").strip()
    project = find_project_source(project_id)

    if project is None:
        return SourceFreshness(
            status="unavailable",
            generation_commit=expected,
            current_commit=None,
            branch=None,
            message="La source du projet est introuvable.",
        )

    source_type = str(project.get("source_type") or "git").lower()
    if source_type != "git":
        # Les archives restent immuables dans le workflow actuel. La détection
        # de HEAD distant concerne uniquement les projets Git.
        return SourceFreshness(
            status="current",
            generation_commit=expected,
            current_commit=expected or None,
            branch=None,
            message="La génération utilise la source approuvée de ce projet.",
        )

    branch = str(project.get("default_branch") or "main")
    try:
        current = git_workspace_manager.get_branch_head(project=project)
    except GitWorkspaceError as error:
        return SourceFreshness(
            status="unavailable",
            generation_commit=expected,
            current_commit=None,
            branch=branch,
            message=(
                "Impossible de vérifier le dernier commit distant : "
                f"{error.message}"
            ),
        )

    if not expected:
        return SourceFreshness(
            status="unavailable",
            generation_commit="",
            current_commit=current,
            branch=branch,
            message="Le commit source de la génération est absent.",
        )

    outdated = current != expected
    if outdated:
        message = (
            f"Un nouveau commit existe sur {branch} : {current[:8]}. "
            f"La génération sélectionnée utilise {expected[:8]}. "
            "Relancez l'analyse, confirmez-la puis créez une nouvelle génération."
        )
    else:
        message = (
            f"La génération correspond au dernier commit de {branch} "
            f"({current[:8]})."
        )

    return SourceFreshness(
        status="outdated" if outdated else "current",
        generation_commit=expected,
        current_commit=current,
        branch=branch,
        message=message,
    )
