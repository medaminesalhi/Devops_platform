from __future__ import annotations

import json
import re

from dataclasses import (
    asdict,
    dataclass,
)

from pathlib import Path

from typing import Any


IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".next",
    ".nuxt",
    "target",
    "vendor",
    "bin",
    "obj",
}


COMPONENT_MARKERS = {
    "package.json",
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
}


TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".php",
    ".rb",
    ".cs",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".env",
    ".txt",
}


SENSITIVE_KEYWORDS = {
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "PRIVATE_KEY",
    "API_KEY",
    "DATABASE_URL",
    "CREDENTIAL",
    "AUTH",
}


@dataclass
class DetectedComponent:
    name: str
    component_type: str
    root_path: str

    runtime: str | None
    framework: str | None
    package_manager: str | None

    build_command: str | None
    start_command: str | None

    detected_port: int | None

    deployable: bool

    dockerfile_path: str | None
    helm_chart_path: str | None

    kubernetes_paths: list[str]
    environment_variables: list[dict[str, Any]]

    confidence: int

    configuration: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisReport:
    components: list[DetectedComponent]
    summary: dict[str, Any]


def analyze_repository(
    *,
    source_root: Path,
    selected_subdirectory: str | None,
    max_files: int = 20000,
    max_file_size_bytes: int = 2_000_000,
) -> AnalysisReport:
    analysis_root = resolve_analysis_root(
        source_root=source_root,
        selected_subdirectory=
            selected_subdirectory,
    )

    inventory = collect_inventory(
        analysis_root=analysis_root,
        max_files=max_files,
        max_file_size_bytes=
            max_file_size_bytes,
    )

    candidate_roots = find_candidate_roots(
        analysis_root=analysis_root,
        files=inventory["files"],
    )

    components: list[DetectedComponent] = []

    for candidate_root in candidate_roots:
        components.extend(
            detect_components_in_directory(
                analysis_root=analysis_root,
                component_root=candidate_root,
                inventory_files=
                    inventory["files"],
                max_file_size_bytes=
                    max_file_size_bytes,
            )
        )

    if not components:
        components.append(
            detect_unknown_component(
                analysis_root=analysis_root,
                inventory_files=
                    inventory["files"],
                max_file_size_bytes=
                    max_file_size_bytes,
            )
        )

    dockerfiles = find_named_files(
        inventory["files"],
        {
            "Dockerfile",
        },
        analysis_root,
    )

    helm_charts = find_named_files(
        inventory["files"],
        {
            "Chart.yaml",
        },
        analysis_root,
    )

    kubernetes_manifests = (
        detect_kubernetes_manifests(
            files=inventory["files"],
            analysis_root=analysis_root,
            max_file_size_bytes=
                max_file_size_bytes,
        )
    )

    argocd_applications = (
        detect_argocd_applications(
            files=inventory["files"],
            analysis_root=analysis_root,
            max_file_size_bytes=
                max_file_size_bytes,
        )
    )

    gitlab_ci_files = find_named_files(
        inventory["files"],
        {
            ".gitlab-ci.yml",
            ".gitlab-ci.yaml",
        },
        analysis_root,
    )

    compose_files = find_named_files(
        inventory["files"],
        {
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        },
        analysis_root,
    )

    deployable_components = [
        component
        for component in components
        if component.deployable
    ]

    warnings: list[str] = []

    if not deployable_components:
        warnings.append(
            (
                "Aucun composant déployable "
                "n'a été identifié automatiquement."
            )
        )

    if not dockerfiles:
        warnings.append(
            (
                "Aucun Dockerfile n'a été détecté. "
                "La phase IA devra en proposer."
            )
        )

    if not helm_charts:
        warnings.append(
            (
                "Aucun chart Helm n'a été détecté. "
                "La phase IA devra en proposer."
            )
        )

    summary = {
        "analysisRoot":
            relative_path(
                analysis_root,
                source_root,
            ),

        "inventory": {
            "fileCount":
                inventory["file_count"],

            "totalSizeBytes":
                inventory[
                    "total_size_bytes"
                ],

            "ignoredFileCount":
                inventory[
                    "ignored_file_count"
                ],

            "limitReached":
                inventory[
                    "limit_reached"
                ],
        },

        "componentCount":
            len(components),

        "deployableComponentCount":
            len(deployable_components),

        "dockerfiles":
            dockerfiles,

        "helmCharts":
            helm_charts,

        "kubernetesManifests":
            kubernetes_manifests,

        "gitlabCiFiles":
            gitlab_ci_files,

        "composeFiles":
            compose_files,

        "argoCd": {
            "existingApplications":
                argocd_applications,

            "existingApplicationCount":
                len(argocd_applications),

            "appProjectManagedByEnvironment":
                True,

            "applicationCreationPhase":
                5,

            "confirmationRequired":
                True,
        },

        "warnings":
            warnings,

        "phase3Ready":
            bool(deployable_components),
    }

    return AnalysisReport(
        components=components,
        summary=summary,
    )


