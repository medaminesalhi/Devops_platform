import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Observable, map } from 'rxjs';
import { Auth } from '../auth/auth';

export type WorkflowGenerationMode = 'hybrid' | 'deterministic';
export type WorkflowGenerationStatus =
  | 'pending'
  | 'running'
  | 'awaiting_review'
  | 'completed'
  | 'confirmed'
  | 'failed'
  | 'cancelled'
  | 'superseded';
export type ArtifactValidationStatus = 'pending' | 'passed' | 'warning' | 'failed';
export type ArtifactReviewStatus = 'pending_review' | 'approved' | 'rejected';
export type ArtifactReviewDecision = 'approved' | 'rejected';
export type ProposalStatus = 'preparing' | 'needs_input' | 'ready' | 'confirmed' | 'failed';
export type ExposureMode = 'internal' | 'public';
export type PersistenceChoice = 'none' | 'suggested' | 'required';
export type MigrationChoice = 'automatic' | 'enabled' | 'disabled';
export type DeliveryMode = 'git' | 'helm';
export type GitRefreshMode = 'polling' | 'webhook';

export interface WorkflowProjectSummary {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  status: string;
  analysisStatus: string | null;
  generationStatus: string | null;
  deploymentContractStatus: string | null;
}

export interface WorkflowAnalysisSummary {
  id: number;
  version: string | null;
  selectedSubdirectory: string | null;
  summary: Record<string, unknown>;
  confirmedAt: string | null;
}

export interface WorkflowComponentSummary {
  id: number;
  name: string;
  componentType: string;
  rootPath: string;
  runtime: string | null;
  framework: string | null;
  packageManager: string | null;
  buildCommand: string | null;
  startCommand: string | null;
  detectedPort: number | null;
  deployable: boolean;
  dockerfilePath: string | null;
  helmChartPath: string | null;
  kubernetesPaths: string[];
  environmentVariables: Array<Record<string, unknown>>;
  confidence: number | null;
  configuration: Record<string, unknown>;
  userModified: boolean;
}

export interface WorkflowEnvironmentService {
  role: string;
  required: boolean;
  connectionId: number;
  connectionName: string;
  providerType: string;
  baseUrl: string;
  status: string;
  lastCheckedAt: string | null;
  lastLatencyMs: number | null;
}

export interface WorkflowEnvironment {
  id: number;
  name: string;
  code: string;
  environmentType: string;
  description: string | null;
  namespace: string;
  domain: string | null;
  configurationStatus: string;
  isDefault: boolean;
  services: WorkflowEnvironmentService[];
}

export interface WorkflowAiConnection {
  id: number;
  name: string;
  providerType: string;
  baseUrl: string;
  description: string | null;
  enabled: boolean;
  verifySsl: boolean;
  status: string;
  authType: string;
  credentialConfigured: boolean;
  lastCheckedAt: string | null;
  lastLatencyMs: number | null;
}

export interface ContractValidationIssue {
  path: string;
  code: string;
  message: string;
}

export interface ContractValidationReport {
  valid: boolean;
  errorCount: number;
  warningCount: number;
  questionCount: number;
  errors: ContractValidationIssue[];
  warnings: ContractValidationIssue[];
  questions: ContractValidationIssue[];
}

export interface DeploymentContractProbe {
  enabled: boolean;
  path: string;
  initialDelaySeconds: number;
  periodSeconds: number;
  timeoutSeconds: number;
  failureThreshold: number;
}

export interface DeploymentContractVariable {
  name: string;
  required: boolean;
  description: string;
  value?: string;
}

export interface DeploymentContractVolume {
  name: string;
  mountPath: string;
  size: string;
  accessMode: 'ReadWriteOnce' | 'ReadOnlyMany' | 'ReadWriteMany' | 'ReadWriteOncePod';
  storageClass: string;
  readOnly: boolean;
}

