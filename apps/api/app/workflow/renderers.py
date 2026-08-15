from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile

from dataclasses import (
    asdict,
    dataclass,
)

from pathlib import (
    Path,
    PurePosixPath,
)

from typing import Any

import yaml




MAX_TEXT_BYTES = 1_000_000


ALLOWED_TYPES = {
    "dockerfile",
    "dockerignore",
    "helm_chart",
    "helm_values",
    "helm_template",
    "configmap",
    "secret_template",
    "migration_job",
    "gitops_manifest",
    "argocd_project",
    "argocd_application",
}


SECRET_PATTERN = re.compile(
    (
        r"(?im)^\s*"
        r"(?:"
        r"password|"
        r"passwd|"
        r"secret|"
        r"token|"
        r"api[_-]?key|"
        r"private[_-]?key|"
        r"database_url"
        r")"
        r"\s*[:=]\s*"
        r"[^\s<{][^\r\n]*$"
    )
)


IMAGE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$"
)


@dataclass
class RenderedArtifact:
    component_id: int | None

    artifact_type: str
    relative_path: str
    content: str

    artifact_status: str

    metadata: dict[str, Any]

    original_content: str | None = None

    validation_status: str = "pending"

    validation_messages: (
        list[dict[str, Any]]
        | None
    ) = None


    def to_dict(
        self,
    ) -> dict[str, Any]:
        value = asdict(
            self
        )


        value[
            "content_sha256"
        ] = hashlib.sha256(
            self.content.encode(
                "utf-8"
            )
        ).hexdigest()


        value[
            "validation_messages"
        ] = (
            self.validation_messages
            or []
        )


        return value


class ArtifactRenderingError(
    RuntimeError
):
    pass


def render_project_artifacts(
    *,
    source_root: Path,
    contract: dict[str, Any],
    ai_plan: dict[str, Any] | None,
    source_version: str,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
]:
    """
    Génère tous les artefacts du projet.

    Le contrat confirmé reste la source principale.

    Les recommandations IA peuvent uniquement compléter
    certaines informations techniques manquantes. Elles
    ne contrôlent jamais les namespaces, les chemins GitOps,
    les credentials ou les permissions Argo CD.
    """

    source_root = (
        source_root.resolve()
    )


    project = require_dict(
        contract,
        "project",
    )


    target = require_dict(
        contract,
        "target",
    )


    policies = require_dict(
        contract,
        "policies",
    )


    components = contract.get(
        "components"
    )


    if (
        not source_root.is_dir()

        or not isinstance(
            components,
            list,
        )
    ):
        raise ArtifactRenderingError(
            (
                "La source ou la liste "
                "des composants est invalide."
            )
        )


    project_slug = dns_name(
        str(
            project.get("slug")
            or project.get("name")
            or "project"
        )
    )


    environment_code = dns_name(
        str(
            target.get(
                "environmentCode"
            )
            or target.get(
                "environmentName"
            )
            or "environment"
        )
    )


    namespace = str(
        target.get("namespace")
        or ""
    ).strip()


    if not namespace:
        raise ArtifactRenderingError(
            "Le namespace est obligatoire."
        )


    delivery = require_dict(
        target,
        "delivery",
    )


    delivery_base_path = safe_path(
        str(
            delivery.get("basePath")
            or "projects"
        )
    )


    recommendations = {
        int(
            item["componentId"]
        ):
            item

        for item in (
            ai_plan or {}
        ).get(
            "components",
            [],
        )

        if (
            isinstance(
                item,
                dict,
            )

            and isinstance(
                item.get(
                    "componentId"
                ),
                int,
            )
        )
    }


    rendered: list[
        RenderedArtifact
    ] = []


    warnings: list[str] = []


    component_summaries: list[
        dict[str, Any]
    ] = []


    for raw_component in components:
        if (
            not isinstance(
                raw_component,
                dict,
            )

            or not raw_component.get(
                "deployable"
            )
        ):
            continue


        component = dict(
            raw_component
        )


        component_id = int(
            component["id"]
        )


        component_slug = dns_name(
            str(
                component.get("slug")
                or component.get("name")
                or "component"
            )
        )


        chart_root = join_path(
            delivery_base_path,

            (
                f"{project_slug}/"
                f"{environment_code}/"
                f"{component_slug}"
            ),
        )


        (
            artifacts,
            component_warnings,
            effective_port,
        ) = render_component(
            source_root=
                source_root,

            contract=
                contract,

            component=
                component,

            recommendation=
                recommendations.get(
                    component_id,
                    {},
                ),

            project_slug=
                project_slug,

            component_slug=
                component_slug,

            chart_root=
                chart_root,

            source_version=
                source_version,

            preserve_dockerfile=
                bool(
                    policies.get(
                        (
                            "preserveExisting"
                            "Dockerfile"
                        ),
                        True,
                    )
                ),

            preserve_chart=
                bool(
                    policies.get(
                        (
                            "preserveExisting"
                            "HelmChart"
                        ),
                        True,
                    )
                ),

            require_non_root=
                bool(
                    policies.get(
                        "requireNonRoot",
                        True,
                    )
                ),
        )


        rendered.extend(
            artifacts
        )


        warnings.extend(
            component_warnings
        )


        component_summaries.append(
            {
                "componentId":
                    component_id,

                "name":
                    component.get(
                        "name"
                    ),

                "slug":
                    component_slug,

                "chartPath":
                    chart_root,

                "imageRepository":
                    image_repository(
                        contract,
                        project_slug,
                        component_slug,
                    ),

                "containerPort":
                    effective_port,
            }
        )


    if not component_summaries:
        raise ArtifactRenderingError(
            (
                "Aucun composant déployable "
                "n'est présent."
            )
        )


    rendered.append(
        render_argocd_project(
            contract,
            project_slug,
            environment_code,
        )
    )


    rendered.extend(
        render_argocd_application(
            contract,
            project_slug,
            environment_code,
            component_summary,
        )

        for component_summary
        in component_summaries
    )


    artifacts = validate_artifacts(
        [
            item.to_dict()

            for item in rendered
        ]
    )


    validation_counts = {
        "passed":
            0,

        "warning":
            0,

        "failed":
            0,

        "pending":
            0,
    }


    artifact_type_counts:dict[str, int] = {}


    for item in artifacts:
        validation_status = str(
            item.get(
                "validation_status"
            )
            or "pending"
        )


        validation_counts[
            validation_status
        ] = (
            validation_counts.get(
                validation_status,
                0,
            )
            + 1
        )


        artifact_type = str(
            item["artifact_type"]
        )


        artifact_type_counts[
            artifact_type
        ] = (
            artifact_type_counts.get(
                artifact_type,
                0,
            )
            + 1
        )


    summary = {
        "schemaVersion":
            1,

        "project": {
            "id":
                project.get("id"),

            "name":
                project.get("name"),

            "slug":
                project_slug,
        },

        "target": {
            "environmentId":
                target.get(
                    "environmentId"
                ),

            "environmentName":
                target.get(
                    "environmentName"
                ),

            "environmentCode":
                environment_code,

            "namespace":
                namespace,

            "domain":
                target.get("domain"),
        },

        "sourceVersion":
            source_version,

        "componentCount":
            len(
                component_summaries
            ),

        "components":
            component_summaries,

        "artifactCount":
            len(
                artifacts
            ),

        "artifactTypeCounts":
            artifact_type_counts,

        "validationCounts":
            validation_counts,

        "warnings":
            warnings,

        "aiPlanUsed":
            ai_plan is not None,

        "aiQuestions": (
            ai_plan or {}
        ).get(
            "questions",
            [],
        ),

        "aiWarnings": (
            ai_plan or {}
        ).get(
            "warnings",
            [],
        ),

        "readyForReview": (
            validation_counts.get(
                "failed",
                0,
            )
            == 0
        ),

        "nextPhase":
            4,
    }


    return (
        artifacts,
        summary,
    )