def resolve_analysis_root(
    *,
    source_root: Path,
    selected_subdirectory: str | None,
) -> Path:
    source_root = source_root.resolve()

    if not selected_subdirectory:
        return source_root

    candidate = (
        source_root
        / selected_subdirectory
    ).resolve()

    try:
        candidate.relative_to(
            source_root
        )

    except ValueError as error:
        raise ValueError(
            (
                "Le sous-dossier d'analyse "
                "sort du repository."
            )
        ) from error

    if not candidate.exists():
        raise ValueError(
            (
                "Le sous-dossier configuré "
                "n'existe pas dans le repository."
            )
        )

    if not candidate.is_dir():
        raise ValueError(
            (
                "Le sous-dossier configuré "
                "n'est pas un dossier."
            )
        )

    return candidate


def collect_inventory(
    *,
    analysis_root: Path,
    max_files: int,
    max_file_size_bytes: int,
) -> dict[str, Any]:
    files: list[Path] = []

    total_size = 0
    ignored_count = 0
    limit_reached = False

    for path in analysis_root.rglob("*"):
        if any(
            part in IGNORED_DIRECTORIES
            for part in path.parts
        ):
            continue

        if path.is_symlink():
            ignored_count += 1
            continue

        if not path.is_file():
            continue

        if len(files) >= max_files:
            limit_reached = True
            break

        try:
            file_size = path.stat().st_size

        except OSError:
            ignored_count += 1
            continue

        if file_size > max_file_size_bytes:
            ignored_count += 1
            continue

        files.append(path)
        total_size += file_size

    return {
        "files":
            files,

        "file_count":
            len(files),

        "total_size_bytes":
            total_size,

        "ignored_file_count":
            ignored_count,

        "limit_reached":
            limit_reached,
    }


def find_candidate_roots(
    *,
    analysis_root: Path,
    files: list[Path],
) -> list[Path]:
    candidates: set[Path] = set()

    for file_path in files:
        if (
            file_path.name
            in COMPONENT_MARKERS
            or file_path.suffix == ".csproj"
        ):
            candidates.add(
                file_path.parent
            )

    if not candidates:
        return [
            analysis_root
        ]

    return sorted(
        candidates,
        key=lambda path: (
            len(path.parts),
            str(path),
        ),
    )


