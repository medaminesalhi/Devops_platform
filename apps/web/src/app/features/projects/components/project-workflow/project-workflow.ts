import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnChanges,
  OnDestroy,
  Output,
  SimpleChanges,
  computed,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  Subscription,
  finalize,
  forkJoin,
  switchMap,
  timer,
} from 'rxjs';

import {
  ArtifactReviewDecision,
  ContractValidationReport,
  DeploymentContract,
  DeploymentContractComponent,
  DeploymentContractVariable,
  DeploymentContractVolume,
  ProjectWorkflowScreen,
  SavedDeploymentContract,
  WorkflowAiConnection,
  WorkflowArtifact,
  WorkflowEnvironment,
  WorkflowGeneration,
  WorkflowGenerationEvent,
  WorkflowGenerationMode,
  WorkflowGenerationStatus,
  WorkflowOverview,
  WorkflowService,
} from '../../../../core/workflow/workflow';

@Component({
  selector: 'app-project-workflow',
  imports: [DatePipe, FormsModule],
  templateUrl: './project-workflow.html',
  styleUrl: './project-workflow.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProjectWorkflow implements OnChanges, OnDestroy {
  private readonly workflowService = inject(WorkflowService);
  private pollingSubscription: Subscription | null = null;
  private lastEventId = 0;

  @Input({ required: true }) projectId!: number;
  @Input() screen: ProjectWorkflowScreen = 'contract';
  @Input() refreshToken = 0;

  @Output() contractConfirmedChange = new EventEmitter<boolean>();
  @Output() generationStatusChange =
    new EventEmitter<WorkflowGenerationStatus | null>();

  readonly overview = signal<WorkflowOverview | null>(null);
  readonly savedContract = signal<SavedDeploymentContract | null>(null);
  readonly contractDraft = signal<DeploymentContract | null>(null);
  readonly contractValidation = signal<ContractValidationReport | null>(null);
  readonly selectedEnvironmentId = signal<number | null>(null);
  readonly selectedComponentId = signal<number | null>(null);
  readonly contractDirty = signal(false);

  readonly generationMode = signal<WorkflowGenerationMode>('deterministic');
  readonly selectedAiConnectionId = signal<number | null>(null);
  readonly aiModels = signal<string[]>([]);
  readonly selectedAiModel = signal('');
  readonly generation = signal<WorkflowGeneration | null>(null);
  readonly generationEvents = signal<WorkflowGenerationEvent[]>([]);

  readonly artifacts = signal<WorkflowArtifact[]>([]);
  readonly selectedArtifactId = signal<number | null>(null);
  readonly selectedArtifact = signal<WorkflowArtifact | null>(null);
  readonly artifactDraft = signal('');
  readonly reviewComment = signal('');

  readonly isLoading = signal(false);
  readonly isPreviewing = signal(false);
  readonly isSavingContract = signal(false);
  readonly isConfirmingContract = signal(false);
  readonly isLoadingModels = signal(false);
  readonly isStartingGeneration = signal(false);
  readonly isLoadingArtifacts = signal(false);
  readonly isLoadingArtifact = signal(false);
  readonly isSavingArtifact = signal(false);
  readonly reviewingArtifactId = signal<number | null>(null);
  readonly isApprovingAll = signal(false);
  readonly isConfirmingGeneration = signal(false);

  readonly error = signal<string | null>(null);
  readonly success = signal<string | null>(null);

  readonly environments = computed(() => this.overview()?.environments ?? []);
  readonly aiConnections = computed(() => this.overview()?.aiConnections ?? []);
  readonly canWrite = computed(() => this.overview()?.canWrite ?? false);

  readonly selectedEnvironment = computed<WorkflowEnvironment | null>(() => {
    const id = this.selectedEnvironmentId();
    return this.environments().find(item => item.id === id) ?? null;
  });

  readonly selectedComponent = computed<DeploymentContractComponent | null>(() => {
    const contract = this.contractDraft();
    const id = this.selectedComponentId();

    if (!contract || id === null) {
      return null;
    }

    return contract.components.find(item => item.id === id) ?? null;
  });

  readonly selectedAiConnection = computed<WorkflowAiConnection | null>(() => {
    const id = this.selectedAiConnectionId();
    return this.aiConnections().find(item => item.id === id) ?? null;
  });

  readonly contractIsValid = computed(
    () => this.contractValidation()?.valid === true,
  );

  readonly contractIsConfirmed = computed(
    () => this.savedContract()?.status === 'confirmed' && !this.contractDirty(),
  );

  readonly generationRunning = computed(() => {
    const status = this.generation()?.status;
    return status === 'pending' || status === 'running';
  });

  readonly generationReviewable = computed(() => {
    const status = this.generation()?.status;
    return status === 'awaiting_review' || status === 'completed';
  });

  readonly generationConfirmed = computed(
    () => this.generation()?.status === 'confirmed',
  );

  readonly canStartGeneration = computed(() => {
    if (
      !this.canWrite()
      || !this.contractIsConfirmed()
      || this.generationRunning()
      || this.isStartingGeneration()
    ) {
      return false;
    }

    if (this.generationMode() === 'deterministic') {
      return true;
    }

    return (
      this.selectedAiConnectionId() !== null
      && this.selectedAiModel().trim().length > 0
    );
  });

  readonly invalidArtifactCount = computed(
    () => this.artifacts().filter(item => item.validationStatus === 'failed').length,
  );

  readonly approvedArtifactCount = computed(
    () => this.artifacts().filter(item => item.reviewStatus === 'approved').length,
  );

  readonly rejectedArtifactCount = computed(
    () => this.artifacts().filter(item => item.reviewStatus === 'rejected').length,
  );

  readonly pendingArtifactCount = computed(
    () => this.artifacts().filter(item => item.reviewStatus === 'pending_review').length,
  );

  readonly canConfirmGeneration = computed(() => {
    const items = this.artifacts();

    return (
      this.canWrite()
      && this.generationReviewable()
      && items.length > 0
      && this.invalidArtifactCount() === 0
      && this.rejectedArtifactCount() === 0
      && this.approvedArtifactCount() === items.length
      && !this.isConfirmingGeneration()
    );
  });

  ngOnChanges(changes: SimpleChanges): void {
    if (
      changes['projectId']
      || changes['screen']
      || changes['refreshToken']
    ) {
      if (Number.isInteger(this.projectId) && this.projectId > 0) {
        this.loadWorkflow();
      }
    }
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  loadWorkflow(): void {
    this.isLoading.set(true);
    this.error.set(null);

    this.workflowService
      .getOverview(this.projectId)
      .pipe(finalize(() => this.isLoading.set(false)))
      .subscribe({
        next: overview => {
          this.overview.set(overview);
          this.savedContract.set(overview.latestContract);
          this.generation.set(overview.latestGeneration);
          this.initializeContract(overview);
          this.initializeAi(overview);
          this.emitProgress();
          this.resumeGeneration(overview.latestGeneration);
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  selectEnvironment(value: string | number | null): void {
    const environmentId = Number(value);

    if (!Number.isInteger(environmentId) || environmentId <= 0) {
      this.selectedEnvironmentId.set(null);
      this.contractDraft.set(null);
      this.savedContract.set(null);
      this.contractValidation.set(null);
      return;
    }

    this.selectedEnvironmentId.set(environmentId);

    const existing = this.overview()?.latestContract;
    if (existing?.environmentId === environmentId) {
      this.savedContract.set(existing);
      this.contractDraft.set(this.clone(existing.contract));
      this.contractValidation.set(existing.validation);
      this.contractDirty.set(false);
      return;
    }

    this.savedContract.set(null);
    this.previewContract(environmentId);
  }

  previewContract(environmentId = this.selectedEnvironmentId()): void {
    if (environmentId === null) {
      return;
    }

    this.isPreviewing.set(true);
    this.error.set(null);
    this.success.set(null);

    this.workflowService
      .previewContract(this.projectId, environmentId)
      .pipe(finalize(() => this.isPreviewing.set(false)))
      .subscribe({
        next: result => {
          this.contractDraft.set(this.clone(result.contract));
          this.contractValidation.set(result.validation);
          this.contractDirty.set(true);
          this.selectedComponentId.set(null);
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  markContractDirty(): void {
    this.contractDirty.set(true);
    this.success.set(null);
  }

  saveContract(): void {
    const environmentId = this.selectedEnvironmentId();
    const contract = this.contractDraft();

    if (environmentId === null || !contract) {
      return;
    }

    this.isSavingContract.set(true);
    this.error.set(null);
    this.success.set(null);

    this.workflowService
      .saveContract(this.projectId, environmentId, contract)
      .pipe(finalize(() => this.isSavingContract.set(false)))
      .subscribe({
        next: saved => {
          this.savedContract.set(saved);
          this.contractDraft.set(this.clone(saved.contract));
          this.contractValidation.set(saved.validation);
          this.contractDirty.set(false);
          this.success.set(
            saved.validation.valid
              ? 'Le contrat est enregistré et prêt pour confirmation.'
              : 'Le contrat est enregistré, mais il contient encore des erreurs.',
          );
          this.emitProgress();
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  confirmContract(): void {
    const contractId = this.savedContract()?.id;

    if (!contractId) {
      return;
    }

    this.isConfirmingContract.set(true);
    this.error.set(null);
    this.success.set(null);

    this.workflowService
      .confirmContract(this.projectId, contractId)
      .pipe(finalize(() => this.isConfirmingContract.set(false)))
      .subscribe({
        next: contract => {
          this.savedContract.set(contract);
          this.contractDraft.set(this.clone(contract.contract));
          this.contractValidation.set(contract.validation);
          this.contractDirty.set(false);
          this.success.set('Le contrat de déploiement est confirmé.');
          this.emitProgress();
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  selectComponent(componentId: number): void {
    this.selectedComponentId.set(
      this.selectedComponentId() === componentId ? null : componentId,
    );
  }

  addVariable(
    componentId: number,
    kind: 'configuration' | 'secrets',
  ): void {
    const variable: DeploymentContractVariable = {
      name: '',
      required: true,
      description: '',
    };

    if (kind === 'configuration') {
      variable.value = '';
    }

    this.mutateComponent(componentId, component => {
      component[kind].push(variable);
    });
  }

  removeVariable(
    componentId: number,
    kind: 'configuration' | 'secrets',
    index: number,
  ): void {
    this.mutateComponent(componentId, component => {
      component[kind].splice(index, 1);
    });
  }

  addVolume(componentId: number): void {
    this.mutateComponent(componentId, component => {
      const volume: DeploymentContractVolume = {
        name: `data-${component.volumes.length + 1}`,
        mountPath: '/data',
        size: '1Gi',
        accessMode: 'ReadWriteOnce',
        storageClass: '',
        readOnly: false,
      };

      component.volumes.push(volume);
    });
  }

  removeVolume(componentId: number, index: number): void {
    this.mutateComponent(componentId, component => {
      component.volumes.splice(index, 1);
    });
  }

  setGenerationMode(mode: WorkflowGenerationMode): void {
    this.generationMode.set(mode);
    this.error.set(null);

    if (mode === 'hybrid' && this.selectedAiConnectionId() === null) {
      const connection = this.preferredAiConnection(this.aiConnections());
      if (connection) {
        this.selectAiConnection(connection.id);
      }
    }
  }

  selectAiConnection(value: string | number | null): void {
    const connectionId = Number(value);
    this.aiModels.set([]);
    this.selectedAiModel.set('');

    if (!Number.isInteger(connectionId) || connectionId <= 0) {
      this.selectedAiConnectionId.set(null);
      return;
    }

    this.selectedAiConnectionId.set(connectionId);
    this.loadAiModels(connectionId);
  }

  loadAiModels(connectionId = this.selectedAiConnectionId()): void {
    if (connectionId === null) {
      return;
    }

    this.isLoadingModels.set(true);
    this.error.set(null);

    this.workflowService
      .getAiModels(this.projectId, connectionId)
      .pipe(finalize(() => this.isLoadingModels.set(false)))
      .subscribe({
        next: models => {
          this.aiModels.set(models);
          if (!models.includes(this.selectedAiModel())) {
            this.selectedAiModel.set(models[0] ?? '');
          }
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  startGeneration(): void {
    const contractId = this.savedContract()?.id;
    if (!contractId) {
      return;
    }

    const mode = this.generationMode();
    this.isStartingGeneration.set(true);
    this.error.set(null);
    this.success.set(null);

    this.workflowService
      .createGeneration(this.projectId, {
        contractId,
        generationMode: mode,
        aiConnectionId:
          mode === 'hybrid' ? this.selectedAiConnectionId() : null,
        aiModel:
          mode === 'hybrid' ? this.selectedAiModel().trim() : null,
      })
      .pipe(finalize(() => this.isStartingGeneration.set(false)))
      .subscribe({
        next: generation => {
          this.generation.set(generation);
          this.generationEvents.set([]);
          this.artifacts.set([]);
          this.clearArtifact();
          this.lastEventId = 0;
          this.emitProgress();
          this.startPolling(generation.id);
          this.success.set('La génération a été ajoutée à la file du worker.');
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  refreshGeneration(): void {
    const generationId = this.generation()?.id;
    if (generationId) {
      this.fetchEvents(generationId);
    }
  }

  loadArtifacts(): void {
    const generationId = this.generation()?.id;
    if (!generationId) {
      return;
    }

    this.isLoadingArtifacts.set(true);
    this.error.set(null);

    this.workflowService
      .getArtifacts(this.projectId, generationId)
      .pipe(finalize(() => this.isLoadingArtifacts.set(false)))
      .subscribe({
        next: artifacts => {
          this.artifacts.set(artifacts);
          if (
            this.selectedArtifactId() !== null
            && !artifacts.some(item => item.id === this.selectedArtifactId())
          ) {
            this.clearArtifact();
          }
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  selectArtifact(artifactId: number): void {
    const generationId = this.generation()?.id;
    if (!generationId) {
      return;
    }

    this.selectedArtifactId.set(artifactId);
    this.isLoadingArtifact.set(true);
    this.error.set(null);

    this.workflowService
      .getArtifact(this.projectId, generationId, artifactId)
      .pipe(finalize(() => this.isLoadingArtifact.set(false)))
      .subscribe({
        next: artifact => {
          this.selectedArtifact.set(artifact);
          this.artifactDraft.set(artifact.content ?? '');
          this.reviewComment.set(artifact.reviewComment ?? '');
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  saveArtifact(): void {
    const generationId = this.generation()?.id;
    const artifactId = this.selectedArtifactId();

    if (!generationId || artifactId === null) {
      return;
    }

    this.isSavingArtifact.set(true);
    this.error.set(null);
    this.success.set(null);

    this.workflowService
      .updateArtifact(
        this.projectId,
        generationId,
        artifactId,
        this.artifactDraft(),
      )
      .pipe(finalize(() => this.isSavingArtifact.set(false)))
      .subscribe({
        next: artifact => {
          this.selectedArtifact.set(artifact);
          this.artifactDraft.set(artifact.content ?? '');
          this.replaceArtifact(artifact);
          this.success.set('Le fichier est enregistré et revalidé.');
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  reviewSelected(decision: ArtifactReviewDecision): void {
    const artifactId = this.selectedArtifactId();
    if (artifactId === null) {
      return;
    }

    this.reviewArtifact(
      artifactId,
      decision,
      this.reviewComment().trim() || null,
    );
  }

  reviewArtifact(
    artifactId: number,
    decision: ArtifactReviewDecision,
    comment: string | null = null,
  ): void {
    const generationId = this.generation()?.id;
    if (!generationId) {
      return;
    }

    this.reviewingArtifactId.set(artifactId);
    this.error.set(null);
    this.success.set(null);

    this.workflowService
      .reviewArtifact(
        this.projectId,
        generationId,
        artifactId,
        decision,
        comment,
      )
      .pipe(finalize(() => this.reviewingArtifactId.set(null)))
      .subscribe({
        next: artifact => {
          this.replaceArtifact(artifact);
          if (this.selectedArtifactId() === artifact.id) {
            this.selectedArtifact.set(artifact);
            this.reviewComment.set(artifact.reviewComment ?? '');
          }
          this.success.set(
            decision === 'approved' ? 'Le fichier est approuvé.' : 'Le fichier est rejeté.',
          );
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  approveAllValid(): void {
    const generationId = this.generation()?.id;
    if (!generationId) {
      return;
    }

    const candidates = this.artifacts().filter(
      item => item.validationStatus !== 'failed' && item.reviewStatus !== 'approved',
    );

    if (candidates.length === 0) {
      return;
    }

    this.isApprovingAll.set(true);
    this.error.set(null);

    forkJoin(
      candidates.map(item =>
        this.workflowService.reviewArtifact(
          this.projectId,
          generationId,
          item.id,
          'approved',
          null,
        ),
      ),
    )
      .pipe(finalize(() => this.isApprovingAll.set(false)))
      .subscribe({
        next: artifacts => {
          artifacts.forEach(item => this.replaceArtifact(item));
          this.success.set(`${artifacts.length} fichier(s) approuvé(s).`);
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
          this.loadArtifacts();
        },
      });
  }

  confirmGeneration(): void {
    const generationId = this.generation()?.id;
    if (!generationId) {
      return;
    }

    this.isConfirmingGeneration.set(true);
    this.error.set(null);

    this.workflowService
      .confirmGeneration(this.projectId, generationId)
      .pipe(finalize(() => this.isConfirmingGeneration.set(false)))
      .subscribe({
        next: generation => {
          this.generation.set(generation);
          this.success.set(
            'La génération est confirmée. Le projet peut passer au déploiement.',
          );
          this.emitProgress();
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  environmentState(environment: WorkflowEnvironment): string {
    const unavailable = environment.services.filter(
      service => service.required && service.status !== 'online',
    );

    if (environment.configurationStatus === 'ready' && unavailable.length === 0) {
      return 'Prêt';
    }

    return unavailable.length > 0 ? 'Services requis indisponibles' : 'À vérifier';
  }

  generationStatusLabel(status: WorkflowGenerationStatus | null | undefined): string {
    const labels: Record<string, string> = {
      pending: 'En attente',
      running: 'En cours',
      awaiting_review: 'Revue requise',
      completed: 'Terminée',
      confirmed: 'Confirmée',
      failed: 'Échec',
      cancelled: 'Annulée',
      superseded: 'Remplacée',
    };

    return labels[status ?? ''] ?? 'Non démarrée';
  }

  generationStepLabel(step: string | null | undefined): string {
    const labels: Record<string, string> = {
      queued: 'Dans la file du worker',
      loading_context: 'Chargement du contexte',
      loading_contract: 'Chargement du contrat',
      preparing_source: 'Préparation de la source',
      ai_planning: 'Planification par le modèle IA',
      rendering: 'Génération Docker, Helm et Argo CD',
      saving_artifacts: 'Enregistrement des fichiers',
      awaiting_review: 'Revue humaine requise',
      confirmed: 'Génération confirmée',
      failed: 'Génération échouée',
    };

    return labels[step ?? ''] ?? step ?? 'Non démarrée';
  }

  artifactTypeLabel(type: string): string {
    const labels: Record<string, string> = {
      dockerfile: 'Dockerfile',
      dockerignore: '.dockerignore',
      helm_chart: 'Chart Helm',
      helm_values: 'Valeurs Helm',
      helm_template: 'Template Helm',
      configmap: 'ConfigMap',
      secret_template: 'Modèle de Secret',
      migration_job: 'Job de migration',
      gitops_manifest: 'Manifest GitOps',
      argocd_project: 'AppProject Argo CD',
      argocd_application: 'Application Argo CD',
    };

    return labels[type] ?? type;
  }

  private initializeContract(overview: WorkflowOverview): void {
    const contract = overview.latestContract;

    if (contract) {
      this.selectedEnvironmentId.set(contract.environmentId);
      this.contractDraft.set(this.clone(contract.contract));
      this.contractValidation.set(contract.validation);
      this.contractDirty.set(false);
      return;
    }

    const environment =
      overview.environments.find(item => item.isDefault)
      ?? overview.environments[0]
      ?? null;

    if (environment && this.screen === 'contract') {
      this.selectedEnvironmentId.set(environment.id);
      this.previewContract(environment.id);
    }
  }

  private initializeAi(overview: WorkflowOverview): void {
    const generation = overview.latestGeneration;
    const mode = generation?.generationMode ?? 'deterministic';
    this.generationMode.set(mode);

    if (mode !== 'hybrid') {
      return;
    }

    const connection =
      overview.aiConnections.find(item => item.id === generation?.aiConnectionId)
      ?? this.preferredAiConnection(overview.aiConnections);

    if (!connection) {
      return;
    }

    this.selectedAiConnectionId.set(connection.id);
    this.selectedAiModel.set(generation?.aiModel ?? '');

    if (this.screen === 'generation') {
      this.loadAiModels(connection.id);
    }
  }

  private preferredAiConnection(
    connections: WorkflowAiConnection[],
  ): WorkflowAiConnection | null {
    return (
      connections.find(item => item.status === 'online')
      ?? connections.find(item => item.status === 'degraded')
      ?? connections[0]
      ?? null
    );
  }

  private resumeGeneration(generation: WorkflowGeneration | null): void {
    this.stopPolling();
    this.generationEvents.set([]);
    this.lastEventId = 0;

    if (!generation) {
      this.artifacts.set([]);
      return;
    }

    if (generation.status === 'pending' || generation.status === 'running') {
      if (this.screen === 'generation') {
        this.startPolling(generation.id);
      }
      return;
    }

    if (this.screen === 'generation') {
      this.fetchEvents(generation.id);
    }

    if (
      this.screen === 'review'
      && (
        generation.status === 'awaiting_review'
        || generation.status === 'completed'
        || generation.status === 'confirmed'
      )
    ) {
      this.loadArtifacts();
    }
  }

  private startPolling(generationId: number): void {
    this.stopPolling();

    this.pollingSubscription = timer(0, 2000)
      .pipe(
        switchMap(() =>
          this.workflowService.getGenerationEvents(
            this.projectId,
            generationId,
            this.lastEventId,
          ),
        ),
      )
      .subscribe({
        next: result => {
          this.generation.set(result.generation);
          this.appendEvents(result.events);
          this.lastEventId = result.lastEventId;
          this.emitProgress();

          if (
            result.generation.status !== 'pending'
            && result.generation.status !== 'running'
          ) {
            this.stopPolling();
          }
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
          this.stopPolling();
        },
      });
  }

  private fetchEvents(generationId: number): void {
    this.workflowService
      .getGenerationEvents(
        this.projectId,
        generationId,
        this.lastEventId,
      )
      .subscribe({
        next: result => {
          this.generation.set(result.generation);
          this.appendEvents(result.events);
          this.lastEventId = result.lastEventId;
          this.emitProgress();
        },
        error: (error: HttpErrorResponse) => {
          this.error.set(this.resolveError(error));
        },
      });
  }

  private appendEvents(events: WorkflowGenerationEvent[]): void {
    const indexed = new Map<number, WorkflowGenerationEvent>();
    this.generationEvents().forEach(item => indexed.set(item.id, item));
    events.forEach(item => indexed.set(item.id, item));
    this.generationEvents.set(
      [...indexed.values()].sort((left, right) => left.id - right.id),
    );
  }

  private stopPolling(): void {
    this.pollingSubscription?.unsubscribe();
    this.pollingSubscription = null;
  }

  private mutateComponent(
    componentId: number,
    mutator: (component: DeploymentContractComponent) => void,
  ): void {
    const contract = this.contractDraft();
    const component = contract?.components.find(item => item.id === componentId);

    if (!component) {
      return;
    }

    mutator(component);
    this.markContractDirty();
  }

  private replaceArtifact(artifact: WorkflowArtifact): void {
    this.artifacts.update(items =>
      items.map(item => item.id === artifact.id ? { ...item, ...artifact } : item),
    );
  }

  private clearArtifact(): void {
    this.selectedArtifactId.set(null);
    this.selectedArtifact.set(null);
    this.artifactDraft.set('');
    this.reviewComment.set('');
  }

  private emitProgress(): void {
    this.contractConfirmedChange.emit(this.contractIsConfirmed());
    this.generationStatusChange.emit(this.generation()?.status ?? null);
  }

  private clone<T>(value: T): T {
    return JSON.parse(JSON.stringify(value)) as T;
  }

  private resolveError(error: HttpErrorResponse): string {
    if (error.status === 0) {
      return 'Le backend Flask est inaccessible.';
    }

    const payload = error.error;
    if (
      typeof payload === 'object'
      && payload !== null
      && 'error' in payload
    ) {
      const nested = payload.error;
      if (
        typeof nested === 'object'
        && nested !== null
        && 'message' in nested
        && typeof nested.message === 'string'
      ) {
        return nested.message;
      }
    }

    return `Erreur HTTP ${error.status}.`;
  }
}
