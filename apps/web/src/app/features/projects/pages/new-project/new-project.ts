import {
  Component,
  OnDestroy,
  inject,
} from '@angular/core';

import {
  FormBuilder,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';

import { RouterLink } from '@angular/router';

type WizardStep = 1 | 2 | 3 | 4 | 5;

type FactKind =
  | 'detected'
  | 'ai';

type PipelineStatus =
  | 'waiting'
  | 'running'
  | 'success'
  | 'error';

interface ProjectAnalysis {
  framework: string;
  frameworkVersion: string;
  runtime: string;
  packageManager: string;
  buildCommand: string;
  startCommand: string;
  port: number;
  architecture: string;
  dockerfileDetected: boolean;
  helmDetected: boolean;
  warnings: string[];
}

interface AnalysisFact {
  label: string;
  value: string;
  source: string;
  confidence: number;
  kind: FactKind;
}

interface GeneratedFile {
  name: string;
  category: 'docker' | 'helm';
  status: 'generated' | 'existing';
  content: string;
}

interface AiMessage {
  author: 'user' | 'assistant';
  content: string;
}

interface PipelineStage {
  id: number;
  name: string;
  description: string;
  status: PipelineStatus;
}

@Component({
  selector: 'app-new-project',
  imports: [
    ReactiveFormsModule,
    RouterLink,
  ],
  templateUrl: './new-project.html',
  styleUrl: './new-project.scss',
})
export class NewProject implements OnDestroy {
  private readonly formBuilder = inject(FormBuilder);

  private deploymentTimerId: number | null = null;

  currentStep: WizardStep = 1;

  isAnalyzing = false;
  isGenerating = false;
  isAssistantThinking = false;
  isDeploying = false;
  deploymentFinished = false;

  analysis: ProjectAnalysis | null = null;

  analysisFacts: AnalysisFact[] = [];

  generatedFiles: GeneratedFile[] = [];

  selectedFileName = '';

  aiMessages: AiMessage[] = [
    {
      author: 'assistant',
      content:
        'Je suis l’assistant du projet. Après l’analyse, ' +
        'vous pourrez me demander d’expliquer ou de modifier ' +
        'la configuration Docker et Helm.',
    },
  ];

  deploymentLogs: string[] = [];

  pipelineStages: PipelineStage[] = [
    {
      id: 1,
      name: 'Repository',
      description: 'Récupération du code GitLab',
      status: 'waiting',
    },
    {
      id: 2,
      name: 'Build',
      description: 'Construction de l’image',
      status: 'waiting',
    },
    {
      id: 3,
      name: 'Nexus',
      description: 'Publication de l’image',
      status: 'waiting',
    },
    {
      id: 4,
      name: 'Helm',
      description: 'Validation du chart',
      status: 'waiting',
    },
    {
      id: 5,
      name: 'GitOps',
      description: 'Mise à jour du repository',
      status: 'waiting',
    },
    {
      id: 6,
      name: 'Argo CD',
      description: 'Synchronisation',
      status: 'waiting',
    },
    {
      id: 7,
      name: 'Kubernetes',
      description: 'Vérification des Pods',
      status: 'waiting',
    },
  ];

  readonly sourceForm =
    this.formBuilder.nonNullable.group({
      repositoryUrl: [
        '',
        [
          Validators.required,
          Validators.pattern(/^https?:\/\/.+/i),
        ],
      ],

      branch: [
        'main',
        [
          Validators.required,
          Validators.maxLength(100),
        ],
      ],

      subdirectory: [
        '',
        [
          Validators.maxLength(200),
        ],
      ],

      accessMode: [
        'private',
        [
          Validators.required,
        ],
      ],
    });

  readonly configurationForm =
    this.formBuilder.nonNullable.group({
      deploymentName: [
        '',
        [
          Validators.required,
          Validators.maxLength(63),
          Validators.pattern(
            /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/,
          ),
        ],
      ],

      environment: [
        'lab',
        [
          Validators.required,
        ],
      ],

      namespace: [
        'sapixi-lab',
        [
          Validators.required,
          Validators.maxLength(63),
          Validators.pattern(
            /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/,
          ),
        ],
      ],

      replicas: [
        1,
        [
          Validators.required,
          Validators.min(1),
          Validators.max(10),
        ],
      ],

      containerPort: [
        8080,
        [
          Validators.required,
          Validators.min(1),
          Validators.max(65535),
        ],
      ],

      ingressEnabled: [
        true,
      ],

      ingressHost: [
        '',
        [
          Validators.maxLength(253),
        ],
      ],

      cpuRequest: [
        '100m',
        [
          Validators.required,
        ],
      ],

      memoryRequest: [
        '256Mi',
        [
          Validators.required,
        ],
      ],

      requireApproval: [
        true,
      ],
    });

  readonly assistantForm =
    this.formBuilder.nonNullable.group({
      prompt: [
        '',
        [
          Validators.required,
          Validators.minLength(3),
          Validators.maxLength(500),
        ],
      ],
    });

  readonly confirmationForm =
    this.formBuilder.nonNullable.group({
      approved: [
        false,
        [
          Validators.requiredTrue,
        ],
      ],
    });

  get selectedFile(): GeneratedFile | null {
    return (
      this.generatedFiles.find(
        (file) => file.name === this.selectedFileName,
      ) ?? null
    );
  }

  get deploymentProgress(): number {
    const completedStages =
      this.pipelineStages.filter(
        (stage) => stage.status === 'success',
      ).length;

    return Math.round(
      (completedStages / this.pipelineStages.length) * 100,
    );
  }

  analyzeRepository(): void {
    this.sourceForm.markAllAsTouched();

    if (this.sourceForm.invalid) {
      return;
    }

    this.isAnalyzing = true;

    const repositoryUrl =
      this.sourceForm.controls.repositoryUrl.value;

    window.setTimeout(() => {
      this.analysis =
        this.simulateProjectAnalysis(repositoryUrl);

      this.analysisFacts =
        this.createAnalysisFacts(this.analysis);

      const deploymentName =
        this.extractRepositoryName(repositoryUrl);

      this.configurationForm.patchValue({
        deploymentName,
        containerPort: this.analysis.port,
        ingressHost:
          `${deploymentName}.sapixi.home.local`,
      });

      this.currentStep = 2;
      this.isAnalyzing = false;
    }, 1200);
  }

  continueToGeneration(): void {
    if (!this.analysis) {
      return;
    }

    this.isGenerating = true;

    window.setTimeout(() => {
      this.generateProjectFiles();

      this.currentStep = 3;
      this.isGenerating = false;
    }, 900);
  }

  regenerateFiles(): void {
    this.generateProjectFiles();

    this.aiMessages = [
      ...this.aiMessages,
      {
        author: 'assistant',
        content:
          'Les fichiers Docker et Helm ont été régénérés ' +
          'avec la configuration actuelle.',
      },
    ];
  }

  selectFile(fileName: string): void {
    this.selectedFileName = fileName;
  }

  sendAssistantMessage(): void {
    this.assistantForm.markAllAsTouched();

    if (this.assistantForm.invalid) {
      return;
    }

    const prompt =
      this.assistantForm.controls.prompt.value.trim();

    this.aiMessages = [
      ...this.aiMessages,
      {
        author: 'user',
        content: prompt,
      },
    ];

    this.assistantForm.reset({
      prompt: '',
    });

    this.isAssistantThinking = true;

    window.setTimeout(() => {
      const answer =
        this.applySimulatedAiInstruction(prompt);

      this.aiMessages = [
        ...this.aiMessages,
        {
          author: 'assistant',
          content: answer,
        },
      ];

      this.isAssistantThinking = false;
    }, 700);
  }

  prepareReview(): void {
    this.configurationForm.markAllAsTouched();

    if (this.configurationForm.invalid) {
      return;
    }

    this.generateProjectFiles();

    this.currentStep = 4;
  }

  launchDeployment(): void {
    this.confirmationForm.markAllAsTouched();

    if (this.confirmationForm.invalid) {
      return;
    }

    this.currentStep = 5;

    this.startDeploymentSimulation();
  }

  goToStep(step: WizardStep): void {
    if (this.isDeploying) {
      return;
    }

    this.currentStep = step;
  }

  resetWizard(): void {
    this.stopDeploymentSimulation();

    this.sourceForm.reset({
      repositoryUrl: '',
      branch: 'main',
      subdirectory: '',
      accessMode: 'private',
    });

    this.configurationForm.reset({
      deploymentName: '',
      environment: 'lab',
      namespace: 'sapixi-lab',
      replicas: 1,
      containerPort: 8080,
      ingressEnabled: true,
      ingressHost: '',
      cpuRequest: '100m',
      memoryRequest: '256Mi',
      requireApproval: true,
    });

    this.assistantForm.reset({
      prompt: '',
    });

    this.confirmationForm.reset({
      approved: false,
    });

    this.currentStep = 1;

    this.isAnalyzing = false;
    this.isGenerating = false;
    this.isAssistantThinking = false;
    this.isDeploying = false;
    this.deploymentFinished = false;

    this.analysis = null;
    this.analysisFacts = [];
    this.generatedFiles = [];
    this.selectedFileName = '';
    this.deploymentLogs = [];

    this.pipelineStages =
      this.pipelineStages.map((stage) => ({
        ...stage,
        status: 'waiting',
      }));

    this.aiMessages = [
      {
        author: 'assistant',
        content:
          'Je suis l’assistant du projet. Après l’analyse, ' +
          'vous pourrez me demander d’expliquer ou de modifier ' +
          'la configuration Docker et Helm.',
      },
    ];
  }

  ngOnDestroy(): void {
    this.stopDeploymentSimulation();
  }

  private simulateProjectAnalysis(
    repositoryUrl: string,
  ): ProjectAnalysis {
    const normalizedUrl =
      repositoryUrl.toLowerCase();

    if (
      normalizedUrl.includes('flask') ||
      normalizedUrl.includes('python') ||
      normalizedUrl.includes('api')
    ) {
      return {
        framework: 'Flask',
        frameworkVersion: '3.x',
        runtime: 'Python 3.11',
        packageManager: 'pip',
        buildCommand:
          'pip install -r requirements.txt',
        startCommand:
          'gunicorn --bind 0.0.0.0:5000 app:app',
        port: 5000,
        architecture:
          'API web Python avec serveur WSGI',
        dockerfileDetected: false,
        helmDetected: false,
        warnings: [
          'Aucun endpoint de santé confirmé.',
          'Aucun chart Helm détecté.',
        ],
      };
    }

    if (
      normalizedUrl.includes('spring') ||
      normalizedUrl.includes('java')
    ) {
      return {
        framework: 'Spring Boot',
        frameworkVersion: '3.x',
        runtime: 'Java 21',
        packageManager: 'Maven',
        buildCommand:
          'mvn clean package -DskipTests',
        startCommand:
          'java -jar application.jar',
        port: 8080,
        architecture:
          'API Java Spring Boot',
        dockerfileDetected: false,
        helmDetected: false,
        warnings: [
          'Les limites mémoire JVM doivent être vérifiées.',
          'Aucun chart Helm détecté.',
        ],
      };
    }

    return {
      framework: 'Angular',
      frameworkVersion: '20',
      runtime: 'Node.js 22',
      packageManager: 'npm',
      buildCommand:
        'npm ci && npm run build',
      startCommand:
        'nginx -g "daemon off;"',
      port: 80,
      architecture:
        'Application web frontend statique',
      dockerfileDetected: false,
      helmDetected: false,
      warnings: [
        'Le serveur Nginx doit être configuré.',
        'Aucun chart Helm détecté.',
      ],
    };
  }

  private createAnalysisFacts(
    analysis: ProjectAnalysis,
  ): AnalysisFact[] {
    return [
      {
        label: 'Framework',
        value:
          `${analysis.framework} ${analysis.frameworkVersion}`,
        source: 'Fichiers de dépendances',
        confidence: 98,
        kind: 'detected',
      },
      {
        label: 'Runtime',
        value: analysis.runtime,
        source: 'Configuration du projet',
        confidence: 95,
        kind: 'detected',
      },
      {
        label: 'Gestionnaire',
        value: analysis.packageManager,
        source: 'Fichiers de dépendances',
        confidence: 99,
        kind: 'detected',
      },
      {
        label: 'Port',
        value: analysis.port.toString(),
        source: 'Code et configuration',
        confidence: 90,
        kind: 'detected',
      },
      {
        label: 'Commande de démarrage',
        value: analysis.startCommand,
        source: 'Proposition de l’assistant IA',
        confidence: 84,
        kind: 'ai',
      },
      {
        label: 'Architecture',
        value: analysis.architecture,
        source: 'Interprétation de l’assistant IA',
        confidence: 82,
        kind: 'ai',
      },
    ];
  }

  private generateProjectFiles(): void {
    if (!this.analysis) {
      return;
    }

    const config =
      this.configurationForm.getRawValue();

    const imageRepository =
      `nexus.piximind.local/sapixi-docker/` +
      `${config.deploymentName}`;

    const dockerfile = this.createDockerfile(
      this.analysis,
    );

    const files: GeneratedFile[] = [
      {
        name: 'Dockerfile',
        category: 'docker',
        status: this.analysis.dockerfileDetected
          ? 'existing'
          : 'generated',
        content: dockerfile,
      },
      {
        name: 'Chart.yaml',
        category: 'helm',
        status: this.analysis.helmDetected
          ? 'existing'
          : 'generated',
        content:
`apiVersion: v2
name: ${config.deploymentName}
description: Chart Helm généré par Piximind
type: application
version: 0.1.0
appVersion: "1.0.0"
`,
      },
      {
        name: 'values.yaml',
        category: 'helm',
        status: this.analysis.helmDetected
          ? 'existing'
          : 'generated',
        content:
`replicaCount: ${config.replicas}

image:
  repository: ${imageRepository}
  tag: latest
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: ${config.containerPort}

ingress:
  enabled: ${config.ingressEnabled}
  host: ${config.ingressHost}

resources:
  requests:
    cpu: ${config.cpuRequest}
    memory: ${config.memoryRequest}

probes:
  readiness:
    path: /health
  liveness:
    path: /health
`,
      },
      {
        name: 'templates/deployment.yaml',
        category: 'helm',
        status: 'generated',
        content:
`apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      app: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app: {{ .Release.Name }}
    spec:
      containers:
        - name: ${config.deploymentName}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          ports:
            - containerPort: ${config.containerPort}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
`,
      },
      {
        name: 'templates/service.yaml',
        category: 'helm',
        status: 'generated',
        content:
`apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}
spec:
  type: {{ .Values.service.type }}
  selector:
    app: {{ .Release.Name }}
  ports:
    - port: {{ .Values.service.port }}
      targetPort: ${config.containerPort}
`,
      },
    ];

    if (config.ingressEnabled) {
      files.push({
        name: 'templates/ingress.yaml',
        category: 'helm',
        status: 'generated',
        content:
`apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ .Release.Name }}
spec:
  ingressClassName: nginx
  rules:
    - host: {{ .Values.ingress.host }}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {{ .Release.Name }}
                port:
                  number: {{ .Values.service.port }}
`,
      });
    }

    this.generatedFiles = files;

    if (
      !this.selectedFileName ||
      !files.some(
        (file) => file.name === this.selectedFileName,
      )
    ) {
      this.selectedFileName = files[0].name;
    }
  }

  private createDockerfile(
    analysis: ProjectAnalysis,
  ): string {
    if (analysis.framework === 'Flask') {
      return `FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
`;
    }

    if (analysis.framework === 'Spring Boot') {
      return `FROM maven:3.9-eclipse-temurin-21 AS build

WORKDIR /app

COPY . .

RUN mvn clean package -DskipTests

FROM eclipse-temurin:21-jre

WORKDIR /app

COPY --from=build /app/target/*.jar application.jar

EXPOSE 8080

ENTRYPOINT ["java", "-jar", "application.jar"]
`;
    }

    return `FROM node:22-alpine AS build

WORKDIR /app

COPY package*.json ./

RUN npm ci

COPY . .

RUN npm run build

FROM nginx:alpine

COPY --from=build /app/dist/ /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
`;
  }

  private applySimulatedAiInstruction(
    prompt: string,
  ): string {
    const normalizedPrompt =
      prompt.toLowerCase();

    const replicaMatch =
      normalizedPrompt.match(/(\d+)\s*replica/);

    if (replicaMatch) {
      const replicas = Number(replicaMatch[1]);

      if (
        Number.isInteger(replicas) &&
        replicas >= 1 &&
        replicas <= 10
      ) {
        this.configurationForm.patchValue({
          replicas,
        });

        this.generateProjectFiles();

        return (
          `La configuration a été mise à jour avec ` +
          `${replicas} replica(s). Les fichiers Helm ` +
          `ont été régénérés.`
        );
      }
    }

    if (
      normalizedPrompt.includes('sans ingress') ||
      normalizedPrompt.includes('désactiver ingress') ||
      normalizedPrompt.includes('desactiver ingress')
    ) {
      this.configurationForm.patchValue({
        ingressEnabled: false,
      });

      this.generateProjectFiles();

      return (
        'Ingress a été désactivé et le fichier ' +
        'templates/ingress.yaml a été retiré.'
      );
    }

    if (
      normalizedPrompt.includes('256mi') ||
      normalizedPrompt.includes('256 mi')
    ) {
      this.configurationForm.patchValue({
        memoryRequest: '256Mi',
      });

      this.generateProjectFiles();

      return (
        'La mémoire demandée a été définie à 256Mi. ' +
        'Le fichier values.yaml a été mis à jour.'
      );
    }

    if (
      normalizedPrompt.includes('pourquoi') &&
      normalizedPrompt.includes('port')
    ) {
      return (
        `Le port ${this.analysis?.port ?? 0} a été proposé ` +
        'à partir de la configuration détectée dans le projet. ' +
        'Vous pouvez le modifier manuellement dans le formulaire.'
      );
    }

    return (
      'J’ai analysé votre demande. Dans cette version, ' +
      'l’assistant est encore simulé. Le backend Flask ' +
      'et Ollama permettront ensuite une analyse réelle ' +
      'et des modifications plus précises.'
    );
  }

  private startDeploymentSimulation(): void {
    this.stopDeploymentSimulation();

    this.isDeploying = true;
    this.deploymentFinished = false;

    this.deploymentLogs = [
      '[DÉMARRAGE] Création du déploiement...',
    ];

    this.pipelineStages =
      this.pipelineStages.map(
        (stage, index) => ({
          ...stage,
          status:
            index === 0
              ? 'running'
              : 'waiting',
        }),
      );

    let currentStageIndex = 0;

    this.deploymentTimerId =
      window.setInterval(() => {
        const currentStage =
          this.pipelineStages[currentStageIndex];

        this.deploymentLogs = [
          ...this.deploymentLogs,
          `[OK] ${currentStage.name} : ` +
            `${currentStage.description}`,
        ];

        this.pipelineStages =
          this.pipelineStages.map(
            (stage, index) => {
              if (index === currentStageIndex) {
                return {
                  ...stage,
                  status: 'success',
                };
              }

              if (index === currentStageIndex + 1) {
                return {
                  ...stage,
                  status: 'running',
                };
              }

              return stage;
            },
          );

        currentStageIndex += 1;

        if (
          currentStageIndex >=
          this.pipelineStages.length
        ) {
          this.stopDeploymentSimulation();

          this.isDeploying = false;
          this.deploymentFinished = true;

          this.deploymentLogs = [
            ...this.deploymentLogs,
            '[TERMINÉ] L’application est disponible.',
          ];
        }
      }, 1100);
  }

  private stopDeploymentSimulation(): void {
    if (this.deploymentTimerId !== null) {
      window.clearInterval(
        this.deploymentTimerId,
      );

      this.deploymentTimerId = null;
    }
  }

  private extractRepositoryName(
    repositoryUrl: string,
  ): string {
    const finalSegment =
      repositoryUrl
        .split('/')
        .filter(Boolean)
        .at(-1) ?? 'application';

    const withoutGitExtension =
      finalSegment.replace(/\.git$/i, '');

    const normalized =
      withoutGitExtension
        .toLowerCase()
        .replace(/[^a-z0-9-]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 63);

    return normalized || 'application';
  }
}