def detect_components_in_directory(
    *,
    analysis_root: Path,
    component_root: Path,
    inventory_files: list[Path],
    max_file_size_bytes: int,
) -> list[DetectedComponent]:
    components: list[DetectedComponent] = []

    package_json_path = (
        component_root
        / "package.json"
    )

    if package_json_path.exists():
        components.append(
            detect_node_component(
                analysis_root=analysis_root,
                component_root=component_root,
                package_json_path=
                    package_json_path,
                inventory_files=
                    inventory_files,
                max_file_size_bytes=
                    max_file_size_bytes,
            )
        )

    python_markers = [
        component_root
        / "requirements.txt",

        component_root
        / "pyproject.toml",

        component_root
        / "Pipfile",
    ]

    if any(
        marker.exists()
        for marker in python_markers
    ):
        components.append(
            detect_python_component(
                analysis_root=analysis_root,
                component_root=component_root,
                inventory_files=
                    inventory_files,
                max_file_size_bytes=
                    max_file_size_bytes,
            )
        )

    if (
        component_root.joinpath(
            "pom.xml"
        ).exists()

        or component_root.joinpath(
            "build.gradle"
        ).exists()

        or component_root.joinpath(
            "build.gradle.kts"
        ).exists()
    ):
        components.append(
            detect_java_component(
                analysis_root=analysis_root,
                component_root=component_root,
                inventory_files=
                    inventory_files,
                max_file_size_bytes=
                    max_file_size_bytes,
            )
        )

    if component_root.joinpath(
        "go.mod"
    ).exists():
        components.append(
            detect_go_component(
                analysis_root=analysis_root,
                component_root=component_root,
                inventory_files=
                    inventory_files,
                max_file_size_bytes=
                    max_file_size_bytes,
            )
        )

    csproj_files = list(
        component_root.glob(
            "*.csproj"
        )
    )

    if csproj_files:
        components.append(
            detect_dotnet_component(
                analysis_root=analysis_root,
                component_root=component_root,
                inventory_files=
                    inventory_files,
                max_file_size_bytes=
                    max_file_size_bytes,
            )
        )

    if component_root.joinpath(
        "composer.json"
    ).exists():
        components.append(
            detect_php_component(
                analysis_root=analysis_root,
                component_root=component_root,
                inventory_files=
                    inventory_files,
                max_file_size_bytes=
                    max_file_size_bytes,
            )
        )

    if (
        component_root.joinpath(
            "Gemfile"
        ).exists()
    ):
        components.append(
            detect_ruby_component(
                analysis_root=analysis_root,
                component_root=component_root,
                inventory_files=
                    inventory_files,
                max_file_size_bytes=
                    max_file_size_bytes,
            )
        )

    if (
        not components

        and component_root.joinpath(
            "Dockerfile"
        ).exists()
    ):
        components.append(
            create_component(
                analysis_root=analysis_root,
                component_root=component_root,
                inventory_files=
                    inventory_files,
                max_file_size_bytes=
                    max_file_size_bytes,

                name=component_root.name
                    or "application",

                component_type=
                    "container",

                runtime=None,
                framework=None,
                package_manager=None,

                build_command=
                    "docker build .",

                start_command=None,

                detected_port=None,

                confidence=65,

                configuration={
                    "detectionReason":
                        "Dockerfile",
                },
            )
        )

    return components


def detect_node_component(
    *,
    analysis_root: Path,
    component_root: Path,
    package_json_path: Path,
    inventory_files: list[Path],
    max_file_size_bytes: int,
) -> DetectedComponent:
    package_data = read_json(
        package_json_path
    )

    dependencies = {
        **(
            package_data.get(
                "dependencies"
            )
            or {}
        ),

        **(
            package_data.get(
                "devDependencies"
            )
            or {}
        ),
    }

    scripts = (
        package_data.get("scripts")
        or {}
    )

    framework = "Node.js"
    component_type = "backend"
    detected_port: int | None = None
    confidence = 65

    if "@angular/core" in dependencies:
        framework = "Angular"
        component_type = "frontend"
        detected_port = 80
        confidence = 98

    elif "next" in dependencies:
        framework = "Next.js"
        component_type = "fullstack"
        detected_port = 3000
        confidence = 98

    elif "@nestjs/core" in dependencies:
        framework = "NestJS"
        component_type = "backend"
        detected_port = 3000
        confidence = 98

    elif "express" in dependencies:
        framework = "Express"
        component_type = "backend"
        detected_port = 3000
        confidence = 90

    elif "vue" in dependencies:
        framework = "Vue"
        component_type = "frontend"
        detected_port = 80
        confidence = 92

    elif "nuxt" in dependencies:
        framework = "Nuxt"
        component_type = "fullstack"
        detected_port = 3000
        confidence = 96

    elif "react" in dependencies:
        framework = "React"
        component_type = "frontend"
        detected_port = 80
        confidence = 90

    elif "svelte" in dependencies:
        framework = "Svelte"
        component_type = "frontend"
        detected_port = 80
        confidence = 90

    package_manager = detect_node_package_manager(
        component_root
    )

    build_script = scripts.get(
        "build"
    )

    start_script = (
        scripts.get("start")
        or scripts.get("serve")
    )

    return create_component(
        analysis_root=analysis_root,
        component_root=component_root,
        inventory_files=inventory_files,
        max_file_size_bytes=
            max_file_size_bytes,

        name=str(
            package_data.get("name")
            or component_root.name
            or "node-app"
        ),

        component_type=
            component_type,

        runtime="Node.js",

        framework=
            framework,

        package_manager=
            package_manager,

        build_command=(
            f"{package_manager} run build"
            if build_script
            else None
        ),

        start_command=(
            f"{package_manager} run start"
            if start_script
            else None
        ),

        detected_port=
            detected_port,

        confidence=
            confidence,

        configuration={
            "scripts":
                scripts,

            "engines":
                package_data.get(
                    "engines"
                )
                or {},

            "dependencyCount":
                len(dependencies),

            "workspace":
                bool(
                    package_data.get(
                        "workspaces"
                    )
                ),
        },
    )


