import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, delay, map, of } from 'rxjs';

import { Auth } from '../auth/auth';

/**
 * Active le jeu de données de démonstration tant que le backend Déploiements
 * n'est pas encore branché. Passez cette constante à false après l'ajout des
 * routes Flask décrites par le service.
 */
export const DEPLOYMENTS_DEMO_MODE = false;

export type DeploymentStatus =
  | 'draft'
  | 'ready'
  | 'queued'
  | 'running'
  | 'waiting_confirmation'
  | 'succeeded'
  | 'failed'
  | 'cancelled';

export type DeploymentStepStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'cancelled';

export type DeploymentStageKey =
  | 'prepare'
  | 'source'
  | 'build'
  | 'registry'
  | 'gitops'
  | 'argocd'
  | 'kubernetes'
  | 'health';

export type DeploymentLogScope =
  | 'system'
  | 'docker'
  | 'nexus'
  | 'gitops'
  | 'argocd'
  | 'kubernetes'
  | 'application';

export type DeploymentLogLevel =
  | 'info'
  | 'warning'
  | 'error'
  | 'success';

export type DeploymentSyncMode =
  | 'prepare_only'
  | 'confirm_before_sync'
  | 'automatic';

export type DeploymentResourceKind =
  | 'argocd_application'
  | 'deployment'
  | 'pod'
  | 'service'
  | 'ingress'
  | 'job'
  | 'pvc';

export interface ApiResponse<T> {
  success: boolean;
  data: T;
}

export interface DeploymentSummary {
  id: number;
  projectId: number;
  projectName: string;
  projectSlug: string;
  generationId: number;
  environmentId: number;
  environmentName: string;
  namespace: string;
  version: string;
  sourceCommit: string;
  gitopsCommit: string | null;
  status: DeploymentStatus;
  currentStage: DeploymentStageKey | null;
  currentStageLabel: string;
  progress: number;
  createdByName: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  note: string | null;
}

export interface DeploymentPreflightItem {
  key: string;
  label: string;
  description: string;
  status: 'ready' | 'warning' | 'blocked';
  integrationName: string | null;
  actionLabel: string | null;
  actionPath: string | null;
}

export interface DeploymentComponent {
  id: string;
  name: string;
  type: string;
  imageRepository: string;
  imageTag: string;
  imageDigest: string | null;
  port: number | null;
  replicas: number;
  buildStatus: DeploymentStepStatus;
  registryStatus: DeploymentStepStatus;
}

export interface DeploymentStep {
  id: string;
  stage: DeploymentStageKey;
  order: number;
  label: string;
  description: string;
  status: DeploymentStepStatus;
  startedAt: string | null;
  finishedAt: string | null;
  durationSeconds: number | null;
  details: Record<string, string | number | boolean | null>;
  errorCode: string | null;
  errorMessage: string | null;
}

export interface DeploymentLogEntry {
  id: number;
  createdAt: string;
  scope: DeploymentLogScope;
  level: DeploymentLogLevel;
  stepId: string | null;
  componentName: string | null;
  message: string;
}

export interface DeploymentResource {
  id: string;
  kind: DeploymentResourceKind;
  name: string;
  namespace: string;
  status: string;
  health: 'healthy' | 'progressing' | 'degraded' | 'unknown';
  ready: string | null;
  image: string | null;
  restarts: number | null;
  age: string;
  message: string | null;
  url: string | null;
}

export interface DeploymentIncident {
  code: string;
  title: string;
  message: string;
  stage: DeploymentStageKey;
  stepId: string;
  componentName: string | null;
  integrationName: string | null;
  occurredAt: string;
  retryable: boolean;
  requiresNewGeneration: boolean;
}

export interface DeploymentCorrection {
  id: number;
  title: string;
  summary: string;
  targetPhase: 'integration' | 'analysis' | 'proposal' | 'generation' | 'deployment';
  targetFile: string | null;
  diff: string | null;
  risk: 'low' | 'medium' | 'high';
  status: 'proposed' | 'approved' | 'rejected' | 'applied';
}

export interface DeploymentDiagnostic {
  status: 'idle' | 'running' | 'completed' | 'failed';
  cause: string | null;
  explanation: string | null;
  confidence: 'low' | 'medium' | 'high' | null;
  targetPhase: 'integration' | 'analysis' | 'proposal' | 'generation' | 'deployment' | null;
  evidence: string[];
  corrections: DeploymentCorrection[];
  providerConnectionId: number | null;
  model: string | null;
  fallback: boolean;
  providerError: string | null;
  createdAt: string | null;
}

export interface DeploymentChatMessage {
  id: number;
  role: 'assistant' | 'user' | 'system';
  content: string;
  createdAt: string;
}

export interface DeploymentHealth {
  argocdSync: 'Synced' | 'OutOfSync' | 'Unknown';
  argocdHealth: 'Healthy' | 'Progressing' | 'Degraded' | 'Unknown';
  readyPods: number;
  totalPods: number;
  ingressReady: boolean;
  migrationStatus: 'succeeded' | 'failed' | 'not_required' | 'pending';
  applicationUrl: string | null;
}

