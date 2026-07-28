from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.analysis.detectors import (
    AnalysisReport,
    DetectedComponent,
    resolve_analysis_root,
)


TECHNOLOGY_FILES = {
    "package.json",
    "angular.json",
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "go.mod",
    "composer.json",
    "Gemfile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "Chart.yaml",
    "values.yaml",
    "values.yml",
    "README.md",
}


SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "id_rsa",
    "id_ed25519",
}


DEPENDENCY_EVIDENCE = {
    "@angular/core": ("Angular", "frontend"),
    "react": ("React", "frontend"),
    "next": ("Next.js", "fullstack"),
    "vue": ("Vue", "frontend"),
    "nuxt": ("Nuxt", "fullstack"),
    "@nestjs/core": ("NestJS", "backend"),
    "express": ("Express", "backend"),
    "fastify": ("Fastify", "backend"),
    "svelte": ("Svelte", "frontend"),
}


PYTHON_EVIDENCE = {
    "flask": "Flask",
    "fastapi": "FastAPI",
    "django": "Django",
    "celery": "Celery",
    "gunicorn": "Gunicorn",
    "uvicorn": "Uvicorn",
}


def enrich_analysis_report(
    *,
    source_root: Path,
    selected_subdirectory: str | None,
    report: AnalysisReport,
) -> AnalysisReport:
    analysis_root = resolve_analysis_root(
        source_root=source_root,
        selected_subdirectory=selected_subdirectory,
    )

    for component in report.components:
        component_root = resolve_component_root(
            analysis_root,
            component.root_path,
        )

        enrich_component(
            analysis_root=analysis_root,
            component_root=component_root,
            component=component,
        )

    deployable_components = [
        component
        for component in report.components
        if component.deployable
    ]

    confidence_values = [
        component.confidence
        for component in deployable_components
    ]

    global_confidence = round(
        sum(confidence_values) / len(confidence_values)
    ) if confidence_values else 0

    missing_dockerfiles = [
        component.name
        for component in deployable_components
        if not component.dockerfile_path
    ]

    missing_helm_charts = [
        component.name
        for component in deployable_components
        if not component.helm_chart_path
    ]

    evidence_count = sum(
        len(
            component.configuration.get("evidence", [])
        )
        for component in report.components
    )

    ai_context_files = collect_ai_context_files(
        analysis_root
    )

    report.summary["globalConfidence"] = global_confidence
    report.summary["evidenceCount"] = evidence_count
    report.summary["technologyCount"] = len(
        {
            component.framework
            for component in deployable_components
            if component.framework
        }
    )

    report.summary["deploymentReadiness"] = {
        "ready": (
            bool(deployable_components)
            and not missing_dockerfiles
            and not missing_helm_charts
        ),
        "missingDockerfiles": missing_dockerfiles,
        "missingHelmCharts": missing_helm_charts,
        "argoCdApplicationRequired": True,
        "message": build_readiness_message(
            deployable_count=len(deployable_components),
            missing_dockerfiles=len(missing_dockerfiles),
            missing_helm_charts=len(missing_helm_charts),
        ),
    }

    report.summary["aiContext"] = {
        "status": "prepared",
        "ready": bool(ai_context_files),
        "selectedFileCount": len(ai_context_files),
        "selectedFiles": ai_context_files,
        "secretsIncluded": False,
        "message": (
            "Un contexte technique filtré est prêt pour une "
            "analyse IA complémentaire. Aucun secret n'est inclus."
        ),
    }

    return report