def render_component(
    *,
    source_root: Path,
    contract: dict[str, Any],
    component: dict[str, Any],
    recommendation: dict[str, Any],
    project_slug: str,
    component_slug: str,
    chart_root: str,
    source_version: str,
    preserve_dockerfile: bool,
    preserve_chart: bool,
    require_non_root: bool,
) -> tuple[
    list[RenderedArtifact],
    list[str],
    int,
]:
    component_id = int(
        component["id"]
    )


    root_path = safe_path(
        str(
            component.get(
                "rootPath"
            )
            or "."
        )
    )


    component_root = resolve(
        source_root,
        root_path,
    )


    if not component_root.is_dir():
        raise ArtifactRenderingError(
            (
                f"Le dossier {root_path!r} "
                "est introuvable."
            )
        )


    build = require_dict(
        component,
        "build",
    )


    container = require_dict(
        component,
        "container",
    )


    requested_port = int(
        container.get("port")
        or 8000
    )


    component_type = str(
        component.get(
            "componentType"
        )
        or ""
    ).lower()


    effective_port = (
        8080

        if (
            require_non_root

            and component_type
            in {
                "frontend",
                "static",
            }

            and requested_port < 1024
        )

        else requested_port
    )


    warnings: list[str] = []


    if effective_port != requested_port:
        warnings.append(
            (
                f"Le port {requested_port} "
                f"du composant "
                f"{component.get('name')} "
                "a été remplacé par "
                f"{effective_port} pour permettre "
                "une exécution non-root."
            )
        )


    artifacts: list[
        RenderedArtifact
    ] = []


    dockerfile_path = safe_path(
        str(
            build.get(
                "dockerfilePath"
            )
            or join_path(
                root_path,
                "Dockerfile",
            )
        )
    )


    existing_dockerfile = resolve(
        source_root,
        dockerfile_path,
    )


    if (
        preserve_dockerfile

        and existing_dockerfile.is_file()
    ):
        content = read_text(
            existing_dockerfile
        )


        artifacts.append(
            artifact(
                component_id,
                "dockerfile",
                dockerfile_path,
                content,
                "existing",

                {
                    "source":
                        "repository",

                    "effectiveContainerPort":
                        effective_port,
                },

                content,
            )
        )


    else:
        (
            content,
            docker_warnings,
        ) = dockerfile(
            component_root,
            component,
            recommendation,
            effective_port,
            require_non_root,
        )


        warnings.extend(
            docker_warnings
        )


        artifacts.append(
            artifact(
                component_id,
                "dockerfile",
                dockerfile_path,
                content,
                "generated",

                {
                    "generator":
                        "sapixi-v2",

                    "aiRecommendationUsed":
                        bool(
                            recommendation
                        ),

                    "effectiveContainerPort":
                        effective_port,

                    "notes":
                        docker_warnings,

                    "aiEditable":
                        True,

                    "aiRole":
                        "container_build",
                },
            )
        )


    dockerignore_path = join_path(
        root_path,
        ".dockerignore",
    )


    existing_dockerignore = resolve(
        source_root,
        dockerignore_path,
    )


    if existing_dockerignore.is_file():
        content = read_text(
            existing_dockerignore
        )


        artifacts.append(
            artifact(
                component_id,
                "dockerignore",
                dockerignore_path,
                content,
                "existing",
                {},
                content,
            )
        )


    else:
        artifacts.append(
            artifact(
                component_id,
                "dockerignore",
                dockerignore_path,
                DOCKERIGNORE,
                "generated",
                {},
            )
        )


    helm_chart_path = str(
        build.get(
            "helmChartPath"
        )
        or ""
    ).strip()


    existing_chart = (
        resolve(
            source_root,
            safe_path(
                helm_chart_path
            ),
        )

        if helm_chart_path

        else None
    )


    if (
        preserve_chart

        and existing_chart

        and existing_chart.is_dir()
    ):
        existing_chart_files = (
            read_existing_chart(
                source_root=
                    source_root,

                chart_root=
                    existing_chart,

                destination_root=
                    chart_root,

                component_id=
                    component_id,
            )
    )


        if existing_chart_files:
            artifacts.extend(
                existing_chart_files
            )


        else:
            warnings.append(
                (
                    "Le chart existant est vide. "
                    "Un nouveau chart a été généré."
                )
            )


            artifacts.extend(
                helm_chart(
                    contract,
                    component,
                    project_slug,
                    component_slug,
                    chart_root,
                    source_version,
                    effective_port,
                    recommendation,
                )
            )


    else:
        artifacts.extend(
            helm_chart(
                contract,
                component,
                project_slug,
                component_slug,
                chart_root,
                source_version,
                effective_port,
                recommendation,
            )
        )


    return (
        artifacts,
        warnings,
        effective_port,
    )


def dockerfile(
    root: Path,
    component: dict[str, Any],
    recommendation: dict[str, Any],
    port: int,
    require_non_root: bool,
) -> tuple[
    str,
    list[str],
]:
    runtime = require_dict(
        component,
        "runtime",
    )


    build = require_dict(
        component,
        "build",
    )


    container = require_dict(
        component,
        "container",
    )


    ai_recommendation = (
        recommendation.get(
            "docker"
        )

        if isinstance(
            recommendation.get(
                "docker"
            ),
            dict,
        )

        else {}
    )


    runtime_name = str(
        runtime.get("name")
        or ""
    ).lower()


    runtime_version = str(
        runtime.get("version")
        or ""
    ).strip()


    framework = str(
        component.get("framework")
        or ""
    ).lower()


    component_type = str(
        component.get(
            "componentType"
        )
        or ""
    ).lower()


    package_manager = str(
        component.get(
            "packageManager"
        )
        or ""
    ).lower()


    start_command = str(
        container.get(
            "startCommand"
        )
        or ai_recommendation.get(
            "startCommand"
        )
        or ""
    ).strip()


    install_command = str(
        build.get(
            "installCommand"
        )
        or ai_recommendation.get(
            "installCommand"
        )
        or ""
    ).strip()


    build_command = str(
        build.get(
            "buildCommand"
        )
        or ai_recommendation.get(
            "buildCommand"
        )
        or ""
    ).strip()


    output_path = str(
        build.get(
            "outputPath"
        )
        or ai_recommendation.get(
            "outputPath"
        )
        or ""
    ).strip()


    warnings: list[str] = []


    # ========================================================
    # FRONTEND ANGULAR / REACT / VUE
    # ========================================================

    if (
        component_type
        in {
            "frontend",
            "static",
        }

        and (
            any(
                framework_name
                in framework

                for framework_name
                in (
                    "angular",
                    "react",
                    "vue",
                )
            )

            or runtime_name
            in {
                "node",
                "nodejs",
                "javascript",
                "typescript",
            }
        )
    ):
        install_command = (
            install_command

            or node_install(
                root,
                package_manager,
            )
        )


        build_command = (
            build_command

            or "npm run build"
        )


        if not output_path:
            output_path = "dist"


            warnings.append(
                (
                    "Le répertoire de sortie "
                    "frontend n'était pas confirmé. "
                    "Vérifiez la valeur dist."
                )
            )


        content = f"""FROM {image_hint(
            ai_recommendation.get(
                "builderImage"
            ),
            "node:22-alpine",
        )} AS build
WORKDIR /app
{node_copy(root)}
RUN {install_command}
COPY . .
RUN {build_command}

FROM busybox:1.36.1 AS runtime
WORKDIR /www
COPY --from=build /app/{output_path.rstrip("/")} /www
USER 10001:10001
EXPOSE {port}
CMD ["httpd", "-f", "-p", "{port}", "-h", "/www"]
"""


        return (
            content,
            warnings,
        )


    # ========================================================
    # PYTHON / FLASK / DJANGO / FASTAPI
    # ========================================================

    if (
        runtime_name
        in {
            "python",
            "python3",
        }

        or any(
            framework_name
            in framework

            for framework_name
            in (
                "flask",
                "django",
                "fastapi",
            )
        )
    ):
        (
            dependency_copy,
            default_install_command,
        ) = python_dependencies(
            root
        )


        install_command = (
            install_command

            or default_install_command
        )


        if not start_command:
            start_command = (
                "python -m app"
            )


            warnings.append(
                (
                    "La commande Python "
                    "par défaut doit être vérifiée."
                )
            )


        user_creation = (
            (
                "RUN groupadd -g 10001 app "
                "&& useradd -u 10001 "
                "-g app -m app\n"
            )

            if require_non_root

            else ""
        )


        copy_flag = (
            "--chown=10001:10001 "

            if require_non_root

            else ""
        )


        final_user = (
            "USER 10001\n"

            if require_non_root

            else ""
        )


        content = f"""FROM {image_hint(
            ai_recommendation.get(
                "runtimeImage"
            ),
            f"python:{runtime_version or '3.11'}-slim",
        )} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1
WORKDIR /app
{user_creation}{dependency_copy}
RUN {install_command}
COPY {copy_flag}. .
{final_user}EXPOSE {port}
CMD ["sh", "-c", {json.dumps(start_command)}]
"""


        return (
            content,
            warnings,
        )


    # ========================================================
    # NODE.JS BACKEND
    # ========================================================

    if (
        runtime_name
        in {
            "node",
            "nodejs",
            "javascript",
            "typescript",
        }

        or package_manager
        in {
            "npm",
            "yarn",
            "pnpm",
        }
    ):
        install_command = (
            install_command

            or node_install(
                root,
                package_manager,
            )
        )


        start_command = (
            start_command

            or "npm start"
        )


        build_line = (
            f"RUN {build_command}\n"

            if build_command

            else ""
        )


        user_creation = (
            (
                "RUN addgroup -g 10001 -S app "
                "&& adduser -u 10001 -S app "
                "-G app "
                "&& chown -R app:app /app\n"
            )

            if require_non_root

            else ""
        )


        final_user = (
            "USER 10001\n"

            if require_non_root

            else ""
        )


        content = f"""FROM {image_hint(
            ai_recommendation.get(
                "runtimeImage"
            ),
            "node:22-alpine",
        )} AS runtime
WORKDIR /app
{node_copy(root)}
RUN {install_command}
COPY . .
{build_line}{user_creation}{final_user}EXPOSE {port}
CMD ["sh", "-c", {json.dumps(start_command)}]
"""


        return (
            content,
            warnings,
        )


    # ========================================================
    # GO
    # ========================================================

    if runtime_name == "go":
        build_command = (
            build_command

            or (
                "CGO_ENABLED=0 "
                "go build -o /out/app ."
            )
        )


        content = f"""FROM {image_hint(
            ai_recommendation.get(
                "builderImage"
            ),
            "golang:1.24-alpine",
        )} AS build
WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN {build_command}

FROM alpine:3.21 AS runtime
RUN addgroup -g 10001 -S app && adduser -u 10001 -S app -G app
COPY --from=build /out/app /usr/local/bin/app
USER 10001
EXPOSE {port}
ENTRYPOINT ["/usr/local/bin/app"]
"""


        return (
            content,
            warnings,
        )


    # ========================================================
    # JAVA / SPRING
    # ========================================================

    if (
        runtime_name == "java"

        or "spring" in framework
    ):
        build_command = (
            build_command

            or (
                "./gradlew build -x test"

                if (
                    root
                    / "gradlew"
                ).is_file()

                else (
                    "mvn -B "
                    "-DskipTests package"
                )
            )
        )


        warnings.append(
            (
                "Vérifiez le chemin du JAR "
                "produit : target/*.jar."
            )
        )


        content = f"""FROM eclipse-temurin:21-jdk-alpine AS build
WORKDIR /src
COPY . .
RUN {build_command}

FROM eclipse-temurin:21-jre-alpine AS runtime
WORKDIR /app
RUN addgroup -g 10001 -S app && adduser -u 10001 -S app -G app
COPY --from=build /src/target/*.jar /app/application.jar
USER 10001
EXPOSE {port}
ENTRYPOINT ["java", "-jar", "/app/application.jar"]
"""


        return (
            content,
            warnings,
        )


    # ========================================================
    # RUNTIME INCONNU
    # ========================================================

    if not start_command:
        raise ArtifactRenderingError(
            (
                "La commande de démarrage "
                "est obligatoire pour ce runtime."
            )
        )


    warnings.append(
        (
            "Dockerfile générique : "
            "revue humaine obligatoire."
        )
    )


    user_creation = (
        (
            "RUN addgroup -g 10001 -S app "
            "&& adduser -u 10001 -S app "
            "-G app "
            "&& chown -R app:app /app\n"
        )

        if require_non_root

        else ""
    )


    final_user = (
        "USER 10001\n"

        if require_non_root

        else ""
    )


    content = f"""FROM alpine:3.21 AS runtime
WORKDIR /app
COPY . .
{user_creation}{final_user}EXPOSE {port}
CMD ["sh", "-c", {json.dumps(start_command)}]
"""


    return (
        content,
        warnings,
    )