export interface DeploymentContractComponent {
  id: number;
  name: string;
  slug: string;
  rootPath: string;
  componentType: string;
  runtime: { name: string; version: string };
  framework: string;
  packageManager: string;
  deployable: boolean;
  build: {
    context: string;
    dockerfilePath: string;
    helmChartPath: string;
    installCommand: string;
    buildCommand: string;
    outputPath: string;
  };
  container: {
    startCommand: string;
    port: number;
    workingDirectory: string;
    runAsUser: number;
    readOnlyRootFilesystem: boolean;
  };
  replicas: number;
  service: {
    enabled: boolean;
    type: 'ClusterIP' | 'NodePort' | 'LoadBalancer';
    port: number;
    targetPort: number;
  };
  ingress: {
    enabled: boolean;
    className: string;
    host: string;
    path: string;
    pathType: 'Prefix' | 'Exact' | 'ImplementationSpecific';
    tlsEnabled: boolean;
    tlsSecretName: string;
    certManagerIssuer: string;
    annotations: Record<string, string>;
  };
  resources: {
    requests: { cpu: string; memory: string };
    limits: { cpu: string; memory: string };
  };
  probes: {
    startup: DeploymentContractProbe;
    readiness: DeploymentContractProbe;
    liveness: DeploymentContractProbe;
  };
  configuration: DeploymentContractVariable[];
  secrets: DeploymentContractVariable[];
  volumes: DeploymentContractVolume[];
  migration: { enabled: boolean; command: string; backoffLimit: number };
  dependencies: string[];
}

export interface DeploymentContract {
  schemaVersion: number;
  project: {
    id: number;
    name: string;
    slug: string;
    analysisRunId: number;
    commitSha: string;
  };
  target: {
    environmentId: number;
    environmentName: string;
    environmentCode: string;
    namespace: string;
    domain: string | null;
    kubernetes: { server: string };
    registry: {
      connectionId: number;
      repositoryName: string;
      repositoryUrl: string | null;
      endpointUrl: string;
      host: string;
      repositoryPrefix: string;
      imagePullSecretName: string;
    };
    delivery: {
      mode: DeliveryMode;
      connectionId: number;
      repositoryId?: number | string | null;
      repositoryName: string;
      repositoryUrl: string;
      targetRevision: string;
      basePath: string;
      refreshMode: GitRefreshMode;
    };
    argocd: {
      serverUrl: string;
      namespace: string;
      projectName: string;
      automaticSync: boolean;
      prune: boolean;
      selfHeal: boolean;
    };
  };
  policies: {
    preserveExistingDockerfile: boolean;
    preserveExistingHelmChart: boolean;
    requireNonRoot: boolean;
    allowPrivileged: boolean;
    requireManualArgoSync: boolean;
    maximumAiContextBytes: number;
  };
  components: DeploymentContractComponent[];
}

export interface SavedDeploymentContract {
  id: number;
  projectId: number;
  analysisRunId: number;
  environmentId: number;
  status: 'draft' | 'confirmed' | 'superseded';
  revision: number;
  namespace: string;
  domain: string | null;
  contract: DeploymentContract;
  validation: ContractValidationReport;
  project: { name: string; slug: string };
  analysis: { version: string | null; confirmedAt: string | null };
  environment: { name: string; code: string; environmentType: string };
  createdBy: number | null;
  updatedBy: number | null;
  confirmedBy: number | null;
  createdAt: string | null;
  updatedAt: string | null;
  confirmedAt: string | null;
}

export interface DeploymentProposalAdvancedDecisions {
  startCommand: string | null;
  port: number | null;
  serviceType: 'ClusterIP' | 'NodePort' | 'LoadBalancer';
  ingressClassName: string;
  ingressPath: string;
  ingressPathType: 'Prefix' | 'Exact' | 'ImplementationSpecific';
  ingressTlsEnabled: boolean;
  ingressTlsSecretName: string | null;
  ingressCertManagerIssuer: string | null;
  readinessPath: string;
  livenessPath: string;
  cpuRequest: string;
  cpuLimit: string;
  memoryRequest: string;
  memoryLimit: string;
}

