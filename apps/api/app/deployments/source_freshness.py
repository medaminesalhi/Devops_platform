from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.analysis.git_workspace import GitWorkspaceError, git_workspace_manager
from app.analysis.repository import find_project_source


@dataclass(frozen=True)
class SourceFreshness:
    status: str
    selected_commit: str
    current_commit: str | None
    branch: str | None
    message: str

    @property
    def historical(self) -> bool:
        return self.status == "historical"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "historical": self.historical}


def inspect_source_freshness(
    *,
    project_id: int,
    selected_commit: str | None,
) -> SourceFreshness:
    """Compare le commit explicitement sélectionné avec le HEAD distant.

    Une version antérieure n'est plus considérée comme une erreur : si
    l'utilisateur la choisit explicitement et qu'une génération approuvée lui
    correspond, elle peut être redéployée (rollback volontaire). Le HEAD sert
    seulement à indiquer si la sélection est la version courante ou historique.
    """
    selected = str(selected_commit or "").strip().lower()
    project = find_project_source(project_id)

    if project is None:
        return SourceFreshness(
            status="unavailable",
            selected_commit=selected,
            current_commit=None,
            branch=None,
            message="La source du projet est introuvable.",
        )

    source_type = str(project.get("source_type") or "git").lower()
    if source_type != "git":
        return SourceFreshness(
            status="current",
            selected_commit=selected,
            current_commit=selected or None,
            branch=None,
            message="La révision source sélectionnée est prête à être utilisée.",
        )

    branch = str(project.get("default_branch") or "main")
    try:
        current = git_workspace_manager.get_branch_head(project=project).lower()
    except GitWorkspaceError as error:
        return SourceFreshness(
            status="unavailable",
            selected_commit=selected,
            current_commit=None,
            branch=branch,
            message=(
                "Impossible de vérifier le dernier commit distant : "
                f"{error.message}"
            ),
        )

    if not selected:
        return SourceFreshness(
            status="unavailable",
            selected_commit="",
            current_commit=current,
            branch=branch,
            message="Aucun commit n'a été sélectionné.",
        )

    if selected == current:
        return SourceFreshness(
            status="current",
            selected_commit=selected,
            current_commit=current,
            branch=branch,
            message=(
                f"Le commit sélectionné est le dernier commit de {branch} "
                f"({current[:8]})."
            ),
        )

    return SourceFreshness(
        status="historical",
        selected_commit=selected,
        current_commit=current,
        branch=branch,
        message=(
            f"Version antérieure sélectionnée : {selected[:8]}. "
            f"Le dernier commit de {branch} est {current[:8]}. "
            "Le déploiement reste autorisé car cette version a été choisie explicitement."
        ),
    )