def detect_python_component(
    *,
    analysis_root: Path,
    component_root: Path,
    inventory_files: list[Path],
    max_file_size_bytes: int,
) -> DetectedComponent:
    dependency_text = "\n".join(
        filter(
            None,
            [
                safe_read_text(
                    component_root
                    / "requirements.txt"
                ),

                safe_read_text(
                    component_root
                    / "pyproject.toml"
                ),

                safe_read_text(
                    component_root
                    / "Pipfile"
                ),
            ],
        )
    ).lower()

    framework = "Python"
    component_type = "backend"
    detected_port: int | None = None
    start_command: str | None = None
    confidence = 65

    if "fastapi" in dependency_text:
        framework = "FastAPI"
        detected_port = 8000
        start_command = (
            "uvicorn main:app "
            "--host 0.0.0.0 --port 8000"
        )
        confidence = 95

    elif "flask" in dependency_text:
        framework = "Flask"
        detected_port = 5000
        start_command = (
            "flask --app wsgi run "
            "--host 0.0.0.0 --port 5000"
        )
        confidence = 95

    elif "django" in dependency_text:
        framework = "Django"
        detected_port = 8000
        start_command = (
            "python manage.py runserver "
            "0.0.0.0:8000"
        )
        confidence = 95

    package_manager = (
        "poetry"
        if "tool.poetry" in dependency_text
        else "pip"
    )

    return create_component(
        analysis_root=analysis_root,
        component_root=component_root,
        inventory_files=inventory_files,
        max_file_size_bytes=
            max_file_size_bytes,

        name=component_root.name
            or "python-app",

        component_type=
            component_type,

        runtime="Python",

        framework=
            framework,

        package_manager=
            package_manager,

        build_command=None,

        start_command=
            start_command,

        detected_port=
            detected_port,

        confidence=
            confidence,

        configuration={
            "requirementsFile":
                find_first_existing_relative(
                    component_root,
                    analysis_root,
                    [
                        "requirements.txt",
                        "pyproject.toml",
                        "Pipfile",
                    ],
                ),
        },
    )


def detect_java_component(
    *,
    analysis_root: Path,
    component_root: Path,
    inventory_files: list[Path],
    max_file_size_bytes: int,
) -> DetectedComponent:
    pom_text = safe_read_text(
        component_root / "pom.xml"
    ).lower()

    gradle_text = (
        safe_read_text(
            component_root
            / "build.gradle"
        )

        + safe_read_text(
            component_root
            / "build.gradle.kts"
        )
    ).lower()

    combined = (
        pom_text
        + gradle_text
    )

    framework = (
        "Spring Boot"
        if "spring-boot" in combined
        else "Java"
    )

    package_manager = (
        "maven"
        if component_root
        .joinpath("pom.xml")
        .exists()
        else "gradle"
    )

    build_command = (
        "mvn clean package"
        if package_manager == "maven"
        else "./gradlew build"
    )

    return create_component(
        analysis_root=analysis_root,
        component_root=component_root,
        inventory_files=inventory_files,
        max_file_size_bytes=
            max_file_size_bytes,

        name=component_root.name
            or "java-app",

        component_type="backend",

        runtime="Java",

        framework=framework,

        package_manager=
            package_manager,

        build_command=
            build_command,

        start_command=(
            "java -jar application.jar"
        ),

        detected_port=(
            8080
            if framework == "Spring Boot"
            else None
        ),

        confidence=(
            95
            if framework == "Spring Boot"
            else 75
        ),

        configuration={},
    )


