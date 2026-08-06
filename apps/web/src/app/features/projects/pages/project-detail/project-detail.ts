import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import {
  Component,
  OnDestroy,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import {
  Subscription,
  catchError,
  combineLatest,
  finalize,
  forkJoin,
  of,
  switchMap,
  timer,
} from 'rxjs';

import {
  AnalysisComponent,
  AnalysisService,
  ProjectAnalysis,
  ProjectAnalysisEvent,
} from '../../../../core/analysis/analysis';
import {
  Project,
  ProjectsService,
  ReplaceProjectCredentialRequest,
} from '../../../../core/projects/projects';
import {
  ArtifactReviewDecision,
  DeploymentProposal,
  DeploymentProposalDecisions,
  WorkflowArtifact,
  WorkflowGeneration,
  WorkflowGenerationEvent,
  WorkflowGenerationMode,
  WorkflowOverview,
  WorkflowService,
} from '../../../../core/workflow/workflow';
import {
  ProjectPhaseItem,
  ProjectPhaseKey,
  ProjectPhaseStepper,
} from '../../components/project-phase-stepper/project-phase-stepper';

interface ApiErrorResponse {
  success: false;
  error: { code: string; message: string };
}

type RoutePhase = ProjectPhaseKey | 'auto';
type GenerationTab = 'progress' | 'artifacts' | 'validation';

@Component({
  selector: 'app-project-detail',
  imports: [DatePipe, FormsModule, RouterLink, ProjectPhaseStepper],
  templateUrl: './project-detail.html',
  styleUrl: './project-detail.scss',
})
export class ProjectDetail implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly projectsService = inject(ProjectsService);
  private readonly analysisService = inject(AnalysisService);
  private readonly workflowService = inject(WorkflowService);

  private routeSubscription: Subscription | null = null;
  private analysisPolling: Subscription | null = null;
  private generationPolling: Subscription | null = null;
  private lastAnalysisEventId = 0;
  private lastGenerationEventId = 0;

  readonly projectId = signal<number | null>(null);
  readonly activePhase = signal<ProjectPhaseKey>('configuration');
  readonly project = signal<Project | null>(null);
  readonly analysis = signal<ProjectAnalysis | null>(null);
  readonly workflow = signal<WorkflowOverview | null>(null);
  readonly proposal = signal<DeploymentProposal | null>(null);
  readonly generation = signal<WorkflowGeneration | null>(null);
  readonly analysisEvents = signal<ProjectAnalysisEvent[]>([]);
  readonly generationEvents = signal<WorkflowGenerationEvent[]>([]);
  readonly artifacts = signal<WorkflowArtifact[]>([]);
  readonly selectedArtifact = signal<WorkflowArtifact | null>(null);

  readonly isLoading = signal(true);
  readonly isRefreshing = signal(false);
  readonly isWorking = signal(false);
  readonly pageError = signal<string | null>(null);
  readonly actionError = signal<string | null>(null);
  readonly successMessage = signal<string | null>(null);

  readonly editingComponentId = signal<number | null>(null);
  readonly componentDraft = signal<AnalysisComponent | null>(null);

  readonly proposalMode = signal<WorkflowGenerationMode>('hybrid');
  readonly selectedAiConnectionId = signal<number | null>(null);
  readonly aiModels = signal<string[]>([]);
  readonly selectedAiModel = signal('');
  readonly proposalDecisions = signal<DeploymentProposalDecisions>({
    namespace: '',
    exposureMode: 'internal',
    domain: null,
    replicas: 1,
    persistence: 'suggested',
    migration: 'automatic',
  });
  readonly proposalAnswers = signal<Partial<Record<string, string>>>({});
  readonly showAdvancedProposal = signal(false);

  readonly generationMode = signal<WorkflowGenerationMode>('deterministic');
  readonly generationTab = signal<GenerationTab>('progress');
  readonly artifactDraft = signal('');
  readonly artifactReviewComment = signal('');

  readonly credentialEditorOpen = signal(false);
  readonly credentialDraft = signal<ReplaceProjectCredentialRequest>({
    credentialSource: 'project',
    authMethod: 'https_token',
    tokenType: 'project_access_token',
    username: 'oauth2',
    secret: null,
  });

  readonly analysisConfirmed = computed(() => this.analysis()?.status === 'confirmed');
  readonly proposalConfirmed = computed(() => this.proposal()?.status === 'confirmed');
  readonly generationConfirmed = computed(() => this.generation()?.status === 'confirmed');
  readonly analysisRunning = computed(() => {
    const status = this.analysis()?.status;
    return status === 'pending' || status === 'preparing' || status === 'cloning' || status === 'analyzing';
  });
  readonly generationRunning = computed(() => {
    const status = this.generation()?.status;
    return status === 'pending' || status === 'running';
  });
  readonly generationReviewReady = computed(() => {
    const status = this.generation()?.status;
    return status === 'awaiting_review' || status === 'completed' || status === 'confirmed';
  });
  readonly selectedEnvironment = computed(() => {
    const environmentId = this.project()?.defaultEnvironment?.id;
    return this.workflow()?.environments.find(item => item.id === environmentId) ?? null;
  });
  readonly selectedAiConnection = computed(() => {
    const id = this.selectedAiConnectionId();
    return this.workflow()?.aiConnections.find(connection => connection.id === id) ?? null;
  });
  readonly approvedCount = computed(
    () => this.artifacts().filter(item => item.reviewStatus === 'approved').length,
  );
  readonly invalidCount = computed(
    () => this.artifacts().filter(item => item.validationStatus === 'failed').length,
  );
  readonly rejectedCount = computed(
    () => this.artifacts().filter(item => item.reviewStatus === 'rejected').length,
  );
  readonly pendingReviewCount = computed(
    () => this.artifacts().filter(item => item.reviewStatus === 'pending_review').length,
  );
  readonly canConfirmGeneration = computed(() => {
    const list = this.artifacts();
    return list.length > 0
      && this.invalidCount() === 0
      && this.rejectedCount() === 0
      && this.approvedCount() === list.length
      && this.generation()?.status === 'awaiting_review';
  });

  readonly phases = computed<ProjectPhaseItem[]>(() => {
    const id = this.projectId();
    const base = id ? `/projects/${id}` : '/projects';
    return [
      {
        key: 'configuration', number: 1, label: 'Configuration',
        description: 'Projet, source et environnement', path: `${base}/configuration`,
        completed: this.project()?.status === 'active', unlocked: true,
      },
      {
        key: 'analysis', number: 2, label: 'Analyse',
        description: 'Comprendre le code', path: `${base}/analysis`,
        completed: this.analysisConfirmed(), unlocked: this.project()?.status === 'active',
      },
      {
        key: 'proposal', number: 3, label: 'Proposition',
        description: 'Stratégie de déploiement', path: `${base}/proposal`,
        completed: this.proposalConfirmed(), unlocked: this.analysisConfirmed(),
      },
      {
        key: 'generation', number: 4, label: 'Génération',
        description: 'Fichiers et validation', path: `${base}/generation`,
        completed: this.generationConfirmed(), unlocked: this.proposalConfirmed(),
      },
      {
        key: 'deployment', number: 5, label: 'Déploiement',
        description: 'Build, GitOps et Kubernetes', path: `${base}/deployment`,
        completed: false, unlocked: this.generationConfirmed(),
      },
    ];
  });

  ngOnInit(): void {
    this.routeSubscription = combineLatest([this.route.paramMap, this.route.data])
      .subscribe(([params, data]) => {
        const id = Number(params.get('projectId'));
        if (!Number.isInteger(id) || id <= 0) {
          this.pageError.set('Identifiant de projet invalide.');
          return;
        }
        this.projectId.set(id);
        const phase = (data['phase'] ?? 'auto') as RoutePhase;
        this.loadContext(phase);
      });
  }

  ngOnDestroy(): void {
    this.routeSubscription?.unsubscribe();
    this.stopAnalysisPolling();
    this.stopGenerationPolling();
  }

  refresh(): void {
    this.loadContext(this.activePhase(), true);
  }

  startAnalysis(): void {
    const id = this.projectId();
    if (!id) return;

    this.isWorking.set(true);
    this.clearMessages();
    this.analysisService.startAnalysis(id)
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: analysis => {
          this.analysis.set(analysis);
          this.analysisEvents.set([]);
          this.lastAnalysisEventId = 0;
          this.startAnalysisPolling(analysis.id);
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
  }

  editComponent(component: AnalysisComponent): void {
    this.editingComponentId.set(component.id);
    this.componentDraft.set(JSON.parse(JSON.stringify(component)) as AnalysisComponent);
  }

  cancelComponentEdit(): void {
    this.editingComponentId.set(null);
    this.componentDraft.set(null);
  }

  saveComponent(): void {
    const projectId = this.projectId();
    const analysisId = this.analysis()?.id;
    const draft = this.componentDraft();
    if (!projectId || !analysisId || !draft) return;

    this.isWorking.set(true);
    this.analysisService.updateComponent(projectId, analysisId, draft.id, {
      name: draft.name,
      componentType: draft.componentType,
      runtime: draft.runtime,
      framework: draft.framework,
      packageManager: draft.packageManager,
      buildCommand: draft.buildCommand,
      startCommand: draft.startCommand,
      detectedPort: draft.detectedPort,
      deployable: draft.deployable,
    })
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: updated => {
          const current = this.analysis();
          if (current) {
            this.analysis.set({
              ...current,
              components: current.components.map(item => item.id === updated.id ? updated : item),
            });
          }
          this.cancelComponentEdit();
          this.successMessage.set('Le composant a été mis à jour.');
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
  }

  confirmAnalysis(): void {
    const projectId = this.projectId();
    const analysisId = this.analysis()?.id;
    if (!projectId || !analysisId) return;

    this.isWorking.set(true);
    this.analysisService.confirmAnalysis(projectId, analysisId)
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: () => {
          const current = this.analysis();
          if (current) this.analysis.set({ ...current, status: 'confirmed' });
          this.successMessage.set('Analyse confirmée. La proposition de déploiement est maintenant disponible.');
          void this.router.navigate(['/projects', projectId, 'proposal']);
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
  }

  updateProposalDecision<K extends keyof DeploymentProposalDecisions>(
    key: K,
    value: DeploymentProposalDecisions[K],
  ): void {
    this.proposalDecisions.update(current => ({ ...current, [key]: value }));
  }

  selectAiConnection(value: number | string | null): void {
    const id = Number(value);
    this.selectedAiConnectionId.set(Number.isInteger(id) && id > 0 ? id : null);
    this.aiModels.set([]);
    this.selectedAiModel.set('');
    if (Number.isInteger(id) && id > 0) {
      const projectId = this.projectId();
      if (!projectId) return;
      this.workflowService.getAiModels(projectId, id).subscribe({
        next: models => {
          this.aiModels.set(models);
          this.selectedAiModel.set(models[0] ?? '');
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
    }
  }

  prepareProposal(): void {
    const projectId = this.projectId();
    if (!projectId) return;

    if (this.proposalMode() === 'hybrid'
      && (!this.selectedAiConnectionId() || !this.selectedAiModel().trim())) {
      this.actionError.set('Sélectionnez une connexion et un modèle IA.');
      return;
    }

    this.isWorking.set(true);
    this.clearMessages();
    this.workflowService.prepareProposal(projectId, {
      mode: this.proposalMode(),
      aiConnectionId: this.proposalMode() === 'hybrid' ? this.selectedAiConnectionId() : null,
      aiModel: this.proposalMode() === 'hybrid' ? this.selectedAiModel().trim() : null,
      decisions: this.proposalDecisions(),
    })
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: proposal => {
          this.proposal.set(proposal);
          this.proposalDecisions.set(proposal.decisions);
          this.successMessage.set('La proposition de déploiement a été préparée.');
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
  }

  setProposalAnswer(questionId: string, answer: string): void {
    this.proposalAnswers.update(current => ({ ...current, [questionId]: answer }));
  }

  saveProposalAnswers(): void {
    const projectId = this.projectId();
    const proposalId = this.proposal()?.id;
    if (!projectId || !proposalId) return;

    this.isWorking.set(true);
    const answers = Object.fromEntries(
      Object.entries(this.proposalAnswers())
        .filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
    );

    this.workflowService.saveProposalAnswers(projectId, proposalId, {
      decisions: this.proposalDecisions(),
      answers,
    })
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: proposal => {
          this.proposal.set(proposal);
          this.successMessage.set('Les décisions ont été enregistrées et la proposition a été revalidée.');
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
  }

  confirmProposal(): void {
    const projectId = this.projectId();
    const proposalId = this.proposal()?.id;
    if (!projectId || !proposalId) return;

    this.isWorking.set(true);
    this.workflowService.confirmProposal(projectId, proposalId)
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: proposal => {
          this.proposal.set(proposal);
          this.successMessage.set('Proposition confirmée. Les fichiers peuvent maintenant être générés.');
          void this.router.navigate(['/projects', projectId, 'generation']);
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
  }

  startGeneration(): void {
    const projectId = this.projectId();
    const proposal = this.proposal();
    const contractId = proposal?.contractId ?? this.workflow()?.latestContract?.id;
    if (!projectId || !contractId) {
      this.actionError.set('Aucun contrat interne confirmé n’est disponible.');
      return;
    }

    this.isWorking.set(true);
    this.clearMessages();
    this.workflowService.createGeneration(projectId, {
      contractId,
      generationMode: this.generationMode(),
      aiConnectionId: this.generationMode() === 'hybrid' ? this.selectedAiConnectionId() : null,
      aiModel: this.generationMode() === 'hybrid' ? this.selectedAiModel().trim() : null,
    })
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: generation => {
          this.generation.set(generation);
          this.generationEvents.set([]);
          this.artifacts.set([]);
          this.lastGenerationEventId = 0;
          this.generationTab.set('progress');
          this.startGenerationPolling(generation.id);
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
  }

  setGenerationTab(tab: GenerationTab): void {
    this.generationTab.set(tab);
    if ((tab === 'artifacts' || tab === 'validation') && this.generationReviewReady()) {
      this.loadArtifacts();
    }
  }

  loadArtifacts(): void {
    const projectId = this.projectId();
    const generationId = this.generation()?.id;
    if (!projectId || !generationId) return;

    this.workflowService.getArtifacts(projectId, generationId).subscribe({
      next: artifacts => this.artifacts.set(artifacts),
      error: error => this.actionError.set(this.resolveError(error)),
    });
  }

  selectArtifact(artifactId: number): void {
    const projectId = this.projectId();
    const generationId = this.generation()?.id;
    if (!projectId || !generationId) return;

    this.workflowService.getArtifact(projectId, generationId, artifactId).subscribe({
      next: artifact => {
        this.selectedArtifact.set(artifact);
        this.artifactDraft.set(artifact.content ?? '');
        this.artifactReviewComment.set(artifact.reviewComment ?? '');
      },
      error: error => this.actionError.set(this.resolveError(error)),
    });
  }

  saveArtifact(): void {
    const projectId = this.projectId();
    const generationId = this.generation()?.id;
    const artifact = this.selectedArtifact();
    if (!projectId || !generationId || !artifact) return;

    this.isWorking.set(true);
    this.workflowService.updateArtifact(
      projectId,
      generationId,
      artifact.id,
      this.artifactDraft(),
    )
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: updated => {
          this.replaceArtifact(updated);
          this.selectedArtifact.set(updated);
          this.successMessage.set('Fichier enregistré et revalidé.');
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
  }

  reviewArtifact(decision: ArtifactReviewDecision): void {
    const projectId = this.projectId();
    const generationId = this.generation()?.id;
    const artifact = this.selectedArtifact();
    if (!projectId || !generationId || !artifact) return;

    this.isWorking.set(true);
    this.workflowService.reviewArtifact(
      projectId,
      generationId,
      artifact.id,
      decision,
      this.artifactReviewComment().trim() || null,
    )
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: updated => {
          this.replaceArtifact(updated);
          this.selectedArtifact.set(updated);
          this.successMessage.set(decision === 'approved' ? 'Fichier approuvé.' : 'Fichier rejeté.');
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
  }

  confirmGeneration(): void {
    const projectId = this.projectId();
    const generationId = this.generation()?.id;
    if (!projectId || !generationId) return;

    this.isWorking.set(true);
    this.workflowService.confirmGeneration(projectId, generationId)
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: generation => {
          this.generation.set(generation);
          this.successMessage.set('Artefacts confirmés. La phase Déploiement est déverrouillée.');
          void this.router.navigate(['/projects', projectId, 'deployment']);
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
  }

  openCredentialEditor(): void {
    const source = this.project()?.source;
    this.credentialDraft.set({
      credentialSource: source?.credentialSource ?? 'project',
      authMethod: source?.authMethod ?? 'https_token',
      tokenType: source?.tokenType ?? 'project_access_token',
      username: source?.username ?? 'oauth2',
      secret: null,
    });
    this.credentialEditorOpen.set(true);
  }

  updateCredentialField<K extends keyof ReplaceProjectCredentialRequest>(
    key: K,
    value: ReplaceProjectCredentialRequest[K],
  ): void {
    this.credentialDraft.update(current => ({ ...current, [key]: value }));
  }

  saveCredential(): void {
    const projectId = this.projectId();
    const draft = this.credentialDraft();
    if (!projectId || !draft.secret?.trim()) {
      this.actionError.set('Saisissez le nouveau secret.');
      return;
    }

    this.isWorking.set(true);
    this.projectsService.replaceCredential(projectId, {
      ...draft,
      secret: draft.secret.trim(),
    })
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: project => {
          this.project.set(project);
          this.credentialEditorOpen.set(false);
          this.successMessage.set('Credential remplacé et chiffré.');
        },
        error: error => this.actionError.set(this.resolveError(error)),
      });
  }

  analysisStatusLabel(): string {
    const status = this.analysis()?.status;
    const labels: Record<string, string> = {
      pending: 'En attente', preparing: 'Préparation', cloning: 'Chargement du code',
      analyzing: 'Analyse en cours', completed: 'Analyse terminée', confirmed: 'Analyse confirmée',
      failed: 'Échec', cancelled: 'Annulée',
    };
    return labels[status ?? ''] ?? 'Non démarrée';
  }

  generationStatusLabel(): string {
    const status = this.generation()?.status;
    const labels: Record<string, string> = {
      pending: 'En attente du worker', running: 'Génération en cours',
      awaiting_review: 'Validation humaine requise', completed: 'Génération terminée',
      confirmed: 'Artefacts confirmés', failed: 'Échec', cancelled: 'Annulée', superseded: 'Remplacée',
    };
    return labels[status ?? ''] ?? 'Non démarrée';
  }

  shortVersion(value: string | null | undefined): string {
    return value ? value.slice(0, 12) : '—';
  }

  artifactTypeLabel(value: string): string {
    const labels: Record<string, string> = {
      dockerfile: 'Dockerfile', dockerignore: '.dockerignore', helm_chart: 'Chart Helm',
      helm_values: 'Valeurs Helm', helm_template: 'Template Helm', configmap: 'ConfigMap',
      secret_template: 'Modèle de Secret', migration_job: 'Job de migration',
      gitops_manifest: 'Manifest GitOps', argocd_project: 'AppProject Argo CD',
      argocd_application: 'Application Argo CD',
    };
    return labels[value] ?? value;
  }

  private loadContext(phase: RoutePhase, refresh = false): void {
    const projectId = this.projectId();
    if (!projectId) return;

    refresh ? this.isRefreshing.set(true) : this.isLoading.set(true);
    this.clearMessages();

    forkJoin({
      project: this.projectsService.getProject(projectId),
      analysis: this.analysisService.getLatestAnalysis(projectId).pipe(catchError(() => of(null))),
      workflow: this.workflowService.getOverview(projectId).pipe(catchError(() => of(null))),
      proposal: this.workflowService.getLatestProposal(projectId).pipe(catchError(() => of(null))),
    })
      .pipe(finalize(() => {
        this.isLoading.set(false);
        this.isRefreshing.set(false);
      }))
      .subscribe({
        next: context => {
          this.project.set(context.project);
          this.analysis.set(context.analysis);
          this.workflow.set(context.workflow);
          this.proposal.set(context.proposal ?? context.workflow?.latestProposal ?? null);
          this.generation.set(context.workflow?.latestGeneration ?? null);
          this.initializeSelections();
          this.resumeWorkers();

          if (phase === 'auto') {
            const target = this.firstIncompletePhase();
            void this.router.navigate(['/projects', projectId, target], { replaceUrl: true });
          } else {
            this.activePhase.set(phase);
          }
        },
        error: error => this.pageError.set(this.resolveError(error)),
      });
  }

  private initializeSelections(): void {
    const environment = this.selectedEnvironment();
    const currentProposal = this.proposal();
    this.proposalDecisions.set(currentProposal?.decisions ?? {
      namespace: environment?.namespace ?? this.project()?.defaultEnvironment?.namespace ?? '',
      exposureMode: environment?.domain ? 'public' : 'internal',
      domain: environment?.domain ?? null,
      replicas: 1,
      persistence: 'suggested',
      migration: 'automatic',
    });

    const preferredAi = this.workflow()?.aiConnections.find(item => item.status === 'online')
      ?? this.workflow()?.aiConnections[0]
      ?? null;
    if (preferredAi && this.selectedAiConnectionId() === null) {
      this.selectedAiConnectionId.set(preferredAi.id);
      this.selectAiConnection(preferredAi.id);
    }
  }

  private firstIncompletePhase(): ProjectPhaseKey {
    if (this.project()?.status !== 'active') return 'configuration';
    if (!this.analysisConfirmed()) return 'analysis';
    if (!this.proposalConfirmed()) return 'proposal';
    if (!this.generationConfirmed()) return 'generation';
    return 'deployment';
  }

  private resumeWorkers(): void {
    this.stopAnalysisPolling();
    this.stopGenerationPolling();
    if (this.analysisRunning() && this.analysis()?.id) {
      this.startAnalysisPolling(this.analysis()!.id);
    }
    if (this.generationRunning() && this.generation()?.id) {
      this.startGenerationPolling(this.generation()!.id);
    } else if (this.generationReviewReady()) {
      this.loadArtifacts();
    }
  }

  private startAnalysisPolling(analysisId: number): void {
    const projectId = this.projectId();
    if (!projectId) return;
    this.stopAnalysisPolling();
    this.analysisPolling = timer(0, 1800)
      .pipe(switchMap(() => forkJoin({
        analysis: this.analysisService.getAnalysis(projectId, analysisId),
        events: this.analysisService.getEvents(projectId, analysisId, this.lastAnalysisEventId),
      })))
      .subscribe({
        next: result => {
          this.analysis.set(result.analysis);
          this.appendAnalysisEvents(result.events);
          if (!this.analysisRunning()) this.stopAnalysisPolling();
        },
        error: error => {
          this.actionError.set(this.resolveError(error));
          this.stopAnalysisPolling();
        },
      });
  }

  private startGenerationPolling(generationId: number): void {
    const projectId = this.projectId();
    if (!projectId) return;
    this.stopGenerationPolling();
    this.generationPolling = timer(0, 1800)
      .pipe(switchMap(() => this.workflowService.getGenerationEvents(
        projectId,
        generationId,
        this.lastGenerationEventId,
      )))
      .subscribe({
        next: result => {
          this.generation.set(result.generation);
          this.lastGenerationEventId = result.lastEventId;
          this.appendGenerationEvents(result.events);
          if (!this.generationRunning()) {
            this.stopGenerationPolling();
            if (this.generationReviewReady()) {
              this.loadArtifacts();
              this.generationTab.set('artifacts');
            }
          }
        },
        error: error => {
          this.actionError.set(this.resolveError(error));
          this.stopGenerationPolling();
        },
      });
  }

  private appendAnalysisEvents(events: ProjectAnalysisEvent[]): void {
    if (!events.length) return;
    const map = new Map(this.analysisEvents().map(item => [item.id, item]));
    events.forEach(item => map.set(item.id, item));
    this.analysisEvents.set([...map.values()].sort((a, b) => a.id - b.id));
    this.lastAnalysisEventId = Math.max(this.lastAnalysisEventId, ...events.map(item => item.id));
  }

  private appendGenerationEvents(events: WorkflowGenerationEvent[]): void {
    if (!events.length) return;
    const map = new Map(this.generationEvents().map(item => [item.id, item]));
    events.forEach(item => map.set(item.id, item));
    this.generationEvents.set([...map.values()].sort((a, b) => a.id - b.id));
  }

  private replaceArtifact(artifact: WorkflowArtifact): void {
    this.artifacts.update(items => items.map(item => item.id === artifact.id ? artifact : item));
  }

  private stopAnalysisPolling(): void {
    this.analysisPolling?.unsubscribe();
    this.analysisPolling = null;
  }

  private stopGenerationPolling(): void {
    this.generationPolling?.unsubscribe();
    this.generationPolling = null;
  }

  private clearMessages(): void {
    this.pageError.set(null);
    this.actionError.set(null);
    this.successMessage.set(null);
  }

  private resolveError(error: HttpErrorResponse): string {
    if (error.status === 0) return 'Le backend Flask est inaccessible.';
    const response = error.error as ApiErrorResponse | null;
    return response?.error?.message || `Erreur HTTP ${error.status}.`;
  }
}