export interface DeploymentProposalDecisions {
  namespace: string;
  exposureMode: ExposureMode;
  domain: string | null;
  replicas: number;
  persistence: PersistenceChoice;
  migration: MigrationChoice;

  // Choix d'infrastructure faits en phase 3.
  imageRepositoryName: string;
  deliveryMode: DeliveryMode;
  gitRepositoryId: number | null;
  // Chemin groupe/projet ou URL GitLab. Permet une saisie manuelle
  // lorsque l'API GitLab ne retourne aucun projet dans la découverte.
  gitRepositoryRef: string | null;
  gitBranch: string;
  gitRefreshMode: GitRefreshMode;
  helmRepositoryName: string | null;

  advanced: DeploymentProposalAdvancedDecisions;
}

export interface DeploymentTargetRepositoryOption {
  provider: 'nexus' | 'gitlab';
  id: string;
  name: string;
  label: string;
  format: string;
  type: string;
  url: string | null;
  endpointUrl: string | null;
  defaultBranch: string | null;
  projectId: number | null;
  writable: boolean;
  metadata: Record<string, unknown>;
}

export interface DeploymentTargetOptions {
  imageRepositories: DeploymentTargetRepositoryOption[];
  helmRepositories: DeploymentTargetRepositoryOption[];
  gitRepositories: DeploymentTargetRepositoryOption[];
  gitDiscoveryError: string | null;
  nexusConnection: { id: number; name: string; status: string } | null;
  gitConnection: { id: number; name: string; status: string } | null;
}

export interface DeploymentProposalQuestion {
  id: string;
  componentId: number | null;
  label: string;
  description: string;
  required: boolean;
  answer: string | null;
  choices: string[];
}

export interface DeploymentProposalComponent {
  componentId: number;
  name: string;
  componentType: string;
  runtime: string;
  framework: string;
  confidence: number;
  summary: string;
  docker: {
    strategy: string;
    installCommand: string;
    buildCommand: string;
    startCommand: string;
    port: number;
  };
  kubernetes: {
    serviceType: string;
    ingressEnabled: boolean;
    host: string | null;
    ingressClassName: string;
    ingressPath: string;
    ingressPathType: 'Prefix' | 'Exact' | 'ImplementationSpecific';
    ingressTlsEnabled: boolean;
    ingressTlsSecretName: string | null;
    ingressCertManagerIssuer: string | null;
    replicas: number;
    readinessPath: string | null;
    livenessPath: string | null;
    cpuRequest: string;
    cpuLimit: string;
    memoryRequest: string;
    memoryLimit: string;
  };
  persistence: {
    enabled: boolean;
    mountPath: string | null;
    size: string | null;
  };
  migration: {
    enabled: boolean;
    command: string | null;
    requiresConfirmation: boolean;
  };
  warnings: string[];
}

export interface DeploymentProposal {
  id: number;
  projectId: number;
  analysisRunId: number;
  environmentId: number;
  contractId: number | null;
  status: ProposalStatus;
  mode: WorkflowGenerationMode;
  aiConnectionId: number | null;
  aiModel: string | null;
  decisions: DeploymentProposalDecisions;
  environment: WorkflowEnvironment;
  components: DeploymentProposalComponent[];
  questions: DeploymentProposalQuestion[];
  warnings: string[];
  validation: ContractValidationReport;
  createdAt: string | null;
  updatedAt: string | null;
  confirmedAt: string | null;
}