def detect_go_component(
    *,
    analysis_root: Path,
    component_root: Path,
    inventory_files: list[Path],
    max_file_size_bytes: int,
) -> DetectedComponent:
    return create_component(
        analysis_root=analysis_root,
        component_root=component_root,
        inventory_files=inventory_files,
        max_file_size_bytes=
            max_file_size_bytes,

        name=component_root.name
            or "go-app",

        component_type="backend",

        runtime="Go",

        framework="Go",

        package_manager="go modules",

        build_command=(
            "go build -o application ."
        ),

        start_command="./application",

        detected_port=None,

        confidence=85,

        configuration={},
    )


def detect_dotnet_component(
    *,
    analysis_root: Path,
    component_root: Path,
    inventory_files: list[Path],
    max_file_size_bytes: int,
) -> DetectedComponent:
    return create_component(
        analysis_root=analysis_root,
        component_root=component_root,
        inventory_files=inventory_files,
        max_file_size_bytes=
            max_file_size_bytes,

        name=component_root.name
            or "dotnet-app",

        component_type="backend",

        runtime=".NET",

        framework="ASP.NET Core",

        package_manager="NuGet",

        build_command="dotnet publish",

        start_command=(
            "dotnet application.dll"
        ),

        detected_port=8080,

        confidence=85,

        configuration={},
    )


def detect_php_component(
    *,
    analysis_root: Path,
    component_root: Path,
    inventory_files: list[Path],
    max_file_size_bytes: int,
) -> DetectedComponent:
    composer = read_json(
        component_root
        / "composer.json"
    )

    dependencies = {
        **(
            composer.get("require")
            or {}
        ),

        **(
            composer.get("require-dev")
            or {}
        ),
    }

    framework = (
        "Laravel"
        if "laravel/framework"
        in dependencies
        else "PHP"
    )

    return create_component(
        analysis_root=analysis_root,
        component_root=component_root,
        inventory_files=inventory_files,
        max_file_size_bytes=
            max_file_size_bytes,

        name=component_root.name
            or "php-app",

        component_type="backend",

        runtime="PHP",

        framework=framework,

        package_manager="Composer",

        build_command=(
            "composer install "
            "--no-dev --optimize-autoloader"
        ),

        start_command=None,

        detected_port=80,

        confidence=(
            95
            if framework == "Laravel"
            else 75
        ),

        configuration={},
    )


def detect_ruby_component(
    *,
    analysis_root: Path,
    component_root: Path,
    inventory_files: list[Path],
    max_file_size_bytes: int,
) -> DetectedComponent:
    gemfile = safe_read_text(
        component_root / "Gemfile"
    ).lower()

    framework = (
        "Ruby on Rails"
        if "rails" in gemfile
        else "Ruby"
    )

    return create_component(
        analysis_root=analysis_root,
        component_root=component_root,
        inventory_files=inventory_files,
        max_file_size_bytes=
            max_file_size_bytes,

        name=component_root.name
            or "ruby-app",

        component_type="backend",

        runtime="Ruby",

        framework=framework,

        package_manager="Bundler",

        build_command=(
            "bundle install"
        ),

        start_command=(
            "bundle exec rails server"
            if framework == "Ruby on Rails"
            else None
        ),

        detected_port=(
            3000
            if framework == "Ruby on Rails"
            else None
        ),

        confidence=(
            92
            if framework == "Ruby on Rails"
            else 70
        ),

        configuration={},
    )