DOCKERIGNORE = """.git
.gitignore
.env
.env.*
!.env.example
*.pem
*.key
*.p12
*.pfx
node_modules
.venv
venv
__pycache__
*.py[cod]
dist
build
target
coverage
.idea
.vscode
.DS_Store
"""
def helm_chart(
    contract: dict[str, Any],
    component: dict[str, Any],
    project_slug: str,
    component_slug: str,
    chart_root: str,
    source_version: str,
    effective_port: int,
    recommendation: dict[str, Any],
) -> list[RenderedArtifact]:
    """
    Construit un chart Helm complet et indépendant
    pour un composant.
    """

    component_id = int(
        component["id"]
    )


    values = build_values(
        contract=
            contract,

        component=
            component,

        project_slug=
            project_slug,

        component_slug=
            component_slug,

        source_version=
            source_version,

        effective_port=
            effective_port,

        recommendation=
            recommendation,
    )


    chart_document = {
        "apiVersion":
            "v2",

        "name":
            component_slug,

        "description": (
            "Chart Helm généré et géré "
            "par SApixi pour "
            f"{component.get('name') or component_slug}."
        ),

        "type":
            "application",

        "version":
            "0.1.0",

        "appVersion":
            source_version[:64]
            or "latest",

        "annotations": {
            "sapixi.io/generated":
                "true",

            "sapixi.io/component-id":
                str(
                    component_id
                ),
        },
    }


    common_metadata = {
        "generator":
            "sapixi-v2",

        "chartRoot":
            chart_root,

        "componentName":
            component.get(
                "name"
            ),

        "aiRecommendationUsed":
            bool(recommendation),
    }


    artifacts = [
        artifact(
            component_id,
            "helm_chart",
            join_path(chart_root, "Chart.yaml"),
            yaml_text(chart_document),
            "generated",
            common_metadata,
        ),
        artifact(
            component_id,
            "helm_values",
            join_path(chart_root, "values.yaml"),
            yaml_text(values),
            "generated",
            common_metadata,
        ),
        artifact(
            component_id,
            "helm_template",
            join_path(chart_root, "templates/_helpers.tpl"),
            HELPERS_TEMPLATE,
            "generated",
            common_metadata,
        ),
        artifact(
            component_id,
            "helm_template",
            join_path(chart_root, "templates/deployment.yaml"),
            DEPLOYMENT_TEMPLATE,
            "generated",
            {
                **common_metadata,
                "aiEditable": True,
                "aiRole": "application_deployment",
            },
        ),
    ]

    # On ne génère plus systématiquement des templates vides/inactifs.
    # La liste des artefacts correspond maintenant réellement au contrat.
    if bool(values.get("service", {}).get("enabled", True)):
        artifacts.append(
            artifact(
                component_id,
                "helm_template",
                join_path(chart_root, "templates/service.yaml"),
                SERVICE_TEMPLATE,
                "generated",
                common_metadata,
            )
        )

    if bool(values.get("ingress", {}).get("enabled", False)):
        artifacts.append(
            artifact(
                component_id,
                "helm_template",
                join_path(chart_root, "templates/ingress.yaml"),
                INGRESS_TEMPLATE,
                "generated",
                common_metadata,
            )
        )

    if bool(values.get("config")):
        artifacts.append(
            artifact(
                component_id,
                "configmap",
                join_path(chart_root, "templates/configmap.yaml"),
                CONFIGMAP_TEMPLATE,
                "generated",
                common_metadata,
            )
        )

    if bool(values.get("secrets", {}).get("values")):
        artifacts.append(
            artifact(
                component_id,
                "secret_template",
                join_path(chart_root, "templates/secret.yaml"),
                SECRET_TEMPLATE,
                "generated",
                {
                    **common_metadata,
                    "containsSecretValues": False,
                    "installedByDefault": False,
                },
            )
        )

    if bool(values.get("persistence")):
        artifacts.append(
            artifact(
                component_id,
                "helm_template",
                join_path(chart_root, "templates/pvc.yaml"),
                PVC_TEMPLATE,
                "generated",
                common_metadata,
            )
        )

    if bool(values.get("migration", {}).get("enabled", False)):
        artifacts.append(
            artifact(
                component_id,
                "migration_job",
                join_path(chart_root, "templates/migration-job.yaml"),
                MIGRATION_JOB_TEMPLATE,
                "generated",
                common_metadata,
            )
        )



    return artifacts


def build_values(
    *,
    contract: dict[str, Any],
    component: dict[str, Any],
    project_slug: str,
    component_slug: str,
    source_version: str,
    effective_port: int,
    recommendation: dict[str, Any],
) -> dict[str, Any]:
    """
    Construit values.yaml depuis le contrat confirmé.

    Les vraies valeurs des secrets ne sont jamais ajoutées.
    Seuls leurs noms apparaissent avec une valeur vide.
    """

    target = require_dict(
        contract,
        "target",
    )


    policies = require_dict(
        contract,
        "policies",
    )


    container = require_dict(
        component,
        "container",
    )


    service = require_dict(
        component,
        "service",
    )


    ingress = require_dict(
        component,
        "ingress",
    )


    resources = require_dict(
        component,
        "resources",
    )


    probes = require_dict(
        component,
        "probes",
    )


    # Les décisions d'infrastructure confirmées par l'utilisateur restent
    # prioritaires. En revanche, les chemins de probes sont une information
    # technique dérivée du code : Qwen peut les corriger lorsqu'il possède
    # une recommandation suffisamment fiable.
    ai_kubernetes = (
        recommendation.get("kubernetes")
        if isinstance(recommendation.get("kubernetes"), dict)
        else {}
    )

    try:
        ai_confidence = int(recommendation.get("confidence") or 0)
    except (TypeError, ValueError):
        ai_confidence = 0

    if ai_confidence >= 70:
        probe_mapping = {
            "startup": "startupPath",
            "readiness": "readinessPath",
            "liveness": "livenessPath",
        }

        for probe_name, ai_key in probe_mapping.items():
            ai_path = str(ai_kubernetes.get(ai_key) or "").strip()
            if not ai_path:
                continue

            raw_probe = probes.get(probe_name)
            probe = dict(raw_probe) if isinstance(raw_probe, dict) else {}
            probe["enabled"] = True
            probe["path"] = ai_path
            probes[probe_name] = probe


    migration = require_dict(
        component,
        "migration",
    )


    config_values: dict[str, str] = {}


    for item in (
        component.get(
            "configuration"
        )
        or []
    ):
        if not isinstance(
            item,
            dict,
        ):
            continue


        name = str(
            item.get("name")
            or ""
        ).strip()


        if name:
            config_values[
                name
            ] = str(
                item.get("value")
                or ""
            )


    secret_values:dict[str, str] = {}


    for item in (
        component.get(
            "secrets"
        )
        or []
    ):
        if not isinstance(
            item,
            dict,
        ):
            continue


        name = str(
            item.get("name")
            or ""
        ).strip()


        if name:
            secret_values[
                name
            ] = ""


    persistence:list[dict[str, Any]] = []


    for item in (
        component.get(
            "volumes"
        )
        or []
    ):
        if not isinstance(
            item,
            dict,
        ):
            continue


        persistence.append(
            {
                "name":
                    dns_name(
                        str(
                            item.get(
                                "name"
                            )
                            or "data"
                        )
                    ),

                "mountPath":
                    str(
                        item.get(
                            "mountPath"
                        )
                        or ""
                    ),

                "size":
                    str(
                        item.get(
                            "size"
                        )
                        or "1Gi"
                    ),

                "accessMode":
                    str(
                        item.get(
                            "accessMode"
                        )
                        or "ReadWriteOnce"
                    ),

                "storageClass":
                    str(
                        item.get(
                            "storageClass"
                        )
                        or ""
                    ),

                "readOnly":
                    bool(
                        item.get(
                            "readOnly",
                            False,
                        )
                    ),
            }
        )


    image_pull_secret = str(
        require_dict(
            target,
            "registry",
        ).get(
            "imagePullSecretName"
        )
        or ""
    ).strip()


    return {
        "nameOverride":
            component_slug,

        "fullnameOverride":
            (
                f"{project_slug}-"
                f"{component_slug}"
            ),

        "replicaCount":
            max(
                1,

                int(
                    component.get(
                        "replicas"
                    )
                    or 1
                ),
            ),


        "image": {
            "repository":
                image_repository(
                    contract,
                    project_slug,
                    component_slug,
                ),

            "tag":
                safe_image_tag(
                    source_version
                ),

            "pullPolicy":
                "IfNotPresent",
        },


        "imagePullSecrets": (
            [
                {
                    "name":
                        image_pull_secret,
                }
            ]

            if image_pull_secret

            else []
        ),


        "podAnnotations": {
            "sapixi.io/project":
                project_slug,

            "sapixi.io/component":
                component_slug,
        },


        "podSecurityContext": {
            "runAsNonRoot":
                bool(
                    policies.get(
                        "requireNonRoot",
                        True,
                    )
                ),

            "runAsUser":
                int(
                    container.get(
                        "runAsUser"
                    )
                    or 10001
                ),

            "runAsGroup":
                int(
                    container.get(
                        "runAsUser"
                    )
                    or 10001
                ),

            "fsGroup":
                int(
                    container.get(
                        "runAsUser"
                    )
                    or 10001
                ),

            "seccompProfile": {
                "type":
                    "RuntimeDefault",
            },
        },


        "securityContext": {
            "allowPrivilegeEscalation":
                False,

            "readOnlyRootFilesystem":
                bool(
                    container.get(
                        (
                            "readOnlyRoot"
                            "Filesystem"
                        ),
                        False,
                    )
                ),

            "runAsNonRoot":
                bool(
                    policies.get(
                        "requireNonRoot",
                        True,
                    )
                ),

            "runAsUser":
                int(
                    container.get(
                        "runAsUser"
                    )
                    or 10001
                ),

            "capabilities": {
                "drop": [
                    "ALL",
                ],
            },
        },


        "service": {
            "enabled":
                bool(
                    service.get(
                        "enabled",
                        True,
                    )
                ),

            "type":
                str(
                    service.get(
                        "type"
                    )
                    or "ClusterIP"
                ),

            "port":
                int(
                    service.get(
                        "port"
                    )
                    or effective_port
                ),

            "targetPort":
                effective_port,
        },


        "ingress": {
            "enabled":
                bool(
                    ingress.get(
                        "enabled",
                        False,
                    )
                ),

            "className":
                str(
                    ingress.get(
                        "className"
                    )
                    or "nginx"
                ),

            "annotations":
                dict(
                    ingress.get(
                        "annotations"
                    )
                    or {}
                ),

            "host":
                str(
                    ingress.get(
                        "host"
                    )
                    or ""
                ),

            "path":
                str(
                    ingress.get(
                        "path"
                    )
                    or "/"
                ),

            "pathType":
                str(
                    ingress.get(
                        "pathType"
                    )
                    or "Prefix"
                ),

            "tls": (
                [
                    {
                        "secretName":
                            str(
                                ingress.get(
                                    "tlsSecretName"
                                )
                            ),

                        "hosts": [
                            str(
                                ingress.get(
                                    "host"
                                )
                                or ""
                            ),
                        ],
                    }
                ]

                if ingress.get(
                    "tlsSecretName"
                )

                else []
            ),
        },


        "resources":
            resources,


        "probes":
            probes,


        "config":
            config_values,


        "secrets": {
            "create":
                False,

            "existingSecretName":
                "",

            "values":
                secret_values,
        },


        "persistence":
            persistence,


        "migration": {
            "enabled":
                bool(
                    migration.get(
                        "enabled",
                        False,
                    )
                ),

            "command":
                str(
                    migration.get(
                        "command"
                    )
                    or ""
                ),

            "backoffLimit":
                max(
                    0,

                    int(
                        migration.get(
                            "backoffLimit"
                        )
                        or 1
                    ),
                ),
        },


        "nodeSelector":
            {},

        "tolerations":
            [],

        "affinity":
            {},
    }