def enrich_component(
    *,
    analysis_root: Path,
    component_root: Path,
    component: DetectedComponent,
) -> None:
    evidence: list[dict[str, Any]] = []

    package_json = component_root / "package.json"

    if package_json.exists():
        package_data = read_json(package_json)
        dependencies = {
            **(package_data.get("dependencies") or {}),
            **(package_data.get("devDependencies") or {}),
        }

        for dependency, (framework, component_type) in DEPENDENCY_EVIDENCE.items():
            if dependency in dependencies:
                evidence.append(
                    evidence_item(
                        file_path=package_json,
                        analysis_root=analysis_root,
                        category="dependency",
                        message=(
                            f"La dépendance {dependency} confirme "
                            f"l'utilisation de {framework}."
                        ),
                        strength="strong",
                    )
                )

                if component.framework in {None, "Node.js"}:
                    component.framework = framework

                if component.component_type in {"unknown", "container"}:
                    component.component_type = component_type

                component.confidence = max(component.confidence, 92)

        scripts = package_data.get("scripts") or {}

        if "build" in scripts:
            evidence.append(
                evidence_item(
                    file_path=package_json,
                    analysis_root=analysis_root,
                    category="build",
                    message="Un script de build est défini dans package.json.",
                    strength="medium",
                )
            )

        if "start" in scripts or "serve" in scripts:
            evidence.append(
                evidence_item(
                    file_path=package_json,
                    analysis_root=analysis_root,
                    category="runtime",
                    message="Un script de démarrage est défini dans package.json.",
                    strength="medium",
                )
            )

    angular_json = component_root / "angular.json"
    if angular_json.exists():
        evidence.append(
            evidence_item(
                file_path=angular_json,
                analysis_root=analysis_root,
                category="framework",
                message="Le fichier angular.json confirme un workspace Angular.",
                strength="strong",
            )
        )
        component.framework = "Angular"
        component.runtime = component.runtime or "Node.js"
        component.component_type = "frontend"
        component.detected_port = component.detected_port or 80
        component.confidence = max(component.confidence, 98)

    python_files = [
        component_root / "requirements.txt",
        component_root / "pyproject.toml",
        component_root / "Pipfile",
    ]

    python_text = "\n".join(
        safe_read_text(path)
        for path in python_files
        if path.exists()
    ).lower()

    if python_text:
        for dependency, framework in PYTHON_EVIDENCE.items():
            if re.search(rf"(^|[^a-z0-9_-]){re.escape(dependency)}([^a-z0-9_-]|$)", python_text):
                source_file = next(
                    path
                    for path in python_files
                    if path.exists()
                )
                evidence.append(
                    evidence_item(
                        file_path=source_file,
                        analysis_root=analysis_root,
                        category="dependency",
                        message=(
                            f"La dépendance {dependency} a été trouvée "
                            "dans les dépendances Python."
                        ),
                        strength=(
                            "strong"
                            if framework in {"Flask", "FastAPI", "Django"}
                            else "medium"
                        ),
                    )
                )

                if framework in {"Flask", "FastAPI", "Django"}:
                    component.framework = framework
                    component.runtime = "Python"
                    component.component_type = "backend"
                    component.confidence = max(component.confidence, 95)

    for marker_name, framework in (
        ("wsgi.py", "Flask / WSGI"),
        ("manage.py", "Django"),
        ("main.py", "Python application"),
    ):
        marker = component_root / marker_name
        if marker.exists():
            evidence.append(
                evidence_item(
                    file_path=marker,
                    analysis_root=analysis_root,
                    category="entrypoint",
                    message=f"Le point d'entrée {marker_name} a été détecté.",
                    strength="medium",
                )
            )

            if marker_name == "manage.py":
                component.framework = "Django"
                component.runtime = "Python"
                component.component_type = "backend"
                component.confidence = max(component.confidence, 96)

    dockerfile = find_dockerfile(component_root)
    docker_details: dict[str, Any] = {}

    if dockerfile is not None:
        docker_details = parse_dockerfile(dockerfile)

        evidence.append(
            evidence_item(
                file_path=dockerfile,
                analysis_root=analysis_root,
                category="container",
                message=(
                    "Dockerfile détecté"
                    + (
                        f" avec l'image finale {docker_details['finalImage']}."
                        if docker_details.get("finalImage")
                        else "."
                    )
                ),
                strength="strong",
            )
        )

        if component.dockerfile_path is None:
            component.dockerfile_path = relative_path(
                dockerfile,
                analysis_root,
            )

        ports = docker_details.get("exposedPorts") or []
        if ports and component.detected_port is None:
            component.detected_port = ports[0]

        final_image = str(
            docker_details.get("finalImage") or ""
        ).lower()

        if "nginx" in final_image:
            evidence.append(
                evidence_item(
                    file_path=dockerfile,
                    analysis_root=analysis_root,
                    category="runtime",
                    message="L'image finale Nginx indique un service HTTP sur le port 80.",
                    strength="strong",
                )
            )

            component.runtime = "Nginx"
            component.detected_port = component.detected_port or 80

            if component.component_type in {"container", "unknown"}:
                component.component_type = "frontend"

            if component.framework in {None, "Node.js"}:
                if has_static_web_files(component_root):
                    component.framework = "HTML/CSS statique"

            component.confidence = max(component.confidence, 90)

        elif any(token in final_image for token in ("python", "gunicorn", "uvicorn")):
            component.runtime = component.runtime or "Python"
            component.component_type = "backend"
            component.confidence = max(component.confidence, 85)

        elif "node" in final_image:
            component.runtime = component.runtime or "Node.js"
            component.confidence = max(component.confidence, 82)

        elif any(token in final_image for token in ("openjdk", "temurin", "java")):
            component.runtime = component.runtime or "Java"
            component.component_type = "backend"
            component.confidence = max(component.confidence, 82)

    if component.helm_chart_path:
        evidence.append(
            {
                "file": component.helm_chart_path,
                "category": "deployment",
                "message": "Un chart Helm existe déjà pour ce composant.",
                "strength": "strong",
            }
        )

    if component.kubernetes_paths:
        evidence.append(
            {
                "file": component.kubernetes_paths[0],
                "category": "deployment",
                "message": (
                    f"{len(component.kubernetes_paths)} manifest(s) "
                    "Kubernetes ont été détectés."
                ),
                "strength": "medium",
            }
        )

    component.configuration = {
        **(component.configuration or {}),
        "evidence": evidence,
        "docker": docker_details,
        "deployment": {
            "dockerfile": bool(component.dockerfile_path),
            "helmChart": bool(component.helm_chart_path),
            "kubernetesManifestCount": len(component.kubernetes_paths),
            "needsDockerfile": (
                component.deployable
                and not component.dockerfile_path
            ),
            "needsHelmChart": (
                component.deployable
                and not component.helm_chart_path
            ),
        },
    }