def detect_unknown_component(
    *,
    analysis_root: Path,
    inventory_files: list[Path],
    max_file_size_bytes: int,
) -> DetectedComponent:
    return create_component(
        analysis_root=analysis_root,
        component_root=analysis_root,
        inventory_files=inventory_files,
        max_file_size_bytes=
            max_file_size_bytes,

        name=analysis_root.name
            or "application",

        component_type="unknown",

        runtime=None,
        framework=None,
        package_manager=None,

        build_command=None,
        start_command=None,

        detected_port=None,

        confidence=20,

        configuration={
            "warning":
                (
                    "La technologie n'a pas "
                    "été reconnue automatiquement."
                ),
        },
    )


def create_component(
    *,
    analysis_root: Path,
    component_root: Path,
    inventory_files: list[Path],
    max_file_size_bytes: int,

    name: str,
    component_type: str,

    runtime: str | None,
    framework: str | None,
    package_manager: str | None,

    build_command: str | None,
    start_command: str | None,

    detected_port: int | None,

    confidence: int,

    configuration: dict[str, Any],
) -> DetectedComponent:
    component_files = [
        file_path
        for file_path in inventory_files
        if is_relative_to(
            file_path,
            component_root,
        )
    ]

    dockerfile_path = find_nearest_named_file(
        files=component_files,
        names={"Dockerfile"},
        base_root=analysis_root,
    )

    helm_chart_path = find_nearest_named_file(
        files=component_files,
        names={"Chart.yaml"},
        base_root=analysis_root,
    )

    kubernetes_paths = (
        detect_kubernetes_manifests(
            files=component_files,
            analysis_root=analysis_root,
            max_file_size_bytes=
                max_file_size_bytes,
        )
    )

    environment_variables = (
        detect_environment_variables(
            files=component_files,
            analysis_root=analysis_root,
            max_file_size_bytes=
                max_file_size_bytes,
        )
    )

    return DetectedComponent(
        name=sanitize_component_name(
            name
        ),

        component_type=
            component_type,

        root_path=
            relative_path(
                component_root,
                analysis_root,
            ),

        runtime=
            runtime,

        framework=
            framework,

        package_manager=
            package_manager,

        build_command=
            build_command,

        start_command=
            start_command,

        detected_port=
            detected_port,

        deployable=(
            component_type
            != "workspace"
        ),

        dockerfile_path=
            dockerfile_path,

        helm_chart_path=
            helm_chart_path,

        kubernetes_paths=
            kubernetes_paths,

        environment_variables=
            environment_variables,

        confidence=
            confidence,

        configuration=
            configuration,
    )


def detect_environment_variables(
    *,
    files: list[Path],
    analysis_root: Path,
    max_file_size_bytes: int,
) -> list[dict[str, Any]]:
    variable_names: set[str] = set()

    env_file_names = {
        ".env.example",
        ".env.sample",
        ".env.template",
        "env.example",
    }

    patterns = [
        re.compile(
            r"os\.getenv\(\s*['\"]([A-Z0-9_]+)"
        ),

        re.compile(
            r"os\.environ\[\s*['\"]([A-Z0-9_]+)"
        ),

        re.compile(
            r"process\.env\.([A-Z0-9_]+)"
        ),

        re.compile(
            r"import\.meta\.env\.([A-Z0-9_]+)"
        ),

        re.compile(
            r"env\(\s*['\"]([A-Z0-9_]+)"
        ),
    ]

    for file_path in files[:2000]:
        if not is_text_candidate(
            file_path
        ):
            continue

        text = safe_read_text(
            file_path,
            max_file_size_bytes,
        )

        if not text:
            continue

        if file_path.name in env_file_names:
            for line in text.splitlines():
                line = line.strip()

                if (
                    not line
                    or line.startswith("#")
                    or "=" not in line
                ):
                    continue

                variable_name = (
                    line
                    .split("=", maxsplit=1)[0]
                    .strip()
                )

                if re.fullmatch(
                    r"[A-Z][A-Z0-9_]*",
                    variable_name,
                ):
                    variable_names.add(
                        variable_name
                    )

        for pattern in patterns:
            variable_names.update(
                pattern.findall(text)
            )

    return [
        {
            "name":
                variable_name,

            "sensitive":
                any(
                    keyword
                    in variable_name

                    for keyword
                    in SENSITIVE_KEYWORDS
                ),

            "valueCaptured":
                False,
        }

        for variable_name
        in sorted(variable_names)
    ]