HELPERS_TEMPLATE = r'''{{- define "sapixi.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "sapixi.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "sapixi.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "sapixi.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
app.kubernetes.io/name: {{ include "sapixi.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
sapixi.io/managed: "true"
{{- end }}

{{- define "sapixi.selectorLabels" -}}
app.kubernetes.io/name: {{ include "sapixi.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
'''


DEPLOYMENT_TEMPLATE = r'''apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "sapixi.fullname" . }}
  labels:
    {{- include "sapixi.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  revisionHistoryLimit: 3
  selector:
    matchLabels:
      {{- include "sapixi.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "sapixi.selectorLabels" . | nindent 8 }}
      {{- with .Values.podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
    spec:
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
      containers:
        - name: {{ .Chart.Name }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          securityContext:
            {{- toYaml .Values.securityContext | nindent 12 }}
          ports:
            - name: http
              containerPort: {{ .Values.service.targetPort }}
              protocol: TCP
          {{- if or .Values.config .Values.secrets.existingSecretName .Values.secrets.create }}
          envFrom:
            {{- if .Values.config }}
            - configMapRef:
                name: {{ include "sapixi.fullname" . }}-config
            {{- end }}
            {{- if .Values.secrets.existingSecretName }}
            - secretRef:
                name: {{ .Values.secrets.existingSecretName }}
            {{- else if .Values.secrets.create }}
            - secretRef:
                name: {{ include "sapixi.fullname" . }}-secret
            {{- end }}
          {{- end }}
          {{- if .Values.probes.startup.enabled }}
          startupProbe:
            httpGet:
              path: {{ .Values.probes.startup.path | quote }}
              port: http
            initialDelaySeconds: {{ .Values.probes.startup.initialDelaySeconds }}
            periodSeconds: {{ .Values.probes.startup.periodSeconds }}
            timeoutSeconds: {{ .Values.probes.startup.timeoutSeconds }}
            failureThreshold: {{ .Values.probes.startup.failureThreshold }}
          {{- end }}
          {{- if .Values.probes.readiness.enabled }}
          readinessProbe:
            httpGet:
              path: {{ .Values.probes.readiness.path | quote }}
              port: http
            initialDelaySeconds: {{ .Values.probes.readiness.initialDelaySeconds }}
            periodSeconds: {{ .Values.probes.readiness.periodSeconds }}
            timeoutSeconds: {{ .Values.probes.readiness.timeoutSeconds }}
            failureThreshold: {{ .Values.probes.readiness.failureThreshold }}
          {{- end }}
          {{- if .Values.probes.liveness.enabled }}
          livenessProbe:
            httpGet:
              path: {{ .Values.probes.liveness.path | quote }}
              port: http
            initialDelaySeconds: {{ .Values.probes.liveness.initialDelaySeconds }}
            periodSeconds: {{ .Values.probes.liveness.periodSeconds }}
            timeoutSeconds: {{ .Values.probes.liveness.timeoutSeconds }}
            failureThreshold: {{ .Values.probes.liveness.failureThreshold }}
          {{- end }}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          {{- with .Values.persistence }}
          volumeMounts:
            {{- range . }}
            - name: {{ .name }}
              mountPath: {{ .mountPath | quote }}
              readOnly: {{ .readOnly }}
            {{- end }}
          {{- end }}
      {{- with .Values.persistence }}
      volumes:
        {{- range . }}
        - name: {{ .name }}
          persistentVolumeClaim:
            claimName: {{ include "sapixi.fullname" $ }}-{{ .name }}
        {{- end }}
      {{- end }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
'''


SERVICE_TEMPLATE = r'''{{- if .Values.service.enabled }}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "sapixi.fullname" . }}
  labels:
    {{- include "sapixi.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type }}
  ports:
    - name: http
      port: {{ .Values.service.port }}
      targetPort: http
      protocol: TCP
  selector:
    {{- include "sapixi.selectorLabels" . | nindent 4 }}
{{- end }}
'''


INGRESS_TEMPLATE = r'''{{- if .Values.ingress.enabled }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "sapixi.fullname" . }}
  labels:
    {{- include "sapixi.labels" . | nindent 4 }}
  {{- with .Values.ingress.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  ingressClassName: {{ .Values.ingress.className | quote }}
  {{- with .Values.ingress.tls }}
  tls:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  rules:
    - host: {{ .Values.ingress.host | quote }}
      http:
        paths:
          - path: {{ .Values.ingress.path | quote }}
            pathType: {{ .Values.ingress.pathType }}
            backend:
              service:
                name: {{ include "sapixi.fullname" . }}
                port:
                  name: http
{{- end }}
'''


CONFIGMAP_TEMPLATE = r'''{{- if .Values.config }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "sapixi.fullname" . }}-config
  labels:
    {{- include "sapixi.labels" . | nindent 4 }}
data:
  {{- range $name, $value := .Values.config }}
  {{ $name }}: {{ $value | quote }}
  {{- end }}
{{- end }}
'''


SECRET_TEMPLATE = r'''{{- if .Values.secrets.create }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "sapixi.fullname" . }}-secret
  labels:
    {{- include "sapixi.labels" . | nindent 4 }}
type: Opaque
stringData:
  {{- range $name, $value := .Values.secrets.values }}
  {{ $name }}: {{ required (printf "La valeur du secret %s est obligatoire" $name) $value | quote }}
  {{- end }}
{{- end }}
'''


PVC_TEMPLATE = r'''{{- range .Values.persistence }}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "sapixi.fullname" $ }}-{{ .name }}
  labels:
    {{- include "sapixi.labels" $ | nindent 4 }}
spec:
  accessModes:
    - {{ .accessMode }}
  {{- if .storageClass }}
  storageClassName: {{ .storageClass | quote }}
  {{- end }}
  resources:
    requests:
      storage: {{ .size }}
{{- end }}
'''