export interface DeploymentDetails extends DeploymentSummary {
  syncMode: DeploymentSyncMode;
  preflight: DeploymentPreflightItem[];
  components: DeploymentComponent[];
  steps: DeploymentStep[];
  logs: DeploymentLogEntry[];
  resources: DeploymentResource[];
  incident: DeploymentIncident | null;
  diagnostic: DeploymentDiagnostic;
  chat: DeploymentChatMessage[];
  health: DeploymentHealth;
}

export interface DeploymentGenerationOption {
  id: number;
  label: string;
  sourceCommit: string;
  createdAt: string;
  componentCount: number;
  approvedArtifactCount: number;
}

export interface DeploymentProjectOption {
  id: number;
  name: string;
  slug: string;
  environmentId: number;
  environmentName: string;
  namespace: string;
  generations: DeploymentGenerationOption[];
}

export interface DeploymentCreateRequest {
  projectId: number;
  generationId: number;
  version: string;
  note: string | null;
  syncMode: DeploymentSyncMode;
}

export interface DeploymentListFilters {
  search?: string | null;
  projectId?: number | null;
  environmentId?: number | null;
  status?: DeploymentStatus | null;
  dateFrom?: string | null;
  dateTo?: string | null;
}

export interface DeploymentListResult {
  deployments: DeploymentSummary[];
  total: number;
}

export interface ProjectDeploymentReadiness {
  projectId: number;
  projectName: string;
  generationId: number | null;
  generationLabel: string | null;
  sourceCommit: string | null;
  sourceCurrentCommit: string | null;
  sourceOutdated: boolean;
  sourceFreshnessStatus: 'current' | 'outdated' | 'unavailable' | null;
  environmentId: number | null;
  environmentName: string | null;
  namespace: string | null;
  componentCount: number;
  ready: boolean;
  checks: DeploymentPreflightItem[];
}