export interface WorkflowGeneration {
  id: number;
  projectId: number;
  analysisRunId: number;
  environmentId: number;
  contractId: number | null;
  aiRunId: number | null;
  aiConnectionId: number | null;
  aiModel: string | null;
  generationMode: WorkflowGenerationMode;
  promptVersion: string | null;
  status: WorkflowGenerationStatus;
  progress: number;
  currentStep: string;
  summary: Record<string, unknown>;
  error: { code: string | null; message: string | null } | null;
  project: { name: string | null; slug: string | null };
  analysis: { version: string | null; confirmedAt: string | null };
  environment: {
    name: string | null;
    code: string | null;
    environmentType: string | null;
    namespace: string | null;
    domain: string | null;
  };
  contract: {
    status: string | null;
    revision: number | null;
    validation: ContractValidationReport;
  };
  createdBy: number | null;
  confirmedBy: number | null;
  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  confirmedAt: string | null;
}

export interface WorkflowGenerationEvent {
  id: number;
  generationRunId: number;
  level: 'info' | 'success' | 'warning' | 'error';
  step: string;
  message: string;
  details: Record<string, unknown>;
  createdAt: string | null;
}

export interface ArtifactValidationMessage {
  level: 'info' | 'warning' | 'error' | string;
  code: string;
  message: string;
}

export interface WorkflowArtifact {
  id: number;
  generationRunId: number;
  projectId: number;
  componentId: number | null;
  componentName: string | null;
  componentRootPath: string | null;
  artifactType: string;
  relativePath: string;
  contentSha256: string;
  artifactStatus: string;
  reviewStatus: ArtifactReviewStatus;
  validationStatus: ArtifactValidationStatus;
  validationMessages: ArtifactValidationMessage[];
  reviewComment: string | null;
  reviewedBy: number | null;
  reviewedAt: string | null;
  editedBy: number | null;
  editedAt: string | null;
  metadata: Record<string, unknown>;
  createdAt: string | null;
  updatedAt: string | null;
  content?: string;
  originalContent?: string | null;
}

export interface WorkflowOverview {
  canWrite: boolean;
  project: WorkflowProjectSummary;
  analysis: WorkflowAnalysisSummary | null;
  components: WorkflowComponentSummary[];
  environments: WorkflowEnvironment[];
  latestContract: SavedDeploymentContract | null;
  latestProposal?: DeploymentProposal | null;
  aiConnections: WorkflowAiConnection[];
  latestGeneration: WorkflowGeneration | null;
}

interface ApiResponse<T> {
  success: boolean;
  data: T;
}