MIGRATION_JOB_TEMPLATE = r'''{{- if .Values.migration.enabled }}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "sapixi.fullname" . }}-migration-{{ .Release.Revision }}
  labels:
    {{- include "sapixi.labels" . | nindent 4 }}
  annotations:
    argocd.argoproj.io/hook: PreSync
    argocd.argoproj.io/hook-delete-policy: BeforeHookCreation,HookSucceeded
    argocd.argoproj.io/sync-wave: "-1"
spec:
  backoffLimit: {{ .Values.migration.backoffLimit }}
  template:
    metadata:
      labels:
        {{- include "sapixi.selectorLabels" . | nindent 8 }}
    spec:
      restartPolicy: Never
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
      containers:
        - name: migration
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          securityContext:
            {{- toYaml .Values.securityContext | nindent 12 }}
          command:
            - sh
            - -c
            - {{ .Values.migration.command | quote }}
          {{- if or .Values.config .Values.secrets.existingSecretName .Values.secrets.create }}
          envFrom:
            {{- if .Values.config }}
            - configMapRef:
                name: {{ include "sapixi.fullname" . }}-config
            {{- end }}
            {{- if .Values.secrets.existingSecretName }}
            - secretRef:
                name: {{ .Values.secrets.existingSecretName }}
            {{- else if .Values.secrets.create }}
            - secretRef:
                name: {{ include "sapixi.fullname" . }}-secret
            {{- end }}
          {{- end }}
{{- end }}
'''


def render_argocd_project(
    contract: dict[str, Any],
    project_slug: str,
    environment_code: str,
) -> RenderedArtifact:
    """
    Construit l'AppProject Argo CD.

    Cette ressource est écrite par SApixi et non
    librement par le modèle IA.
    """

    target = require_dict(
        contract,
        "target",
    )


    delivery = require_dict(
        target,
        "delivery",
    )


    argocd = require_dict(
        target,
        "argocd",
    )


    kubernetes = require_dict(
        target,
        "kubernetes",
    )


    project_name = dns_name(
        str(
            argocd.get(
                "projectName"
            )
            or (
                f"sapixi-"
                f"{project_slug}-"
                f"{environment_code}"
            )
        )
    )


    namespace = str(
        target.get(
            "namespace"
        )
        or ""
    ).strip()


    repository_url = str(
        delivery.get(
            "repositoryUrl"
        )
        or ""
    ).strip()


    cluster_server = str(
        kubernetes.get(
            "server"
        )
        or (
            "https://"
            "kubernetes.default.svc"
        )
    ).strip()


    document = {
        "apiVersion":
            "argoproj.io/v1alpha1",

        "kind":
            "AppProject",

        "metadata": {
            "name":
                project_name,

            "namespace":
                str(
                    argocd.get(
                        "namespace"
                    )
                    or "argocd"
                ),

            "labels": {
                "sapixi.io/managed":
                    "true",

                "sapixi.io/project":
                    project_slug,

                "sapixi.io/environment":
                    environment_code,
            },
        },

        "spec": {
            "description": (
                "Projet Argo CD géré "
                "par SApixi pour "
                f"{project_slug} dans "
                f"{environment_code}."
            ),

            "sourceRepos": [
                repository_url,
            ],

            "destinations": [
                {
                    "server":
                        cluster_server,

                    "namespace":
                        namespace,
                }
            ],

            "clusterResourceWhitelist": [
                {
                    "group":
                        "",

                    "kind":
                        "Namespace",
                }
            ],

            "namespaceResourceWhitelist": [
                {
                    "group":
                        "*",

                    "kind":
                        "*",
                }
            ],

            "orphanedResources": {
                "warn":
                    True,
            },
        },
    }


    return artifact(
        None,
        "argocd_project",

        join_path(
            "argocd/projects",
            f"{project_name}.yaml",
        ),

        yaml_text(
            document
        ),

        "generated",

        {
            "generator":
                "sapixi-v2",

            "argocdProjectName":
                project_name,

            "destinationNamespace":
                namespace,

            "deliveryMode":
                str(delivery.get("mode") or "git"),
        },
    )


def render_argocd_application(
    contract: dict[str, Any],
    project_slug: str,
    environment_code: str,
    component_summary: dict[str, Any],
) -> RenderedArtifact:
    """
    Crée une Application Argo CD pour un composant.

    L'auto-sync est ajouté uniquement si :
    - automaticSync est activé ;
    - la politique de confirmation manuelle est désactivée.
    """

    target = require_dict(
        contract,
        "target",
    )


    policies = require_dict(
        contract,
        "policies",
    )


    delivery = require_dict(
        target,
        "delivery",
    )


    argocd = require_dict(
        target,
        "argocd",
    )


    kubernetes = require_dict(
        target,
        "kubernetes",
    )


    component_slug = dns_name(
        str(
            component_summary[
                "slug"
            ]
        )
    )


    application_name = dns_name(
        (
            f"{project_slug}-"
            f"{component_slug}-"
            f"{environment_code}"
        )
    )


    argocd_project_name = dns_name(
        str(
            argocd.get(
                "projectName"
            )
            or (
                f"sapixi-"
                f"{project_slug}-"
                f"{environment_code}"
            )
        )
    )


    sync_policy: dict[str, Any] = {
        "syncOptions": [
            "CreateNamespace=true",
            "ApplyOutOfSyncOnly=true",
        ],
    }


    allow_automatic = (
        bool(
            argocd.get(
                "automaticSync",
                False,
            )
        )

        and not bool(
            policies.get(
                "requireManualArgoSync",
                True,
            )
        )
    )


    if allow_automatic:
        sync_policy[
            "automated"
        ] = {
            "prune":
                bool(
                    argocd.get(
                        "prune",
                        False,
                    )
                ),

            "selfHeal":
                bool(
                    argocd.get(
                        "selfHeal",
                        False,
                    )
                ),

            "allowEmpty":
                False,
        }


    document = {
        "apiVersion":
            "argoproj.io/v1alpha1",

        "kind":
            "Application",

        "metadata": {
            "name":
                application_name,

            "namespace":
                str(
                    argocd.get(
                        "namespace"
                    )
                    or "argocd"
                ),

            "finalizers": [
                (
                    "resources-finalizer."
                    "argocd.argoproj.io"
                ),
            ],

            "labels": {
                "sapixi.io/managed":
                    "true",

                "sapixi.io/project":
                    project_slug,

                "sapixi.io/environment":
                    environment_code,

                "sapixi.io/component":
                    component_slug,
            },
        },

        "spec": {
            "project":
                argocd_project_name,

            "source": (
                {
                    "repoURL": str(delivery.get("repositoryUrl") or ""),
                    "targetRevision": str(delivery.get("targetRevision") or "main"),
                    "path": str(component_summary["chartPath"]),
                    "helm": {
                        "releaseName": dns_name(f"{project_slug}-{component_slug}"),
                    },
                }
                if str(delivery.get("mode") or "git") == "git"
                else {
                    "repoURL": str(delivery.get("repositoryUrl") or ""),
                    "chart": component_slug,
                    "targetRevision": str(
                        delivery.get("targetRevision")
                        or "__SAPIXI_HELM_VERSION__"
                    ),
                    "helm": {
                        "releaseName": dns_name(f"{project_slug}-{component_slug}"),
                    },
                }
            ),

            "destination": {
                "server":
                    str(
                        kubernetes.get(
                            "server"
                        )
                        or (
                            "https://"
                            "kubernetes.default.svc"
                        )
                    ),

                "namespace":
                    str(
                        target.get(
                            "namespace"
                        )
                        or ""
                    ),
            },

            "syncPolicy":
                sync_policy,

            "revisionHistoryLimit":
                10,
        },
    }


    return artifact(
        int(
            component_summary[
                "componentId"
            ]
        ),

        "argocd_application",

        join_path(
            "argocd/applications",
            f"{application_name}.yaml",
        ),

        yaml_text(
            document
        ),

        "generated",

        {
            "generator":
                "sapixi-v2",

            "applicationName":
                application_name,

            "argocdProjectName":
                argocd_project_name,

            "automaticSync":
                allow_automatic,

            "deliveryMode":
                str(delivery.get("mode") or "git"),

            "chartPath":
                component_summary[
                    "chartPath"
                ],
        },
    )


def read_existing_chart(
    *,
    source_root: Path,
    chart_root: Path,
    destination_root: str,
    component_id: int,
) -> list[RenderedArtifact]:
    """
    Copie un chart existant vers le chemin GitOps
    déterministe.

    Les fichiers symboliques et trop volumineux
    sont ignorés.
    """

    source_root = (
        source_root.resolve()
    )


    chart_root = (
        chart_root.resolve()
    )


    try:
        chart_root.relative_to(
            source_root
        )

    except ValueError as error:
        raise ArtifactRenderingError(
            (
                "Le chart Helm sort "
                "du repository autorisé."
            )
        ) from error


    results: list[RenderedArtifact] = []


    for path in sorted(
        chart_root.rglob("*")
    ):
        if (
            not path.is_file()

            or path.is_symlink()
        ):
            continue


        relative_in_chart = (
            path.relative_to(
                chart_root
            )
        )


        if any(
            part.startswith(".")

            for part
            in relative_in_chart.parts
        ):
            continue


        if (
            path.stat().st_size
            > MAX_TEXT_BYTES
        ):
            continue


        content = read_text(
            path
        )


        destination = join_path(
            destination_root,

            relative_in_chart
            .as_posix(),
        )


        artifact_type = (
            existing_chart_artifact_type(
                relative_in_chart
            )
        )


        results.append(
            artifact(
                component_id,
                artifact_type,
                destination,
                content,
                "existing",

                {
                    "source":
                        "repository",

                    "originalPath":
                        str(
                            path.relative_to(
                                source_root
                            )
                        ).replace(
                            "\\",
                            "/",
                        ),

                    "chartRoot":
                        destination_root,
                },

                content,
            )
        )


    if (
        results

        and not any(
            item.relative_path.endswith(
                "/Chart.yaml"
            )

            or item.relative_path
            == "Chart.yaml"

            for item in results
        )
    ):
        raise ArtifactRenderingError(
            (
                "Le dossier Helm existant "
                "ne contient pas Chart.yaml."
            )
        )


    return results