def detect_kubernetes_manifests(
    *,
    files: list[Path],
    analysis_root: Path,
    max_file_size_bytes: int,
) -> list[str]:
    results: list[str] = []

    kubernetes_kinds = {
        "Deployment",
        "StatefulSet",
        "DaemonSet",
        "Service",
        "Ingress",
        "ConfigMap",
        "Secret",
        "Job",
        "CronJob",
    }

    for file_path in files:
        if file_path.suffix not in {
            ".yaml",
            ".yml",
        }:
            continue

        text = safe_read_text(
            file_path,
            max_file_size_bytes,
        )

        if not text:
            continue

        if any(
            f"kind: {kind}" in text
            for kind in kubernetes_kinds
        ):
            results.append(
                relative_path(
                    file_path,
                    analysis_root,
                )
            )

    return sorted(
        set(results)
    )


def detect_argocd_applications(
    *,
    files: list[Path],
    analysis_root: Path,
    max_file_size_bytes: int,
) -> list[str]:
    results: list[str] = []

    for file_path in files:
        if file_path.suffix not in {
            ".yaml",
            ".yml",
        }:
            continue

        text = safe_read_text(
            file_path,
            max_file_size_bytes,
        )

        if (
            "argoproj.io" in text
            and "kind: Application" in text
        ):
            results.append(
                relative_path(
                    file_path,
                    analysis_root,
                )
            )

    return sorted(
        set(results)
    )


def detect_node_package_manager(
    component_root: Path,
) -> str:
    if component_root.joinpath(
        "pnpm-lock.yaml"
    ).exists():
        return "pnpm"

    if component_root.joinpath(
        "yarn.lock"
    ).exists():
        return "yarn"

    return "npm"


def find_named_files(
    files: list[Path],
    names: set[str],
    base_root: Path,
) -> list[str]:
    return sorted(
        {
            relative_path(
                file_path,
                base_root,
            )

            for file_path in files

            if file_path.name in names
        }
    )


def find_nearest_named_file(
    *,
    files: list[Path],
    names: set[str],
    base_root: Path,
) -> str | None:
    matching = [
        file_path
        for file_path in files
        if file_path.name in names
    ]

    if not matching:
        return None

    matching.sort(
        key=lambda path: (
            len(path.parts),
            str(path),
        )
    )

    return relative_path(
        matching[0],
        base_root,
    )


def find_first_existing_relative(
    component_root: Path,
    analysis_root: Path,
    names: list[str],
) -> str | None:
    for name in names:
        candidate = (
            component_root / name
        )

        if candidate.exists():
            return relative_path(
                candidate,
                analysis_root,
            )

    return None


def read_json(
    file_path: Path,
) -> dict[str, Any]:
    try:
        return json.loads(
            file_path.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return {}


def safe_read_text(
    file_path: Path,
    max_size: int = 2_000_000,
) -> str:
    try:
        if not file_path.exists():
            return ""

        if file_path.stat().st_size > max_size:
            return ""

        return file_path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

    except OSError:
        return ""


def is_text_candidate(
    file_path: Path,
) -> bool:
    return (
        file_path.name.startswith(".env")
        or file_path.suffix
        in TEXT_EXTENSIONS
    )


def relative_path(
    path: Path,
    base_root: Path,
) -> str:
    try:
        relative = path.resolve().relative_to(
            base_root.resolve()
        )

    except ValueError:
        return str(path)

    value = relative.as_posix()

    return value or "."


def is_relative_to(
    path: Path,
    parent: Path,
) -> bool:
    try:
        path.resolve().relative_to(
            parent.resolve()
        )

        return True

    except ValueError:
        return False


def sanitize_component_name(
    value: str,
) -> str:
    normalized = re.sub(
        r"[^a-zA-Z0-9._-]+",
        "-",
        value.strip(),
    )

    return normalized.strip("-") or "application"