@Injectable({ providedIn: 'root' })
export class WorkflowService {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);

  getOverview(projectId: number): Observable<WorkflowOverview> {
    return this.http
      .get<ApiResponse<WorkflowOverview>>(`/api/projects/${projectId}/workflow`, {
        headers: this.headers(),
      })
      .pipe(map(response => response.data));
  }

  getDeploymentTargetOptions(projectId: number): Observable<DeploymentTargetOptions> {
    return this.http
      .get<ApiResponse<DeploymentTargetOptions>>(
        `/api/projects/${projectId}/deployment-target-options`,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data));
  }

  getLatestProposal(projectId: number): Observable<DeploymentProposal | null> {
    return this.http
      .get<ApiResponse<{ proposal: DeploymentProposal | null }>>(
        `/api/projects/${projectId}/deployment-proposals/latest`,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.proposal));
  }

  prepareProposal(
    projectId: number,
    payload: {
      mode: WorkflowGenerationMode;
      aiConnectionId: number | null;
      aiModel: string | null;
      decisions: DeploymentProposalDecisions;
    },
  ): Observable<DeploymentProposal> {
    return this.http
      .post<ApiResponse<{ proposal: DeploymentProposal }>>(
        `/api/projects/${projectId}/deployment-proposals`,
        payload,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.proposal));
  }

  saveProposalAnswers(
    projectId: number,
    proposalId: number,
    payload: {
      decisions: DeploymentProposalDecisions;
      answers: Record<string, string>;
    },
  ): Observable<DeploymentProposal> {
    return this.http
      .put<ApiResponse<{ proposal: DeploymentProposal }>>(
        `/api/projects/${projectId}/deployment-proposals/${proposalId}`,
        payload,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.proposal));
  }

  confirmProposal(projectId: number, proposalId: number): Observable<DeploymentProposal> {
    return this.http
      .post<ApiResponse<{ proposal: DeploymentProposal }>>(
        `/api/projects/${projectId}/deployment-proposals/${proposalId}/confirm`,
        {},
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.proposal));
  }

  getAiModels(projectId: number, connectionId: number): Observable<string[]> {
    return this.http
      .get<ApiResponse<{ models: string[] }>>(
        `/api/projects/${projectId}/workflow/ai-connections/${connectionId}/models`,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.models));
  }

  createGeneration(
    projectId: number,
    payload: {
      contractId: number;
      generationMode: WorkflowGenerationMode;
      aiConnectionId?: number | null;
      aiModel?: string | null;
    },
  ): Observable<WorkflowGeneration> {
    return this.http
      .post<ApiResponse<{ generation: WorkflowGeneration }>>(
        `/api/projects/${projectId}/workflow/generations`,
        payload,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.generation));
  }

  getGenerationEvents(
    projectId: number,
    generationId: number,
    afterId = 0,
  ): Observable<{
    events: WorkflowGenerationEvent[];
    lastEventId: number;
    generation: WorkflowGeneration;
  }> {
    const params = new HttpParams().set('afterId', String(afterId));
    return this.http
      .get<ApiResponse<{
        events: WorkflowGenerationEvent[];
        lastEventId: number;
        generation: WorkflowGeneration;
      }>>(
        `/api/projects/${projectId}/workflow/generations/${generationId}/events`,
        { headers: this.headers(), params },
      )
      .pipe(map(response => response.data));
  }

  getArtifacts(projectId: number, generationId: number): Observable<WorkflowArtifact[]> {
    return this.http
      .get<ApiResponse<{ artifacts: WorkflowArtifact[] }>>(
        `/api/projects/${projectId}/workflow/generations/${generationId}/artifacts`,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.artifacts));
  }

  getArtifact(
    projectId: number,
    generationId: number,
    artifactId: number,
  ): Observable<WorkflowArtifact> {
    return this.http
      .get<ApiResponse<{ artifact: WorkflowArtifact }>>(
        `/api/projects/${projectId}/workflow/generations/${generationId}/artifacts/${artifactId}`,
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.artifact));
  }

  updateArtifact(
    projectId: number,
    generationId: number,
    artifactId: number,
    content: string,
  ): Observable<WorkflowArtifact> {
    return this.http
      .put<ApiResponse<{ artifact: WorkflowArtifact }>>(
        `/api/projects/${projectId}/workflow/generations/${generationId}/artifacts/${artifactId}`,
        { content },
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.artifact));
  }

  reviewArtifact(
    projectId: number,
    generationId: number,
    artifactId: number,
    decision: ArtifactReviewDecision,
    comment: string | null,
  ): Observable<WorkflowArtifact> {
    return this.http
      .post<ApiResponse<{ artifact: WorkflowArtifact }>>(
        `/api/projects/${projectId}/workflow/generations/${generationId}/artifacts/${artifactId}/review`,
        { decision, comment },
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.artifact));
  }

  confirmGeneration(projectId: number, generationId: number): Observable<WorkflowGeneration> {
    return this.http
      .post<ApiResponse<{ generation: WorkflowGeneration }>>(
        `/api/projects/${projectId}/workflow/generations/${generationId}/confirm`,
        {},
        { headers: this.headers() },
      )
      .pipe(map(response => response.data.generation));
  }

  private headers(): HttpHeaders {
    const token = this.auth.getAccessToken();
    return token
      ? new HttpHeaders({ Authorization: `Bearer ${token}` })
      : new HttpHeaders();
  }
}