def parse_dockerfile(dockerfile: Path) -> dict[str, Any]:
    text = safe_read_text(dockerfile)

    images: list[str] = []
    exposed_ports: list[int] = []
    command: str | None = None
    entrypoint: str | None = None
    workdir: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        instruction, _, value = line.partition(" ")
        instruction = instruction.upper()
        value = value.strip()

        if instruction == "FROM":
            image = value.split(" AS ", 1)[0].split(" as ", 1)[0].strip()
            if image:
                images.append(image)

        elif instruction == "EXPOSE":
            for token in value.split():
                port_text = token.split("/", 1)[0]
                if port_text.isdigit():
                    port = int(port_text)
                    if 1 <= port <= 65535:
                        exposed_ports.append(port)

        elif instruction == "CMD":
            command = value

        elif instruction == "ENTRYPOINT":
            entrypoint = value

        elif instruction == "WORKDIR":
            workdir = value

    return {
        "stages": images,
        "buildImage": images[0] if images else None,
        "finalImage": images[-1] if images else None,
        "exposedPorts": sorted(set(exposed_ports)),
        "command": command,
        "entrypoint": entrypoint,
        "workdir": workdir,
        "multiStage": len(images) > 1,
    }


def collect_ai_context_files(analysis_root: Path) -> list[str]:
    selected: list[str] = []

    for path in sorted(analysis_root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue

        if any(
            part in {
                ".git",
                "node_modules",
                ".venv",
                "venv",
                "dist",
                "build",
                "coverage",
                "__pycache__",
            }
            for part in path.parts
        ):
            continue

        if path.name in SENSITIVE_FILE_NAMES or path.name.startswith(".env"):
            continue

        if path.name in TECHNOLOGY_FILES or path.suffix in {".csproj"}:
            selected.append(relative_path(path, analysis_root))

        if len(selected) >= 40:
            break

    return selected


def build_readiness_message(
    *,
    deployable_count: int,
    missing_dockerfiles: int,
    missing_helm_charts: int,
) -> str:
    if deployable_count == 0:
        return "Aucun composant déployable n'a été confirmé."

    if missing_dockerfiles == 0 and missing_helm_charts == 0:
        return "Les artefacts Docker et Helm nécessaires sont déjà présents."

    return (
        f"{missing_dockerfiles} Dockerfile(s) et "
        f"{missing_helm_charts} chart(s) Helm devront être préparés."
    )


def resolve_component_root(
    analysis_root: Path,
    root_path: str,
) -> Path:
    if not root_path or root_path == ".":
        return analysis_root

    candidate = (analysis_root / root_path).resolve()

    try:
        candidate.relative_to(analysis_root.resolve())
    except ValueError:
        return analysis_root

    return candidate if candidate.exists() else analysis_root


def find_dockerfile(component_root: Path) -> Path | None:
    direct = component_root / "Dockerfile"
    if direct.exists():
        return direct

    candidates = sorted(component_root.glob("Dockerfile*"))
    return candidates[0] if candidates else None


def has_static_web_files(component_root: Path) -> bool:
    return any(
        path.exists()
        for path in (
            component_root / "index.html",
            component_root / "src" / "index.html",
            component_root / "public" / "index.html",
        )
    )


def evidence_item(
    *,
    file_path: Path,
    analysis_root: Path,
    category: str,
    message: str,
    strength: str,
) -> dict[str, Any]:
    return {
        "file": relative_path(file_path, analysis_root),
        "category": category,
        "message": message,
        "strength": strength,
    }


def read_json(file_path: Path) -> dict[str, Any]:
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def safe_read_text(file_path: Path) -> str:
    try:
        if not file_path.exists() or file_path.stat().st_size > 2_000_000:
            return ""
        return file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def relative_path(path: Path, base_root: Path) -> str:
    try:
        value = path.resolve().relative_to(base_root.resolve()).as_posix()
        return value or "."
    except ValueError:
        return str(path)