@Injectable({ providedIn: 'root' })
export class DeploymentsService {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);

  private readonly mockDetails = new Map<number, DeploymentDetails>(
    this.createMockDeployments().map(item => [item.id, item]),
  );

  private nextDeploymentId = 60;
  private nextLogId = 500;
  private nextChatId = 100;

  listDeployments(filters: DeploymentListFilters = {}): Observable<DeploymentListResult> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      let params = new HttpParams();
      const search = filters.search?.trim();
      if (search) params = params.set('search', search);
      if (filters.projectId) params = params.set('projectId', filters.projectId);
      if (filters.environmentId) params = params.set('environmentId', filters.environmentId);
      if (filters.status) params = params.set('status', filters.status);
      if (filters.dateFrom) params = params.set('dateFrom', filters.dateFrom);
      if (filters.dateTo) params = params.set('dateTo', filters.dateTo);

      return this.http
        .get<ApiResponse<DeploymentListResult>>('/api/deployments', {
          headers: this.headers(),
          params,
        })
        .pipe(map(response => response.data));
    }

    let items = [...this.mockDetails.values()];
    const search = filters.search?.trim().toLowerCase();
    if (search) {
      items = items.filter(item =>
        item.projectName.toLowerCase().includes(search)
        || item.version.toLowerCase().includes(search)
        || item.sourceCommit.toLowerCase().includes(search),
      );
    }
    if (filters.projectId) items = items.filter(item => item.projectId === filters.projectId);
    if (filters.environmentId) items = items.filter(item => item.environmentId === filters.environmentId);
    if (filters.status) items = items.filter(item => item.status === filters.status);

    items.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    return this.respond({ deployments: items.map(item => this.toSummary(item)), total: items.length });
  }

  getOptions(): Observable<DeploymentProjectOption[]> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      return this.http
        .get<ApiResponse<{ projects: DeploymentProjectOption[] }>>('/api/deployments/options', {
          headers: this.headers(),
        })
        .pipe(map(response => response.data.projects));
    }

    return this.respond(this.mockProjectOptions());
  }

  getProjectReadiness(
    projectId: number,
    generationId: number | null = null,
  ): Observable<ProjectDeploymentReadiness> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      let params = new HttpParams();
      if (generationId) params = params.set('generationId', generationId);

      return this.http
        .get<ApiResponse<{ readiness: ProjectDeploymentReadiness }>>(
          `/api/deployments/projects/${projectId}/readiness`,
          { headers: this.headers(), params },
        )
        .pipe(map(response => response.data.readiness));
    }

    const option = this.mockProjectOptions().find(item => item.id === projectId)
      ?? this.mockProjectOptions()[0];
    const generation = option.generations.find(item => item.id === generationId)
      ?? option.generations[0]
      ?? null;
    const checks = this.defaultPreflight();

    return this.respond({
      projectId,
      projectName: option.name,
      generationId: generation?.id ?? null,
      generationLabel: generation?.label ?? null,
      sourceCommit: generation?.sourceCommit ?? null,
      sourceCurrentCommit: generation?.sourceCommit ?? null,
      sourceOutdated: false,
      sourceFreshnessStatus: 'current',
      environmentId: option.environmentId,
      environmentName: option.environmentName,
      namespace: option.namespace,
      componentCount: generation?.componentCount ?? 0,
      ready: checks.every(item => item.status !== 'blocked'),
      checks,
    });
  }

  createDeployment(request: DeploymentCreateRequest): Observable<DeploymentDetails> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      return this.http
        .post<ApiResponse<{ deployment: DeploymentDetails }>>('/api/deployments', request, {
          headers: this.headers(),
        })
        .pipe(map(response => response.data.deployment));
    }

    const option = this.mockProjectOptions().find(item => item.id === request.projectId)
      ?? this.mockProjectOptions()[0];
    const generation = option.generations.find(item => item.id === request.generationId)
      ?? option.generations[0];
    const id = this.nextDeploymentId++;
    const now = new Date().toISOString();
    const deployment: DeploymentDetails = {
      id,
      projectId: option.id,
      projectName: option.name,
      projectSlug: option.slug,
      generationId: generation.id,
      environmentId: option.environmentId,
      environmentName: option.environmentName,
      namespace: option.namespace,
      version: request.version,
      sourceCommit: generation.sourceCommit,
      gitopsCommit: null,
      status: 'ready',
      currentStage: 'prepare',
      currentStageLabel: 'Prêt à démarrer',
      progress: 0,
      createdByName: 'Amine Salhi',
      createdAt: now,
      startedAt: null,
      finishedAt: null,
      note: request.note,
      syncMode: request.syncMode,
      preflight: this.defaultPreflight(),
      components: this.defaultComponents(request.version),
      steps: this.defaultSteps(),
      logs: [{
        id: this.nextLogId++,
        createdAt: now,
        scope: 'system',
        level: 'info',
        stepId: null,
        componentName: null,
        message: 'Déploiement créé. Les prérequis sont prêts à être vérifiés.',
      }],
      resources: [],
      incident: null,
      diagnostic: this.emptyDiagnostic(),
      chat: [{
        id: this.nextChatId++,
        role: 'assistant',
        content: 'Je suivrai le pipeline et je pourrai analyser toute erreur détectée.',
        createdAt: now,
      }],
      health: this.emptyHealth(),
    };

    this.mockDetails.set(id, deployment);
    return this.respond(deployment);
  }

  getDeployment(deploymentId: number): Observable<DeploymentDetails> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      return this.http
        .get<ApiResponse<{ deployment: DeploymentDetails }>>(`/api/deployments/${deploymentId}`, {
          headers: this.headers(),
        })
        .pipe(map(response => response.data.deployment));
    }

    const deployment = this.requireMock(deploymentId);
    if (deployment.status === 'running' || deployment.status === 'queued') {
      this.advanceMockDeployment(deployment);
    }
    return this.respond(deployment, 180);
  }

  startDeployment(deploymentId: number): Observable<DeploymentDetails> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      return this.http
        .post<ApiResponse<{ deployment: DeploymentDetails }>>(
          `/api/deployments/${deploymentId}/start`,
          {},
          { headers: this.headers() },
        )
        .pipe(map(response => response.data.deployment));
    }

    const deployment = this.requireMock(deploymentId);
    deployment.status = 'running';
    deployment.startedAt ??= new Date().toISOString();
    deployment.currentStage = 'prepare';
    deployment.currentStageLabel = 'Préparation du workspace';
    this.appendLog(deployment, 'system', 'info', 'Le worker a pris en charge le déploiement.');
    return this.respond(deployment);
  }

  cancelDeployment(deploymentId: number): Observable<DeploymentDetails> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      return this.http
        .post<ApiResponse<{ deployment: DeploymentDetails }>>(
          `/api/deployments/${deploymentId}/cancel`,
          {},
          { headers: this.headers() },
        )
        .pipe(map(response => response.data.deployment));
    }

    const deployment = this.requireMock(deploymentId);
    deployment.status = 'cancelled';
    deployment.finishedAt = new Date().toISOString();
    deployment.currentStageLabel = 'Annulé par l’utilisateur';
    const running = deployment.steps.find(step => step.status === 'running');
    if (running) running.status = 'cancelled';
    this.appendLog(deployment, 'system', 'warning', 'Le déploiement a été annulé.');
    return this.respond(deployment);
  }

  retryDeployment(deploymentId: number): Observable<DeploymentDetails> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      return this.http
        .post<ApiResponse<{ deployment: DeploymentDetails }>>(
          `/api/deployments/${deploymentId}/retry`,
          {},
          { headers: this.headers() },
        )
        .pipe(map(response => response.data.deployment));
    }

    const deployment = this.requireMock(deploymentId);
    const failed = deployment.steps.find(step => step.status === 'failed');
    if (failed) {
      failed.status = 'pending';
      failed.errorCode = null;
      failed.errorMessage = null;
      failed.startedAt = null;
      failed.finishedAt = null;
    }
    deployment.status = 'running';
    deployment.finishedAt = null;
    deployment.incident = null;
    deployment.diagnostic = this.emptyDiagnostic();
    deployment.currentStage = failed?.stage ?? 'prepare';
    deployment.currentStageLabel = failed?.label ?? 'Nouvelle tentative';
    this.appendLog(deployment, 'system', 'info', 'Nouvelle tentative lancée depuis l’étape échouée.');
    return this.respond(deployment);
  }

  confirmSynchronization(deploymentId: number): Observable<DeploymentDetails> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      return this.http
        .post<ApiResponse<{ deployment: DeploymentDetails }>>(
          `/api/deployments/${deploymentId}/confirm-sync`,
          {},
          { headers: this.headers() },
        )
        .pipe(map(response => response.data.deployment));
    }

    const deployment = this.requireMock(deploymentId);
    deployment.status = 'running';
    deployment.currentStage = 'argocd';
    deployment.currentStageLabel = 'Synchronisation Argo CD';
    const argocd = deployment.steps.find(step => step.stage === 'argocd');
    if (argocd) argocd.status = 'pending';
    this.appendLog(deployment, 'argocd', 'info', 'Synchronisation confirmée par l’utilisateur.');
    return this.respond(deployment);
  }

  requestDiagnosis(deploymentId: number): Observable<DeploymentDiagnostic> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      return this.http
        .post<ApiResponse<{ diagnostic: DeploymentDiagnostic }>>(
          `/api/deployments/${deploymentId}/diagnostic`,
          {},
          { headers: this.headers() },
        )
        .pipe(map(response => response.data.diagnostic));
    }

    const deployment = this.requireMock(deploymentId);
    const now = new Date().toISOString();
    deployment.diagnostic = {
      status: 'completed',
      cause: deployment.incident?.code === 'REGISTRY_AUTHENTICATION_FAILED'
        ? 'Le registre Nexus a refusé le credential configuré pour la connexion nexus-lab.'
        : 'La configuration du port du conteneur ne correspond probablement pas au port écouté par l’application.',
      explanation: deployment.incident?.code === 'REGISTRY_AUTHENTICATION_FAILED'
        ? 'L’image a été construite correctement. L’échec intervient uniquement au moment du push HTTP vers Nexus, ce qui écarte un problème Dockerfile.'
        : 'Les événements Kubernetes montrent un échec de la readiness probe après le démarrage du conteneur.',
      confidence: 'high',
      targetPhase: deployment.incident?.code === 'REGISTRY_AUTHENTICATION_FAILED'
        ? 'integration'
        : 'generation',
      evidence: deployment.incident?.code === 'REGISTRY_AUTHENTICATION_FAILED'
        ? [
            'Réponse HTTP 401 pendant docker push.',
            'Le build local s’est terminé avec succès.',
            'La connexion concernée est nexus-lab.',
          ]
        : [
            'La probe cible le port 8000.',
            'La commande Gunicorn déclare le port 5000.',
          ],
      corrections: deployment.incident?.code === 'REGISTRY_AUTHENTICATION_FAILED'
        ? [{
            id: 1,
            title: 'Remplacer le credential Nexus',
            summary: 'Ouvrir la connexion nexus-lab, enregistrer un token valide puis relancer depuis Publication Nexus.',
            targetPhase: 'integration',
            targetFile: null,
            diff: null,
            risk: 'low',
            status: 'proposed',
          }]
        : [{
            id: 2,
            title: 'Aligner les ports du Service et du Deployment',
            summary: 'Mettre containerPort et targetPort à 5000 dans une nouvelle révision des artefacts.',
            targetPhase: 'generation',
            targetFile: 'helm/templates/deployment.yaml',
            diff: '- containerPort: 8000\n+ containerPort: 5000\n- targetPort: 8000\n+ targetPort: 5000',
            risk: 'low',
            status: 'proposed',
          }],
      providerConnectionId: 1,
      model: 'demo-model',
      fallback: false,
      providerError: null,
      createdAt: now,
    };
    deployment.chat.push({
      id: this.nextChatId++,
      role: 'assistant',
      content: `${deployment.diagnostic.cause}\n\n${deployment.diagnostic.explanation}`,
      createdAt: now,
    });
    return this.respond(deployment.diagnostic, 700);
  }

  sendDiagnosticMessage(deploymentId: number, content: string): Observable<DeploymentChatMessage[]> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      return this.http
        .post<ApiResponse<{ messages: DeploymentChatMessage[] }>>(
          `/api/deployments/${deploymentId}/diagnostic/messages`,
          { content },
          { headers: this.headers() },
        )
        .pipe(map(response => response.data.messages));
    }

    const deployment = this.requireMock(deploymentId);
    const now = new Date().toISOString();
    deployment.chat.push({ id: this.nextChatId++, role: 'user', content, createdAt: now });
    deployment.chat.push({
      id: this.nextChatId++,
      role: 'assistant',
      content: this.mockAssistantAnswer(content, deployment),
      createdAt: new Date(Date.now() + 300).toISOString(),
    });
    return this.respond(deployment.chat, 500);
  }

  approveCorrection(deploymentId: number, correctionId: number): Observable<DeploymentDiagnostic> {
    if (!DEPLOYMENTS_DEMO_MODE) {
      return this.http
        .post<ApiResponse<{ diagnostic: DeploymentDiagnostic }>>(
          `/api/deployments/${deploymentId}/corrections/${correctionId}/approve`,
          {},
          { headers: this.headers() },
        )
        .pipe(map(response => response.data.diagnostic));
    }

    const deployment = this.requireMock(deploymentId);
    const correction = deployment.diagnostic.corrections.find(item => item.id === correctionId);
    if (correction) correction.status = 'approved';
    deployment.chat.push({
      id: this.nextChatId++,
      role: 'system',
      content: correction?.targetPhase === 'integration'
        ? 'Correction approuvée. Ouvrez l’intégration concernée, remplacez le credential puis revenez relancer le déploiement.'
        : 'Correction approuvée. Une nouvelle révision de génération sera créée par le backend avant toute nouvelle tentative.',
      createdAt: new Date().toISOString(),
    });
    return this.respond(deployment.diagnostic);
  }

  private headers(): HttpHeaders {
    const token = this.auth.getAccessToken();
    return token
      ? new HttpHeaders({ Authorization: `Bearer ${token}` })
      : new HttpHeaders();
  }

  private respond<T>(value: T, wait = 280): Observable<T> {
    return of(this.clone(value)).pipe(delay(wait));
  }

  private clone<T>(value: T): T {
    return JSON.parse(JSON.stringify(value)) as T;
  }

  private toSummary(item: DeploymentDetails): DeploymentSummary {
    const {
      syncMode: _syncMode,
      preflight: _preflight,
      components: _components,
      steps: _steps,
      logs: _logs,
      resources: _resources,
      incident: _incident,
      diagnostic: _diagnostic,
      chat: _chat,
      health: _health,
      ...summary
    } = item;
    return this.clone(summary);
  }

  private requireMock(id: number): DeploymentDetails {
    const deployment = this.mockDetails.get(id);
    if (!deployment) throw new Error(`Déploiement #${id} introuvable.`);
    return deployment;
  }

  private appendLog(
    deployment: DeploymentDetails,
    scope: DeploymentLogScope,
    level: DeploymentLogLevel,
    message: string,
    stepId: string | null = null,
    componentName: string | null = null,
  ): void {
    deployment.logs.push({
      id: this.nextLogId++,
      createdAt: new Date().toISOString(),
      scope,
      level,
      stepId,
      componentName,
      message,
    });
  }

  private advanceMockDeployment(deployment: DeploymentDetails): void {
    if (deployment.status === 'queued') deployment.status = 'running';
    const current = deployment.steps.find(step => step.status === 'running');
    if (current) {
      current.status = 'succeeded';
      current.finishedAt = new Date().toISOString();
      current.durationSeconds = Math.max(1, Math.round(Math.random() * 10));
      this.appendLog(deployment, this.scopeForStage(current.stage), 'success', `${current.label} terminée.`, current.id);
    }

    const next = deployment.steps.find(step => step.status === 'pending');
    if (!next) {
      deployment.status = 'succeeded';
      deployment.progress = 100;
      deployment.currentStage = 'health';
      deployment.currentStageLabel = 'Application saine';
      deployment.finishedAt = new Date().toISOString();
      deployment.gitopsCommit = '7c2f91a';
      deployment.health = {
        argocdSync: 'Synced',
        argocdHealth: 'Healthy',
        readyPods: deployment.components.length,
        totalPods: deployment.components.length,
        ingressReady: true,
        migrationStatus: 'succeeded',
        applicationUrl: `https://${deployment.projectSlug}.${deployment.environmentName.toLowerCase()}.local`,
      };
      deployment.resources = this.successResources(deployment);
      this.appendLog(deployment, 'system', 'success', 'Déploiement terminé avec succès.');
      return;
    }

    if (next.stage === 'argocd' && deployment.syncMode === 'confirm_before_sync') {
      deployment.status = 'waiting_confirmation';
      deployment.currentStage = 'argocd';
      deployment.currentStageLabel = 'Confirmation Argo CD requise';
      this.appendLog(deployment, 'argocd', 'warning', 'Le pipeline attend votre confirmation avant la synchronisation.', next.id);
      return;
    }

    next.status = 'running';
    next.startedAt = new Date().toISOString();
    deployment.currentStage = next.stage;
    deployment.currentStageLabel = next.label;
    const completed = deployment.steps.filter(step => step.status === 'succeeded').length;
    deployment.progress = Math.round((completed / deployment.steps.length) * 100);
    this.appendLog(deployment, this.scopeForStage(next.stage), 'info', `${next.label} en cours…`, next.id);
  }

  private scopeForStage(stage: DeploymentStageKey): DeploymentLogScope {
    const scopes: Record<DeploymentStageKey, DeploymentLogScope> = {
      prepare: 'system',
      source: 'system',
      build: 'docker',
      registry: 'nexus',
      gitops: 'gitops',
      argocd: 'argocd',
      kubernetes: 'kubernetes',
      health: 'application',
    };
    return scopes[stage];
  }

  private mockAssistantAnswer(content: string, deployment: DeploymentDetails): string {
    const normalized = content.toLowerCase();
    if (normalized.includes('preuve') || normalized.includes('pourquoi')) {
      return deployment.incident?.code === 'REGISTRY_AUTHENTICATION_FAILED'
        ? 'Le build se termine avant l’erreur. La première réponse en échec est un HTTP 401 sur l’endpoint Docker Registry de nexus-lab. Cela indique un problème de credential, pas un problème dans le Dockerfile.'
        : 'La commande de démarrage et les événements de probe indiquent deux ports différents. C’est la preuve principale utilisée par le diagnostic.';
    }
    if (normalized.includes('corrig') || normalized.includes('solution')) {
      return 'J’ai préparé une correction contrôlée. Examinez la carte « Correction proposée » puis approuvez-la. SApixi créera une nouvelle révision ou vous dirigera vers l’intégration concernée, sans modifier silencieusement la version exécutée.';
    }
    return 'Je peux préciser la cause, expliquer les preuves, comparer plusieurs solutions ou préparer une correction contrôlée. Aucun secret ne m’est transmis et aucune action n’est appliquée sans votre validation.';
  }

  private emptyDiagnostic(): DeploymentDiagnostic {
    return {
      status: 'idle',
      cause: null,
      explanation: null,
      confidence: null,
      targetPhase: null,
      evidence: [],
      corrections: [],
      providerConnectionId: null,
      model: null,
      fallback: false,
      providerError: null,
      createdAt: null,
    };
  }

  private emptyHealth(): DeploymentHealth {
    return {
      argocdSync: 'Unknown',
      argocdHealth: 'Unknown',
      readyPods: 0,
      totalPods: 0,
      ingressReady: false,
      migrationStatus: 'pending',
      applicationUrl: null,
    };
  }

  private defaultPreflight(): DeploymentPreflightItem[] {
    return [
      { key: 'artifacts', label: 'Artefacts approuvés', description: 'La génération a été validée par un utilisateur.', status: 'ready', integrationName: null, actionLabel: null, actionPath: null },
      { key: 'kubernetes', label: 'Kubernetes', description: 'Le cluster cible répond et le namespace est disponible.', status: 'ready', integrationName: 'cluster-lab', actionLabel: 'Ouvrir', actionPath: '/integrations' },
      { key: 'argocd', label: 'Argo CD', description: 'Le service GitOps est accessible.', status: 'ready', integrationName: 'argocd-lab', actionLabel: 'Ouvrir', actionPath: '/integrations' },
      { key: 'registry', label: 'Registre Nexus', description: 'Le registre accepte les opérations de lecture.', status: 'ready', integrationName: 'nexus-lab', actionLabel: 'Ouvrir', actionPath: '/integrations' },
      { key: 'gitops', label: 'Source Argo CD', description: 'La source Argo CD sélectionnée est disponible.', status: 'ready', integrationName: 'gitops-lab', actionLabel: 'Ouvrir', actionPath: '/integrations' },
      { key: 'secrets', label: 'Secrets Kubernetes', description: 'Tous les secrets requis sont associés.', status: 'ready', integrationName: null, actionLabel: null, actionPath: null },
    ];
  }

  private defaultComponents(tag: string): DeploymentComponent[] {
    return [
      { id: 'api', name: 'API Flask', type: 'api', imageRepository: 'nexus.lab.local/sapixi/api', imageTag: tag, imageDigest: null, port: 5000, replicas: 1, buildStatus: 'pending', registryStatus: 'pending' },
      { id: 'web', name: 'Frontend Angular', type: 'frontend', imageRepository: 'nexus.lab.local/sapixi/web', imageTag: tag, imageDigest: null, port: 8080, replicas: 1, buildStatus: 'pending', registryStatus: 'pending' },
    ];
  }

  private defaultSteps(): DeploymentStep[] {
    const rows: Array<[DeploymentStageKey, string, string]> = [
      ['prepare', 'Préparer le workspace', 'Créer un espace temporaire sécurisé.'],
      ['source', 'Récupérer le commit approuvé', 'Charger exactement la version analysée.'],
      ['build', 'Construire les images', 'Exécuter les builds Docker contrôlés.'],
      ['registry', 'Publier vers Nexus', 'Pousser les images et enregistrer les digests.'],
      ['gitops', 'Publier la configuration', 'Publier les charts vers la source Argo CD sélectionnée.'],
      ['argocd', 'Synchroniser Argo CD', 'Synchroniser la source confirmée avec Kubernetes.'],
      ['kubernetes', 'Observer Kubernetes', 'Attendre les pods, services et jobs.'],
      ['health', 'Vérifier la santé', 'Contrôler Argo CD, les probes et l’Ingress.'],
    ];
    return rows.map(([stage, label, description], index) => ({
      id: `step-${index + 1}`,
      stage,
      order: index + 1,
      label,
      description,
      status: 'pending',
      startedAt: null,
      finishedAt: null,
      durationSeconds: null,
      details: {},
      errorCode: null,
      errorMessage: null,
    }));
  }

  private mockProjectOptions(): DeploymentProjectOption[] {
    return [
      {
        id: 3,
        name: 'sapixi-platform',
        slug: 'sapixi-platform',
        environmentId: 1,
        environmentName: 'Lab',
        namespace: 'sapixi-lab',
        generations: [
          { id: 14, label: 'Génération #14', sourceCommit: '0e463202076b', createdAt: '2026-08-06T09:32:00Z', componentCount: 2, approvedArtifactCount: 17 },
          { id: 13, label: 'Génération #13', sourceCommit: '9a2b117cd641', createdAt: '2026-08-05T18:10:00Z', componentCount: 2, approvedArtifactCount: 17 },
        ],
      },
      {
        id: 7,
        name: 'orders-api',
        slug: 'orders-api',
        environmentId: 2,
        environmentName: 'Test',
        namespace: 'orders-test',
        generations: [
          { id: 22, label: 'Génération #22', sourceCommit: 'a782cc1009de', createdAt: '2026-08-05T13:45:00Z', componentCount: 1, approvedArtifactCount: 9 },
        ],
      },
    ];
  }

  private successResources(deployment: DeploymentDetails): DeploymentResource[] {
    return [
      { id: 'argocd-app', kind: 'argocd_application', name: deployment.projectSlug, namespace: 'argocd', status: 'Synced', health: 'healthy', ready: null, image: null, restarts: null, age: '2 min', message: 'Application Healthy', url: null },
      { id: 'api-deploy', kind: 'deployment', name: `${deployment.projectSlug}-api`, namespace: deployment.namespace, status: 'Available', health: 'healthy', ready: '1/1', image: `${deployment.components[0].imageRepository}:${deployment.version}`, restarts: null, age: '2 min', message: null, url: null },
      { id: 'web-deploy', kind: 'deployment', name: `${deployment.projectSlug}-web`, namespace: deployment.namespace, status: 'Available', health: 'healthy', ready: '1/1', image: `${deployment.components[1].imageRepository}:${deployment.version}`, restarts: null, age: '2 min', message: null, url: null },
      { id: 'api-pod', kind: 'pod', name: `${deployment.projectSlug}-api-7db58-abc`, namespace: deployment.namespace, status: 'Running', health: 'healthy', ready: '1/1', image: `${deployment.components[0].imageRepository}:${deployment.version}`, restarts: 0, age: '1 min', message: null, url: null },
      { id: 'web-pod', kind: 'pod', name: `${deployment.projectSlug}-web-84bc7-def`, namespace: deployment.namespace, status: 'Running', health: 'healthy', ready: '1/1', image: `${deployment.components[1].imageRepository}:${deployment.version}`, restarts: 0, age: '1 min', message: null, url: null },
      { id: 'ingress', kind: 'ingress', name: deployment.projectSlug, namespace: deployment.namespace, status: 'Ready', health: 'healthy', ready: null, image: null, restarts: null, age: '1 min', message: 'TLS actif', url: `https://${deployment.projectSlug}.${deployment.environmentName.toLowerCase()}.local` },
    ];
  }

  private createMockDeployments(): DeploymentDetails[] {
    const base = (id: number, projectId: number, projectName: string, version: string, status: DeploymentStatus, progress: number, createdAt: string): DeploymentDetails => ({
      id,
      projectId,
      projectName,
      projectSlug: projectName,
      generationId: projectId === 3 ? 14 : 22,
      environmentId: projectId === 3 ? 1 : 2,
      environmentName: projectId === 3 ? 'Lab' : 'Test',
      namespace: projectId === 3 ? 'sapixi-lab' : 'orders-test',
      version,
      sourceCommit: projectId === 3 ? '0e463202076b' : 'a782cc1009de',
      gitopsCommit: status === 'succeeded' ? '7c2f91a' : null,
      status,
      currentStage: status === 'failed' ? 'registry' : status === 'running' ? 'argocd' : status === 'succeeded' ? 'health' : 'argocd',
      currentStageLabel: status === 'failed' ? 'Publication Nexus' : status === 'running' ? 'Synchronisation Argo CD' : status === 'succeeded' ? 'Application saine' : 'Confirmation requise',
      progress,
      createdByName: 'Amine Salhi',
      createdAt,
      startedAt: createdAt,
      finishedAt: status === 'succeeded' || status === 'failed' ? new Date(new Date(createdAt).getTime() + 420000).toISOString() : null,
      note: 'Version de démonstration du workflow de déploiement.',
      syncMode: 'confirm_before_sync',
      preflight: this.defaultPreflight(),
      components: this.defaultComponents(version),
      steps: this.defaultSteps(),
      logs: [],
      resources: [],
      incident: null,
      diagnostic: this.emptyDiagnostic(),
      chat: [{ id: this.nextChatId++, role: 'assistant', content: 'Je surveille cette exécution. En cas d’échec, lancez un diagnostic pour obtenir une correction contrôlée.', createdAt }],
      health: this.emptyHealth(),
    });

    const failed = base(42, 7, 'orders-api', 'v1.8.3', 'failed', 44, '2026-08-06T09:42:00Z');
    failed.steps.slice(0, 3).forEach(step => step.status = 'succeeded');
    failed.steps[3].status = 'failed';
    failed.steps[3].errorCode = 'REGISTRY_AUTHENTICATION_FAILED';
    failed.steps[3].errorMessage = 'Nexus a refusé le credential configuré.';
    failed.incident = {
      code: 'REGISTRY_AUTHENTICATION_FAILED',
      title: 'Échec de la publication Nexus',
      message: 'L’image orders-api a été construite, mais Nexus a refusé l’authentification.',
      stage: 'registry',
      stepId: failed.steps[3].id,
      componentName: 'Orders API',
      integrationName: 'nexus-lab',
      occurredAt: '2026-08-06T09:49:00Z',
      retryable: true,
      requiresNewGeneration: false,
    };
    failed.logs = [
      { id: 401, createdAt: '2026-08-06T09:42:01Z', scope: 'system', level: 'info', stepId: 'step-1', componentName: null, message: 'Workspace temporaire créé.' },
      { id: 402, createdAt: '2026-08-06T09:43:10Z', scope: 'docker', level: 'success', stepId: 'step-3', componentName: 'Orders API', message: 'Image construite avec succès.' },
      { id: 403, createdAt: '2026-08-06T09:49:02Z', scope: 'nexus', level: 'error', stepId: 'step-4', componentName: 'Orders API', message: 'unauthorized: authentication required (HTTP 401).' },
    ];

    const running = base(41, 3, 'sapixi-platform', 'v1.0.2', 'running', 68, '2026-08-06T09:25:00Z');
    running.steps.slice(0, 5).forEach(step => step.status = 'succeeded');
    running.steps[5].status = 'running';
    running.logs = [
      { id: 410, createdAt: '2026-08-06T09:25:02Z', scope: 'system', level: 'info', stepId: 'step-1', componentName: null, message: 'Déploiement pris en charge par le worker.' },
      { id: 411, createdAt: '2026-08-06T09:27:20Z', scope: 'docker', level: 'success', stepId: 'step-3', componentName: 'API Flask', message: 'Image API publiée.' },
      { id: 412, createdAt: '2026-08-06T09:29:11Z', scope: 'gitops', level: 'success', stepId: 'step-5', componentName: null, message: 'Commit GitOps créé : 8a721de.' },
      { id: 413, createdAt: '2026-08-06T09:30:03Z', scope: 'argocd', level: 'info', stepId: 'step-6', componentName: null, message: 'Synchronisation Argo CD en cours.' },
    ];

    const succeeded = base(40, 3, 'sapixi-platform', 'v1.0.1', 'succeeded', 100, '2026-08-05T16:10:00Z');
    succeeded.steps.forEach(step => step.status = 'succeeded');
    succeeded.health = { argocdSync: 'Synced', argocdHealth: 'Healthy', readyPods: 2, totalPods: 2, ingressReady: true, migrationStatus: 'succeeded', applicationUrl: 'https://sapixi-platform.lab.local' };
    succeeded.resources = this.successResources(succeeded);
    succeeded.logs = [{ id: 420, createdAt: '2026-08-05T16:18:00Z', scope: 'system', level: 'success', stepId: 'step-8', componentName: null, message: 'Déploiement terminé avec succès.' }];

    const waiting = base(39, 3, 'sapixi-platform', 'v1.0.0', 'waiting_confirmation', 61, '2026-08-05T11:20:00Z');
    waiting.steps.slice(0, 5).forEach(step => step.status = 'succeeded');
    waiting.logs = [{ id: 430, createdAt: '2026-08-05T11:27:00Z', scope: 'argocd', level: 'warning', stepId: 'step-6', componentName: null, message: 'Confirmation requise avant synchronisation.' }];

    return [failed, running, succeeded, waiting];
  }
}