def existing_chart_artifact_type(
    path: PurePosixPath,
) -> str:
    lower = (
        path.as_posix()
        .lower()
    )


    name = (
        path.name.lower()
    )


    if name == "chart.yaml":
        return "helm_chart"


    if (
        name.startswith(
            "values"
        )

        and path.suffix.lower()
        in {
            ".yaml",
            ".yml",
        }
    ):
        return "helm_values"


    if "secret" in name:
        return "secret_template"


    if "configmap" in name:
        return "configmap"


    if (
        "migration" in name

        or name == "job.yaml"
    ):
        return "migration_job"


    if lower.startswith(
        "templates/"
    ):
        return "helm_template"


    return "gitops_manifest"


def apply_ai_artifact_revision(
    *,
    artifacts: list[dict[str, Any]],
    revision: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Applique les fichiers COMPLETS retournés par Qwen uniquement sur les
    artefacts marqués aiEditable par le renderer.

    Si une révision rend le bundle invalide, toutes les révisions IA sont
    rejetées et SApixi revient automatiquement aux templates sûrs.
    """

    base_artifacts = [dict(item) for item in artifacts]
    if not isinstance(revision, dict):
        return base_artifacts, {
            "applied": False,
            "modifiedPaths": [],
            "rejected": False,
        }

    raw_revisions = revision.get("artifacts")
    if not isinstance(raw_revisions, list):
        raw_revisions = []

    by_path = {
        str(item.get("relative_path") or ""): item
        for item in base_artifacts
    }

    modified_paths: list[str] = []
    policy_rejected_paths: list[str] = []

    for raw_revision in raw_revisions:
        if not isinstance(raw_revision, dict):
            continue

        if str(raw_revision.get("action") or "") != "replace":
            continue

        path = str(raw_revision.get("relativePath") or "").replace("\\", "/").strip()
        target = by_path.get(path)
        if target is None:
            continue

        metadata = target.get("metadata")
        if not isinstance(metadata, dict) or not bool(metadata.get("aiEditable")):
            continue

        candidate = str(raw_revision.get("content") or "")
        if not candidate.strip():
            continue

        # Garde-fous supplémentaires avant les validateurs Docker/Helm.
        forbidden = (
            r"(?im)^\s*privileged\s*:\s*true\s*$",
            r"(?im)^\s*hostNetwork\s*:\s*true\s*$",
            r"(?im)^\s*hostPID\s*:\s*true\s*$",
            r"(?im)^\s*hostPath\s*:\s*$",
            r"(?im)^\s*runAsNonRoot\s*:\s*false\s*$",
        )

        if any(re.search(pattern, candidate) for pattern in forbidden):
            policy_rejected_paths.append(path)
            continue

        original_content = str(target.get("content") or "")
        target["original_content"] = (
            target.get("original_content")
            if target.get("original_content") is not None
            else original_content
        )
        target["content"] = candidate
        target["artifact_status"] = "ai_modified"
        target["metadata"] = {
            **metadata,
            "aiModified": True,
            "aiRevisionReason": str(raw_revision.get("reason") or ""),
            "aiChanges": list(raw_revision.get("changes") or []),
        }
        modified_paths.append(path)

    if not modified_paths:
        return base_artifacts, {
            "applied": False,
            "modifiedPaths": [],
            "rejected": bool(policy_rejected_paths),
            "policyRejectedPaths": policy_rejected_paths,
            "summary": str(revision.get("summary") or ""),
        }

    revised = validate_artifacts(base_artifacts)

    failed = [
        item
        for item in revised
        if str(item.get("validation_status") or "") == "failed"
    ]

    if failed:
        # Un seul fichier IA invalide ne doit jamais casser une génération.
        # On revient au bundle déterministe complet puis on le revalide.
        safe = validate_artifacts(artifacts)
        return safe, {
            "applied": False,
            "modifiedPaths": modified_paths,
            "rejected": True,
            "summary": str(revision.get("summary") or ""),
            "failedPaths": [
                str(item.get("relative_path") or "")
                for item in failed
            ],
            "policyRejectedPaths": policy_rejected_paths,
        }

    return revised, {
        "applied": True,
        "modifiedPaths": modified_paths,
        "rejected": False,
        "policyRejectedPaths": policy_rejected_paths,
        "summary": str(revision.get("summary") or ""),
    }


def ai_editable_artifacts(
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Retourne uniquement les artefacts que Qwen a le droit de réviser."""

    result: list[dict[str, Any]] = []

    for item in artifacts:
        metadata = item.get("metadata")
        if not isinstance(metadata, dict) or not bool(metadata.get("aiEditable")):
            continue

        result.append(
            {
                "componentId": item.get("component_id"),
                "artifactType": str(item.get("artifact_type") or ""),
                "relativePath": str(item.get("relative_path") or ""),
                "baseContent": str(item.get("content") or ""),
                "role": str(metadata.get("aiRole") or ""),
            }
        )

    return result


def validate_artifacts(
    artifacts: list[
        dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    """
    Valide tous les fichiers générés.

    Les validations Helm réelles sont exécutées
    lorsque la commande helm est installée.
    """

    normalized:list[dict[str, Any]] = []


    seen_paths:set[str] = set()


    for raw_artifact in artifacts:
        item = dict(
            raw_artifact
        )


        artifact_type = str(
            item.get(
                "artifact_type"
            )
            or ""
        )


        relative_path = safe_path(
            str(
                item.get(
                    "relative_path"
                )
                or ""
            )
        )


        content = str(
            item.get(
                "content"
            )
            or ""
        )


        if (
            artifact_type
            not in ALLOWED_TYPES
        ):
            raise ArtifactRenderingError(
                (
                    "Type d'artefact "
                    "non autorisé : "
                    f"{artifact_type!r}."
                )
            )


        if relative_path in seen_paths:
            raise ArtifactRenderingError(
                (
                    "Deux artefacts utilisent "
                    "le chemin "
                    f"{relative_path!r}."
                )
            )


        seen_paths.add(
            relative_path
        )


        item[
            "relative_path"
        ] = relative_path


        item["content"] = (
            content
        )


        existing_messages = item.get(
            "validation_messages"
        )


        messages = (
            list(
                existing_messages
            )

            if isinstance(
                existing_messages,
                list,
            )

            else []
        )


        (
            _status,
            static_messages,
        ) = validate_artifact_content(
            artifact_type=
                artifact_type,

            relative_path=
                relative_path,

            content=
                content,

            metadata=(
                item.get(
                    "metadata"
                )

                if isinstance(
                    item.get(
                        "metadata"
                    ),
                    dict,
                )

                else {}
            ),
        )


        messages.extend(
            static_messages
        )


        item[
            "validation_messages"
        ] = deduplicate_messages(
            messages
        )


        item[
            "validation_status"
        ] = (
            validation_status_from_messages(
                item[
                    "validation_messages"
                ]
            )
        )


        item[
            "content_sha256"
        ] = hashlib.sha256(
            content.encode(
                "utf-8"
            )
        ).hexdigest()


        normalized.append(
            item
        )


    apply_helm_cli_validation(
        normalized
    )


    for item in normalized:
        item[
            "validation_messages"
        ] = deduplicate_messages(
            item.get(
                "validation_messages"
            )
            or []
        )


        item[
            "validation_status"
        ] = (
            validation_status_from_messages(
                item[
                    "validation_messages"
                ]
            )
        )


    return normalized


def validate_artifact_content(
    *,
    artifact_type: str,
    relative_path: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[
    str,
    list[dict[str, Any]],
]:
    """
    Validation réutilisable après une modification
    humaine dans la phase 4.
    """

    del metadata


    messages:list[dict[str, Any]] = []


    try:
        safe_path(
            relative_path
        )

    except ArtifactRenderingError as error:
        messages.append(
            validation_message(
                "error",
                "PATH_INVALID",
                str(error),
            )
        )


    if not content.strip():
        messages.append(
            validation_message(
                "error",
                "EMPTY_ARTIFACT",
                "Le fichier est vide.",
            )
        )


        return (
            "failed",
            messages,
        )


    encoded = content.encode(
        "utf-8"
    )


    if (
        len(encoded)
        > MAX_TEXT_BYTES
    ):
        messages.append(
            validation_message(
                "error",
                "ARTIFACT_TOO_LARGE",

                (
                    "Le fichier dépasse "
                    f"{MAX_TEXT_BYTES} octets."
                ),
            )
        )


    if "\x00" in content:
        messages.append(
            validation_message(
                "error",
                "NUL_BYTE_FORBIDDEN",

                (
                    "Le fichier contient "
                    "un octet NUL."
                ),
            )
        )


    if (
        artifact_type
        != "secret_template"

        and contains_possible_secret_value(
            content
        )
    ):
        messages.append(
            validation_message(
                "error",
                "POSSIBLE_SECRET_VALUE",

                (
                    "Une valeur sensible "
                    "semble être écrite "
                    "en clair."
                ),
            )
        )


    if artifact_type == "dockerfile":
        messages.extend(
            validate_dockerfile_content(
                content
            )
        )


    elif artifact_type in {
        "helm_chart",
        "helm_values",
        "argocd_project",
        "argocd_application",
        "gitops_manifest",
    }:
        messages.extend(
            validate_static_yaml(
                content
            )
        )


    elif artifact_type in {
        "helm_template",
        "configmap",
        "secret_template",
        "migration_job",
    }:
        messages.extend(
            validate_helm_template_content(
                content
            )
        )


    if not any(
        message.get(
            "level"
        )
        in {
            "warning",
            "error",
        }

        for message
        in messages
    ):
        messages.append(
            validation_message(
                "info",
                (
                    "STATIC_VALIDATION_"
                    "PASSED"
                ),
                (
                    "Validation statique "
                    "réussie."
                ),
            )
        )


    return (
        validation_status_from_messages(
            messages
        ),

        messages,
    )


def contains_possible_secret_value(
    content: str,
) -> bool:
    """
    Détecte une valeur sensible non vide.

    Les valeurs vides et les placeholders sont autorisés.
    """

    assignment = re.compile(
        (
            r"(?im)^\s*"
            r"(password|passwd|secret|token|"
            r"api[_-]?key|private[_-]?key|"
            r"database_url)"
            r"\s*[:=]\s*"
            r"(.*?)\s*$"
        )
    )


    harmless_values = {
        "",
        "null",
        "~",
        "changeme",
        "change-me",
        "replace-me",
        "placeholder",
        "example",
    }


    for match in assignment.finditer(
        content
    ):
        raw_value = (
            match.group(2)
            .strip()
        )


        normalized = (
            raw_value
            .strip("'\"")
            .strip()
            .lower()
        )


        if normalized in harmless_values:
            continue


        if raw_value.startswith(
            (
                "<",
                "${",
                "{{",
            )
        ):
            continue


        if normalized.startswith(
            (
                "replace_",
                "replace-",
                "example_",
                "example-",
            )
        ):
            continue


        return True


    return False


def validate_dockerfile_content(
    content: str,
) -> list[dict[str, Any]]:
    messages:list[dict[str, Any]] = []


    upper = content.upper()


    if not re.search(
        r"(?im)^\s*FROM\s+\S+",
        content,
    ):
        messages.append(
            validation_message(
                "error",
                "DOCKER_FROM_REQUIRED",

                (
                    "Le Dockerfile ne contient "
                    "aucune instruction FROM."
                ),
            )
        )


    if re.search(
        (
            r"(?im)^\s*"
            r"(?:COPY|ADD)\s+"
            r".*\.env(?:\s|$)"
        ),
        content,
    ):
        messages.append(
            validation_message(
                "error",
                (
                    "DOCKER_ENV_COPY_"
                    "FORBIDDEN"
                ),
                (
                    "Le Dockerfile tente "
                    "de copier un fichier .env."
                ),
            )
        )


    if re.search(
        r"(?im)^\s*ADD\s+https?://",
        content,
    ):
        messages.append(
            validation_message(
                "warning",
                "DOCKER_REMOTE_ADD",

                (
                    "Évitez ADD avec "
                    "une URL distante."
                ),
            )
        )


    if re.search(
        (
            r"(?i)"
            r"(curl|wget)"
            r"[^\n|]*"
            r"\|\s*(?:sh|bash)"
        ),
        content,
    ):
        messages.append(
            validation_message(
                "warning",
                "DOCKER_PIPE_TO_SHELL",

                (
                    "Une commande distante "
                    "est envoyée directement "
                    "au shell."
                ),
            )
        )


    if "USER " not in upper:
        messages.append(
            validation_message(
                "warning",
                (
                    "DOCKER_USER_"
                    "NOT_EXPLICIT"
                ),
                (
                    "Aucun utilisateur "
                    "non-root explicite "
                    "n'est défini."
                ),
            )
        )


    if not re.search(
        (
            r"(?im)^\s*"
            r"(?:CMD|ENTRYPOINT)"
            r"\s+"
        ),
        content,
    ):
        messages.append(
            validation_message(
                "warning",
                (
                    "DOCKER_START_COMMAND_"
                    "MISSING"
                ),
                (
                    "Aucune instruction CMD "
                    "ou ENTRYPOINT "
                    "n'est présente."
                ),
            )
        )


    messages.append(
        validation_message(
            "warning",
            (
                "DOCKER_BUILD_"
                "NOT_EXECUTED"
            ),
            (
                "La validation statique "
                "ne remplace pas "
                "un docker build réel."
            ),
        )
    )


    return messages


def validate_static_yaml(
    content: str,
) -> list[dict[str, Any]]:
    try:
        documents = list(
            yaml.safe_load_all(
                content
            )
        )

    except yaml.YAMLError as error:
        return [
            validation_message(
                "error",
                "YAML_INVALID",
                f"YAML invalide : {error}",
            )
        ]


    if (
        not documents

        or all(
            document is None

            for document
            in documents
        )
    ):
        return [
            validation_message(
                "error",
                "YAML_EMPTY",
                (
                    "Le document YAML "
                    "est vide."
                ),
            )
        ]


    return []


def validate_helm_template_content(
    content: str,
) -> list[dict[str, Any]]:
    messages:list[dict[str, Any]] = []



    if (
        content.count("{{")
        != content.count("}}")
    ):
        messages.append(
            validation_message(
                "error",
                (
                    "HELM_DELIMITERS_"
                    "UNBALANCED"
                ),
                (
                    "Les délimiteurs Helm "
                    "{{ et }} ne sont pas "
                    "équilibrés."
                ),
            )
        )


    if (
        "apiVersion:"
        not in content

        and not content
        .lstrip()
        .startswith("{{-")
    ):
        messages.append(
            validation_message(
                "warning",
                (
                    "KUBERNETES_API_VERSION_"
                    "NOT_VISIBLE"
                ),
                (
                    "Aucune apiVersion "
                    "Kubernetes n'est visible "
                    "dans le template."
                ),
            )
        )


    return messages


def apply_helm_cli_validation(
    artifacts: list[
        dict[str, Any]
    ],
) -> None:
    """
    Exécute helm lint et helm template.

    Helm reste facultatif pendant le développement :
    son absence produit un avertissement plutôt
    qu'une erreur bloquante.
    """

    chart_artifacts = [
        item

        for item in artifacts

        if (
            item.get(
                "artifact_type"
            )
            == "helm_chart"

            and str(
                item.get(
                    "relative_path"
                )
                or ""
            ).endswith(
                "Chart.yaml"
            )
        )
    ]


    if not chart_artifacts:
        return


    helm_binary = shutil.which(
        "helm"
    )


    if helm_binary is None:
        for chart in chart_artifacts:
            chart.setdefault(
                "validation_messages",
                [],
            ).append(
                validation_message(
                    "warning",
                    "HELM_NOT_INSTALLED",

                    (
                        "Helm n'est pas installé : "
                        "helm lint et helm template "
                        "n'ont pas été exécutés."
                    ),
                )
            )


        return


    for chart in chart_artifacts:
        chart_path = PurePosixPath(
            str(
                chart[
                    "relative_path"
                ]
            )
        )


        prefix = (
            chart_path.parent
            .as_posix()
        )


        chart_members = [
            item

            for item in artifacts

            if path_is_inside(
                str(
                    item.get(
                        "relative_path"
                    )
                    or ""
                ),

                prefix,
            )
        ]


        with tempfile.TemporaryDirectory(
            prefix="sapixi-helm-"
        ) as temporary:
            temporary_root = Path(
                temporary
            )


            destination_chart = (
                temporary_root
                / "chart"
            )


            destination_chart.mkdir(
                parents=True,
                exist_ok=True,
            )


            for member in chart_members:
                member_path = (
                    PurePosixPath(
                        str(
                            member[
                                "relative_path"
                            ]
                        )
                    )
                )


                relative = (
                    member_path
                    .relative_to(
                        PurePosixPath(
                            prefix
                        )
                    )
                )


                destination = (
                    destination_chart

                    / Path(
                        relative.as_posix()
                    )
                )


                destination.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )


                destination.write_text(
                    str(
                        member.get(
                            "content"
                        )
                        or ""
                    ),

                    encoding="utf-8",
                )


            lint_result = run_command(
                [
                    helm_binary,
                    "lint",
                    str(
                        destination_chart
                    ),
                ],

                timeout_seconds=
                    45,
            )


            template_result = run_command(
                [
                    helm_binary,
                    "template",
                    "sapixi-validation",

                    str(
                        destination_chart
                    ),

                    "--namespace",
                    "sapixi-validation",
                ],

                timeout_seconds=
                    45,
            )


        messages = chart.setdefault(
            "validation_messages",
            [],
        )


        if lint_result[0] == 0:
            messages.append(
                validation_message(
                    "info",
                    "HELM_LINT_PASSED",
                    "helm lint a réussi.",
                )
            )

        else:
            messages.append(
                validation_message(
                    "error",
                    "HELM_LINT_FAILED",

                    trim_command_output(
                        lint_result[1]
                    ),
                )
            )


        if template_result[0] == 0:
            messages.append(
                validation_message(
                    "info",
                    (
                        "HELM_TEMPLATE_"
                        "PASSED"
                    ),
                    (
                        "helm template "
                        "a réussi."
                    ),
                )
            )

        else:
            messages.append(
                validation_message(
                    "error",
                    (
                        "HELM_TEMPLATE_"
                        "FAILED"
                    ),

                    trim_command_output(
                        template_result[1]
                    ),
                )
            )


def run_command(
    command: list[str],
    *,
    timeout_seconds: int,
) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,

            check=False,
            capture_output=True,
            text=True,

            timeout=
                timeout_seconds,

            encoding=
                "utf-8",

            errors=
                "replace",
        )

    except subprocess.TimeoutExpired:
        return (
            124,

            (
                "La commande a dépassé "
                "le délai maximal."
            ),
        )

    except OSError as error:
        return (
            127,
            str(error),
        )


    output = "\n".join(
        part.strip()

        for part in (
            result.stdout,
            result.stderr,
        )

        if (
            part

            and part.strip()
        )
    )


    return (
        result.returncode,
        output,
    )


def artifact(
    component_id: int | None,
    artifact_type: str,
    relative_path: str,
    content: str,
    artifact_status: str,
    metadata: dict[str, Any],
    original_content: str | None = None,
) -> RenderedArtifact:
    if (
        artifact_type
        not in ALLOWED_TYPES
    ):
        raise ArtifactRenderingError(
            (
                "Le type d'artefact "
                f"{artifact_type!r} "
                "n'est pas autorisé."
            )
        )


    if artifact_status not in {
        "generated",
        "existing",
        "proposed_update",
        "needs_review",
    }:
        raise ArtifactRenderingError(
            (
                "Le statut d'artefact "
                f"{artifact_status!r} "
                "n'est pas autorisé."
            )
        )


    return RenderedArtifact(
        component_id=
            component_id,

        artifact_type=
            artifact_type,

        relative_path=
            safe_path(
                relative_path
            ),

        content=(
            content.rstrip()
            + "\n"
        ),

        artifact_status=
            artifact_status,

        metadata=
            metadata,

        original_content=
            original_content,
    )


def require_dict(
    parent: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    value = parent.get(
        key
    )


    if not isinstance(
        value,
        dict,
    ):
        raise ArtifactRenderingError(
            (
                f"La section {key!r} "
                "du contrat est invalide."
            )
        )


    return value


def dns_name(
    value: str,
) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "-",
        value.lower().strip(),
    ).strip("-")


    if not normalized:
        normalized = (
            "application"
        )


    if len(normalized) > 63:
        digest = hashlib.sha256(
            normalized.encode(
                "utf-8"
            )
        ).hexdigest()[:8]


        normalized = (
            f"{normalized[:54].rstrip('-')}"
            f"-{digest}"
        )


    return normalized


def safe_image_tag(
    value: str,
) -> str:
    normalized = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "-",
        value.strip(),
    ).strip(".-")


    return (
        normalized[:128]
        or "latest"
    )


def safe_path(
    value: str,
) -> str:
    normalized = (
        value.replace(
            "\\",
            "/",
        )
        .strip()
    )


    path = PurePosixPath(
        normalized
    )


    if (
        not normalized

        or normalized.startswith(
            "/"
        )

        or path.is_absolute()
    ):
        raise ArtifactRenderingError(
            (
                f"Le chemin {value!r} "
                "doit être relatif."
            )
        )


    if any(
        part in {
            "",
            ".",
            "..",
        }

        for part
        in path.parts
    ):
        raise ArtifactRenderingError(
            (
                f"Le chemin {value!r} "
                "contient une partie interdite."
            )
        )


    if (
        path.parts

        and ":"
        in path.parts[0]
    ):
        raise ArtifactRenderingError(
            (
                f"Le chemin {value!r} "
                "ne doit pas contenir "
                "de lecteur Windows."
            )
        )


    return path.as_posix()


def join_path(
    *values: str,
) -> str:
    parts: list[str] = []

    for value in values:
        normalized = (
            str(value)
            .replace(
                "\\",
                "/",
            )
            .strip("/")
        )


        if (
            normalized

            and normalized
            != "."
        ):
            parts.append(
                normalized
            )


    if not parts:
        raise ArtifactRenderingError(
            "Le chemin construit est vide."
        )


    return safe_path(
        PurePosixPath(
            *parts
        ).as_posix()
    )


def resolve(
    root: Path,
    relative_path: str,
) -> Path:
    root = root.resolve()


    normalized_path = (
        "."
        if relative_path == "."
        else safe_path(
            relative_path
        )
    )


    candidate = (
        root
        / normalized_path
    ).resolve()


    try:
        candidate.relative_to(
            root
        )

    except ValueError as error:
        raise ArtifactRenderingError(
            (
                f"Le chemin {relative_path!r} "
                "sort du repository autorisé."
            )
        ) from error


    return candidate


def read_text(
    path: Path,
) -> str:
    if (
        not path.is_file()

        or path.is_symlink()
    ):
        raise ArtifactRenderingError(
            (
                f"Le fichier {path} "
                "est introuvable "
                "ou symbolique."
            )
        )


    if (
        path.stat().st_size
        > MAX_TEXT_BYTES
    ):
        raise ArtifactRenderingError(
            (
                f"Le fichier {path} "
                "dépasse la taille "
                "maximale autorisée."
            )
        )


    return path.read_text(
        encoding="utf-8",
        errors="replace",
    )


def node_install(
    root: Path,
    package_manager: str,
) -> str:
    if (
        package_manager == "pnpm"

        or (
            root
            / "pnpm-lock.yaml"
        ).is_file()
    ):
        return (
            "corepack enable "
            "&& pnpm install "
            "--frozen-lockfile"
        )


    if (
        package_manager == "yarn"

        or (
            root
            / "yarn.lock"
        ).is_file()
    ):
        return (
            "corepack enable "
            "&& yarn install "
            "--immutable"
        )


    if (
        root
        / "package-lock.json"
    ).is_file():
        return "npm ci"


    return "npm install"


def node_copy(
    root: Path,
) -> str:
    lines = [
        "COPY package.json ./",
    ]


    if (
        root
        / "package-lock.json"
    ).is_file():
        lines = [
            (
                "COPY package.json "
                "package-lock.json ./"
            ),
        ]


    elif (
        root
        / "pnpm-lock.yaml"
    ).is_file():
        lines = [
            (
                "COPY package.json "
                "pnpm-lock.yaml ./"
            ),
        ]


    elif (
        root
        / "yarn.lock"
    ).is_file():
        lines = [
            (
                "COPY package.json "
                "yarn.lock ./"
            ),
        ]


    return "\n".join(
        lines
    )


def python_dependencies(
    root: Path,
) -> tuple[str, str]:
    if (
        root
        / "requirements.txt"
    ).is_file():
        return (
            "COPY requirements.txt ./\n",

            (
                "python -m pip install "
                "--no-cache-dir "
                "-r requirements.txt"
            ),
        )


    if (
        root
        / "pyproject.toml"
    ).is_file():
        return (
            "COPY . .\n",

            (
                "python -m pip install "
                "--no-cache-dir ."
            ),
        )


    if (
        root
        / "Pipfile"
    ).is_file():
        return (
            (
                "COPY Pipfile "
                "Pipfile.lock* ./\n"
            ),

            (
                "python -m pip install "
                "--no-cache-dir pipenv "
                "&& pipenv install "
                "--system --deploy"
            ),
        )


    return (
        "",

        (
            "python -m pip install "
            "--no-cache-dir "
            "--upgrade pip"
        ),
    )


def image_hint(
    value: Any,
    fallback: str,
) -> str:
    normalized = str(
        value or ""
    ).strip()


    if not normalized:
        return fallback


    if not IMAGE_PATTERN.fullmatch(
        normalized
    ):
        return fallback


    return normalized


def image_repository(
    contract: dict[str, Any],
    project_slug: str,
    component_slug: str,
) -> str:
    target = require_dict(
        contract,
        "target",
    )


    registry = require_dict(
        target,
        "registry",
    )


    host = str(
        registry.get(
            "host"
        )
        or ""
    ).strip().rstrip("/")


    prefix = str(
        registry.get(
            "repositoryPrefix"
        )
        or project_slug
    ).strip("/")


    if not host:
        raise ArtifactRenderingError(
            (
                "L'adresse du registre "
                "Docker est obligatoire."
            )
        )


    repository = "/".join(
        part

        for part in (
            host,
            prefix,
            component_slug,
        )

        if part
    )


    if not IMAGE_PATTERN.fullmatch(
        repository
    ):
        raise ArtifactRenderingError(
            (
                "Le repository d'image "
                f"{repository!r} "
                "est invalide."
            )
        )


    return repository


def yaml_text(
    value: Any,
) -> str:
    return yaml.safe_dump(
        value,

        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def validation_message(
    level: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "level":
            level,

        "code":
            code,

        "message":
            message[:4000],
    }


def validation_status_from_messages(
    messages: list[
        dict[str, Any]
    ],
) -> str:
    levels = {
        str(
            message.get(
                "level"
            )
            or ""
        )

        for message
        in messages
    }


    if "error" in levels:
        return "failed"


    if "warning" in levels:
        return "warning"


    return "passed"


def deduplicate_messages(
    messages: list[
        dict[str, Any]
    ],
) -> list[dict[str, Any]]:
    result:list[dict[str, Any]] = []


    seen: set[
            tuple[
                str,
                str,
                str,
            ]
        ] = set()


    for raw in messages:
        if not isinstance(
            raw,
            dict,
        ):
            continue


        item = validation_message(
            str(
                raw.get(
                    "level"
                )
                or "info"
            ),

            str(
                raw.get(
                    "code"
                )
                or (
                    "VALIDATION_"
                    "MESSAGE"
                )
            ),

            str(
                raw.get(
                    "message"
                )
                or ""
            ),
        )


        key = (
            item["level"],
            item["code"],
            item["message"],
        )


        if key not in seen:
            seen.add(
                key
            )


            result.append(
                item
            )


    return result


def path_is_inside(
    relative_path: str,
    prefix: str,
) -> bool:
    path = PurePosixPath(
        safe_path(
            relative_path
        )
    )


    parent = PurePosixPath(
        safe_path(
            prefix
        )
    )


    try:
        path.relative_to(
            parent
        )


        return True

    except ValueError:
        return False


def trim_command_output(
    value: str,
) -> str:
    normalized = value.strip()


    if not normalized:
        return (
            "La commande a échoué "
            "sans produire de sortie."
        )


    return normalized[-4000:]