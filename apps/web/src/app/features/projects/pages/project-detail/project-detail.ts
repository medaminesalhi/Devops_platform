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
import { ActivatedRoute, RouterLink } from '@angular/router';
import {
  Subscription,
  finalize,
  switchMap,
  timer,
} from 'rxjs';

import { ProjectWorkflow } from '../../components/project-workflow/project-workflow';

import {
  WorkflowGenerationStatus,
  WorkflowService,
} from '../../../../core/workflow/workflow';

import {
  Project,
  ProjectsService,
} from '../../../../core/projects/projects';

import {
  AnalysisComponent,
  AnalysisEvidence,
  AnalysisService,
  AnalysisStatus,
  ProjectAnalysis,
  ProjectAnalysisEvent,
} from '../../../../core/analysis/analysis';


interface AnalysisStepDefinition {
  key: string;
  label: string;
  description: string;
  minimumProgress: number;
}


export type AnalysisStepState =
  | 'waiting'
  | 'active'
  | 'completed'
  | 'failed';


export type ProjectPagePhase =
  | 'configuration'
  | 'analysis'
  | 'contract'
  | 'generation'
  | 'review';


interface ProjectPhaseDefinition {
  key: ProjectPagePhase;
  number: number;
  label: string;
  description: string;
}


@Component({
  selector: 'app-project-detail',

  imports: [
    FormsModule,
    RouterLink,
    ProjectWorkflow,
  ],

  templateUrl:
    './project-detail.html',

  styleUrl:
    './project-detail.scss',
})
export class ProjectDetail
  implements OnInit, OnDestroy {
  private readonly route =
    inject(ActivatedRoute);

  private readonly projectsService =
    inject(ProjectsService);

  private readonly analysisService =
    inject(AnalysisService);

  private readonly workflowService =
    inject(WorkflowService);


  private pollingSubscription:
    Subscription | null = null;

  private lastEventId = 0;


  readonly phases: ProjectPhaseDefinition[] = [
    {
      key: 'configuration',
      number: 1,
      label: 'Configuration',
      description: 'Source et environnement',
    },
    {
      key: 'analysis',
      number: 2,
      label: 'Analyse',
      description: 'Code et composants',
    },
    {
      key: 'contract',
      number: 3,
      label: 'Contrat',
      description: 'Cible de déploiement',
    },
    {
      key: 'generation',
      number: 4,
      label: 'Génération',
      description: 'Docker, Helm et Argo CD',
    },
    {
      key: 'review',
      number: 5,
      label: 'Revue',
      description: 'Validation humaine',
    },
  ];


  readonly activePhase =
    signal<ProjectPagePhase>('configuration');

  readonly workflowRefreshToken =
    signal(0);

  readonly workflowContractConfirmed =
    signal(false);

  readonly workflowGenerationStatus =
    signal<WorkflowGenerationStatus | null>(null);

  readonly workflowProgressLoading =
    signal(false);


  readonly analysisSteps:
    AnalysisStepDefinition[] = [
      {
        key: 'preparing_source',
        label: 'Préparer la source',
        description:
          'Credential et workspace temporaire',
        minimumProgress: 5,
      },
      {
        key: 'source_ready',
        label: 'Charger le code',
        description:
          'Clone Git ou extraction ZIP',
        minimumProgress: 25,
      },
      {
        key: 'inventory',
        label: 'Inventorier les fichiers',
        description:
          'Arborescence et fichiers techniques',
        minimumProgress: 45,
      },
      {
        key: 'technology_detection',
        label: 'Détecter les technologies',
        description:
          'Frameworks, runtimes et dépendances',
        minimumProgress: 65,
      },
      {
        key: 'deployment_analysis',
        label: 'Analyser le déploiement',
        description:
          'Docker, Helm, Kubernetes et Argo CD',
        minimumProgress: 82,
      },
      {
        key: 'report_generation',
        label: 'Construire le rapport',
        description:
          'Preuves, alertes et contexte IA',
        minimumProgress: 95,
      },
    ];


  readonly projectId =
    signal<number | null>(null);

  readonly project =
    signal<Project | null>(null);

  readonly analysis =
    signal<ProjectAnalysis | null>(null);

  readonly events =
    signal<ProjectAnalysisEvent[]>([]);


  readonly isLoadingProject =
    signal(true);

  readonly isLoadingAnalysis =
    signal(true);

  readonly isStartingAnalysis =
    signal(false);

  readonly isRefreshing =
    signal(false);

  readonly isConfirming =
    signal(false);

  readonly savingComponentId =
    signal<number | null>(null);

  readonly editingComponentId =
    signal<number | null>(null);

  readonly componentDraft =
    signal<AnalysisComponent | null>(null);

  readonly technicalDetailsOpen =
    signal(false);


  readonly projectError =
    signal<string | null>(null);

  readonly analysisError =
    signal<string | null>(null);

  readonly componentError =
    signal<string | null>(null);

  readonly confirmationError =
    signal<string | null>(null);


  readonly analysisRunning =
    computed(() =>
      this.isRunningStatus(
        this.analysis()?.status,
      ),
    );


  readonly analysisEditable =
    computed(
      () =>
        this.analysis()?.status
        === 'completed',
    );


  readonly analysisConfirmed =
    computed(
      () =>
        this.analysis()?.status
        === 'confirmed',
    );


  readonly phase3Unlocked =
    computed(
      () => this.analysisConfirmed(),
    );


  readonly activePhaseNumber =
    computed(
      () =>
        this.phases.find(
          phase => phase.key === this.activePhase(),
        )?.number ?? 1,
    );


  readonly activePhaseDefinition =
    computed(
      () =>
        this.phases.find(
          phase => phase.key === this.activePhase(),
        ) ?? this.phases[0],
    );


  readonly deployableComponents =
    computed(() =>
      this.analysis()
        ?.components
        .filter(
          component =>
            component.deployable,
        )
      ?? [],
    );


  readonly missingArtifactCount =
    computed(() => {
      const readiness =
        this.analysis()
          ?.summary
          .deploymentReadiness;

      return (
        readiness
          ?.missingDockerfiles
          ?.length
        ?? 0
      ) + (
        readiness
          ?.missingHelmCharts
          ?.length
        ?? 0
      );
    });


  readonly globalConfidence =
    computed(() => {
      const summaryValue =
        this.analysis()
          ?.summary
          .globalConfidence;

      if (
        typeof summaryValue
        === 'number'
      ) {
        return Math.round(
          summaryValue,
        );
      }

      const components =
        this.analysis()
          ?.components
        ?? [];

      if (components.length === 0) {
        return 0;
      }

      const total =
        components.reduce(
          (
            sum,
            component,
          ) =>
            sum
            + Number(
              component.confidence
              || 0,
            ),
          0,
        );

      return Math.round(
        total / components.length,
      );
    });


  readonly technologyCount =
    computed(() => {
      const summaryValue =
        this.analysis()
          ?.summary
          .technologyCount;

      if (
        typeof summaryValue
        === 'number'
      ) {
        return summaryValue;
      }

      const technologies =
        new Set<string>();

      for (
        const component
        of this.analysis()
          ?.components
        ?? []
      ) {
        for (
          const value of [
            component.framework,
            component.runtime,
            component.packageManager,
          ]
        ) {
          const normalized =
            value?.trim();

          if (normalized) {
            technologies.add(
              normalized.toLowerCase(),
            );
          }
        }
      }

      return technologies.size;
    });


  readonly canConfirm =
    computed(() =>
      this.analysis()?.status
        === 'completed'
      && this.deployableComponents()
        .length > 0
      && this.editingComponentId()
        === null
      && !this.isConfirming(),
    );


  ngOnInit(): void {
    const rawProjectId =
      this.route
        .snapshot
        .paramMap
        .get('projectId')
      ?? this.route
        .snapshot
        .paramMap
        .get('id');

    const projectId =
      Number(rawProjectId);

    if (
      !Number.isInteger(projectId)
      || projectId <= 0
    ) {
      this.projectError.set(
        'L’identifiant du projet est invalide.',
      );

      this.isLoadingProject.set(false);
      this.isLoadingAnalysis.set(false);

      return;
    }

    this.projectId.set(projectId);

    this.loadProject(projectId);

    this.loadLatestAnalysis(
      projectId,
    );

    this.loadWorkflowProgress(
      projectId,
    );
  }


  ngOnDestroy(): void {
    this.stopPolling();
  }


  loadProject(
    projectId: number,
  ): void {
    this.isLoadingProject.set(true);

    this.projectError.set(null);

    this.projectsService
      .getProject(projectId)
      .pipe(
        finalize(() => {
          this.isLoadingProject.set(
            false,
          );
        }),
      )
      .subscribe({
        next: project => {
          this.project.set(project);
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.projectError.set(
            this.resolveError(error),
          );
        },
      });
  }


  loadLatestAnalysis(
    projectId: number,
  ): void {
    this.isLoadingAnalysis.set(true);

    this.analysisError.set(null);

    this.analysisService
      .getLatestAnalysis(projectId)
      .pipe(
        finalize(() => {
          this.isLoadingAnalysis.set(
            false,
          );
        }),
      )
      .subscribe({
        next: analysis => {
          this.resetEvents();

          this.applyAnalysis(
            analysis,
          );

          if (
            this.isRunningStatus(
              analysis.status,
            )
          ) {
            this.startPolling(
              projectId,
              analysis.id,
            );
          }
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          if (error.status === 404) {
            this.analysis.set(null);

            this.resetEvents();

            return;
          }

          this.analysisError.set(
            this.resolveError(error),
          );
        },
      });
  }


  startAnalysis(): void {
    const projectId =
      this.projectId();

    if (
      projectId === null
      || this.analysisRunning()
      || this.isStartingAnalysis()
    ) {
      return;
    }

    this.analysisError.set(null);
    this.componentError.set(null);
    this.confirmationError.set(null);

    this.cancelComponentEdit();

    this.resetEvents();

    this.isStartingAnalysis.set(
      true,
    );

    this.analysisService
      .startAnalysis(projectId)
      .pipe(
        finalize(() => {
          this.isStartingAnalysis.set(
            false,
          );
        }),
      )
      .subscribe({
        next: analysis => {
          this.applyAnalysis(
            analysis,
          );

          this.startPolling(
            projectId,
            analysis.id,
          );
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.analysisError.set(
            this.resolveError(error),
          );
        },
      });
  }


  refreshAnalysis(): void {
    const projectId =
      this.projectId();

    const currentAnalysis =
      this.analysis();

    if (
      projectId === null
      || currentAnalysis === null
      || this.isRefreshing()
    ) {
      return;
    }

    this.isRefreshing.set(true);

    this.analysisError.set(null);

    this.analysisService
      .getAnalysis(
        projectId,
        currentAnalysis.id,
      )
      .pipe(
        finalize(() => {
          this.isRefreshing.set(
            false,
          );
        }),
      )
      .subscribe({
        next: analysis => {
          this.applyAnalysis(
            analysis,
          );

          if (
            this.isRunningStatus(
              analysis.status,
            )
          ) {
            this.startPolling(
              projectId,
              analysis.id,
            );
          }
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.analysisError.set(
            this.resolveError(error),
          );
        },
      });
  }


  startComponentEdit(
    component: AnalysisComponent,
  ): void {
    if (!this.analysisEditable()) {
      return;
    }

    this.componentError.set(null);

    this.editingComponentId.set(
      component.id,
    );

    this.componentDraft.set(
      this.cloneComponent(
        component,
      ),
    );
  }


  cancelComponentEdit(): void {
    this.editingComponentId.set(
      null,
    );

    this.componentDraft.set(
      null,
    );

    this.componentError.set(
      null,
    );
  }


  saveComponent(): void {
    const projectId =
      this.projectId();

    const currentAnalysis =
      this.analysis();

    const draft =
      this.componentDraft();

    if (
      projectId === null
      || currentAnalysis === null
      || currentAnalysis.status
        !== 'completed'
      || draft === null
    ) {
      return;
    }

    const name =
      draft.name.trim();

    if (!name) {
      this.componentError.set(
        'Le nom du composant est obligatoire.',
      );

      return;
    }

    const port =
      draft.detectedPort;

    if (
      port !== null
      && (
        !Number.isInteger(
          Number(port),
        )
        || Number(port) < 1
        || Number(port) > 65535
      )
    ) {
      this.componentError.set(
        'Le port doit être compris entre 1 et 65535.',
      );

      return;
    }

    this.componentError.set(null);

    this.savingComponentId.set(
      draft.id,
    );

    this.analysisService
      .updateComponent(
        projectId,
        currentAnalysis.id,
        draft.id,
        {
          name,

          componentType:
            draft.componentType.trim(),

          runtime:
            draft.runtime?.trim()
            || null,

          framework:
            draft.framework?.trim()
            || null,

          packageManager:
            draft.packageManager
              ?.trim()
            || null,

          buildCommand:
            draft.buildCommand
              ?.trim()
            || null,

          startCommand:
            draft.startCommand
              ?.trim()
            || null,

          detectedPort:
            port === null
              ? null
              : Number(port),

          deployable:
            draft.deployable,
        },
      )
      .pipe(
        finalize(() => {
          this.savingComponentId.set(
            null,
          );
        }),
      )
      .subscribe({
        next: updatedComponent => {
          this.analysis.update(
            analysis => {
              if (!analysis) {
                return analysis;
              }

              return {
                ...analysis,

                components:
                  analysis.components.map(
                    component =>
                      component.id
                      === updatedComponent.id
                        ? updatedComponent
                        : component,
                  ),
              };
            },
          );

          this.cancelComponentEdit();
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.componentError.set(
            this.resolveError(error),
          );
        },
      });
  }


  confirmAnalysis(): void {
    const projectId =
      this.projectId();

    const currentAnalysis =
      this.analysis();

    if (
      projectId === null
      || currentAnalysis === null
      || !this.canConfirm()
    ) {
      return;
    }

    this.isConfirming.set(true);

    this.confirmationError.set(
      null,
    );

    this.analysisService
      .confirmAnalysis(
        projectId,
        currentAnalysis.id,
      )
      .pipe(
        finalize(() => {
          this.isConfirming.set(
            false,
          );
        }),
      )
      .subscribe({
        next: confirmed => {
          if (!confirmed) {
            this.confirmationError.set(
              'Le backend n’a pas confirmé l’analyse.',
            );

            return;
          }

          this.analysis.update(
            analysis => {
              if (!analysis) {
                return analysis;
              }

              return {
                ...analysis,

                status: 'confirmed',

                currentStep:
                  'confirmed',

                progress: 100,

                confirmedAt:
                  new Date()
                    .toISOString(),
              };
            },
          );

          this.loadEvents(
            projectId,
            currentAnalysis.id,
          );

          this.activePhase.set(
            'contract',
          );

          this.loadWorkflowProgress(
            projectId,
          );

          this.scrollPageTop();
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.confirmationError.set(
            this.resolveError(error),
          );
        },
      });
  }


  loadWorkflowProgress(
    projectId = this.projectId(),
  ): void {
    if (projectId === null) {
      return;
    }

    this.workflowProgressLoading.set(true);

    this.workflowService
      .getOverview(projectId)
      .pipe(
        finalize(() => {
          this.workflowProgressLoading.set(false);
        }),
      )
      .subscribe({
        next: overview => {
          this.workflowContractConfirmed.set(
            overview.latestContract?.status
            === 'confirmed',
          );

          this.workflowGenerationStatus.set(
            overview.latestGeneration?.status
            ?? null,
          );
        },

        error: () => {
          /*
           * La progression du workflow est une aide
           * d’interface. Les erreurs détaillées restent
           * affichées dans le composant de la phase.
           */
        },
      });
  }


  phaseUnlocked(
    phase: ProjectPagePhase,
  ): boolean {
    if (
      phase === 'configuration'
      || phase === 'analysis'
    ) {
      return true;
    }

    if (phase === 'contract') {
      return this.analysisConfirmed();
    }

    if (phase === 'generation') {
      return this.workflowContractConfirmed();
    }

    return [
      'awaiting_review',
      'completed',
      'confirmed',
    ].includes(
      this.workflowGenerationStatus()
      ?? '',
    );
  }


  phaseCompleted(
    phase: ProjectPagePhase,
  ): boolean {
    if (phase === 'configuration') {
      return this.project() !== null;
    }

    if (phase === 'analysis') {
      return this.analysisConfirmed();
    }

    if (phase === 'contract') {
      return this.workflowContractConfirmed();
    }

    if (phase === 'generation') {
      return [
        'awaiting_review',
        'completed',
        'confirmed',
      ].includes(
        this.workflowGenerationStatus()
        ?? '',
      );
    }

    return this.workflowGenerationStatus()
      === 'confirmed';
  }


  openPhase(
    phase: ProjectPagePhase,
  ): void {
    if (!this.phaseUnlocked(phase)) {
      return;
    }

    this.activePhase.set(phase);
    this.scrollPageTop();
  }


  goToPreviousPhase(): void {
    const index = this.phases.findIndex(
      phase => phase.key === this.activePhase(),
    );

    if (index > 0) {
      this.openPhase(
        this.phases[index - 1].key,
      );
    }
  }


  goToNextPhase(): void {
    const index = this.phases.findIndex(
      phase => phase.key === this.activePhase(),
    );

    const nextPhase = this.phases[index + 1];

    if (
      nextPhase
      && this.phaseUnlocked(nextPhase.key)
    ) {
      this.openPhase(nextPhase.key);
    }
  }


  refreshCurrentPhase(): void {
    const projectId = this.projectId();

    if (projectId === null) {
      return;
    }

    if (this.activePhase() === 'configuration') {
      this.loadProject(projectId);
      return;
    }

    if (this.activePhase() === 'analysis') {
      this.refreshAnalysis();
      return;
    }

    this.workflowRefreshToken.update(
      value => value + 1,
    );

    this.loadWorkflowProgress(projectId);
  }


  onContractConfirmed(
    confirmed: boolean,
  ): void {
    this.workflowContractConfirmed.set(
      confirmed,
    );
  }


  onGenerationStatus(
    status: WorkflowGenerationStatus | null,
  ): void {
    this.workflowGenerationStatus.set(
      status,
    );
  }


  private scrollPageTop(): void {
    window.scrollTo({
      top: 0,
      behavior: 'smooth',
    });
  }


  toggleTechnicalDetails(): void {
    this.technicalDetailsOpen.update(
      open => !open,
    );
  }


  analysisStatusLabel(
    status: AnalysisStatus,
  ): string {
    const labels:
      Record<AnalysisStatus, string> = {
        pending:
          'En attente',

        preparing:
          'Préparation',

        cloning:
          'Chargement du code',

        analyzing:
          'Analyse en cours',

        completed:
          'Analyse terminée',

        failed:
          'Analyse échouée',

        cancelled:
          'Analyse annulée',

        confirmed:
          'Analyse confirmée',
      };

    return labels[status];
  }


  currentStepLabel(
    step: string,
  ): string {
    const labels:
      Record<string, string> = {
        pending:
          'Analyse en attente de démarrage',

        preparing:
          'Préparation de la source',

        preparing_source:
          'Préparation de la source',

        cloning:
          'Téléchargement du repository',

        checkout_completed:
          'Version du code chargée',

        source_ready:
          'Code chargé dans le workspace',

        inventory:
          'Inventaire sécurisé des fichiers',

        component_detection:
          'Détection des composants',

        technology_detection:
          'Détection des technologies',

        deployment_analysis:
          'Analyse de Docker, Helm et Kubernetes',

        report_generation:
          'Construction du rapport',

        completed:
          'Rapport disponible',

        confirmed:
          'Rapport confirmé',

        failed:
          'L’analyse a rencontré une erreur',
      };

    return labels[step] ?? step;
  }


  analysisActionLabel(): string {
    const currentAnalysis =
      this.analysis();

    if (this.isStartingAnalysis()) {
      return 'Lancement…';
    }

    if (!currentAnalysis) {
      return this.isZipSource()
        ? 'Analyser l’archive'
        : 'Lancer l’analyse du code';
    }

    if (
      currentAnalysis.status
      === 'failed'
    ) {
      return 'Réessayer l’analyse';
    }

    if (this.analysisRunning()) {
      return 'Analyse en cours';
    }

    return 'Analyser la version actuelle';
  }


  stepState(
    stepKey: string,
  ): AnalysisStepState {
    const currentAnalysis =
      this.analysis();

    if (!currentAnalysis) {
      return 'waiting';
    }

    if (
      currentAnalysis.status
        === 'completed'
      || currentAnalysis.status
        === 'confirmed'
    ) {
      return 'completed';
    }

    const stepIndex =
      this.analysisSteps
        .findIndex(
          step =>
            step.key === stepKey,
        );

    const currentIndex =
      this.currentStepIndex(
        currentAnalysis,
      );

    if (
      currentAnalysis.status
      === 'failed'
    ) {
      if (stepIndex < currentIndex) {
        return 'completed';
      }

      if (stepIndex === currentIndex) {
        return 'failed';
      }

      return 'waiting';
    }

    if (stepIndex < currentIndex) {
      return 'completed';
    }

    if (stepIndex === currentIndex) {
      return 'active';
    }

    return 'waiting';
  }


  sourceTypeLabel(): string {
    return this.isZipSource()
      ? 'Archive ZIP'
      : 'Repository Git';
  }


  sourceDisplayName(): string {
    return (
      this.analysis()
        ?.summary
        .source
        ?.displayName

      || this.project()
        ?.source
        .repositoryPath

      || this.project()
        ?.source
        .repositoryUrl

      || 'Source du projet'
    );
  }


  sourceBranchLabel(): string {
    if (this.isZipSource()) {
      return 'Archive importée';
    }

    return (
      this.project()
        ?.source
        .branch

      || this.analysis()
        ?.summary
        .source
        ?.branch

      || 'Branche non disponible'
    );
  }


  sourceVersion(): string {
    const summarySource =
      this.analysis()
        ?.summary
        .source;

    if (
      summarySource
        ?.shortVersion
    ) {
      return summarySource
        .shortVersion;
    }

    const version =
      summarySource?.version

      || this.analysis()
        ?.analyzedCommitSha

      || this.project()
        ?.source
        .lastCommitSha

      || null;

    return this.shortVersion(
      version,
    );
  }


  versionCaption(): string {
    return this.isZipSource()
      ? 'Empreinte de l’archive'
      : 'Commit analysé';
  }


  componentEvidence(
    component: AnalysisComponent,
  ): AnalysisEvidence[] {
    const rawEvidence =
      component.configuration
        ?.['evidence'];

    if (!Array.isArray(rawEvidence)) {
      return [];
    }

    return rawEvidence.filter(
      value =>
        this.isAnalysisEvidence(
          value,
        ),
    );
  }


  componentTypeLabel(
    componentType: string,
  ): string {
    const labels:
      Record<string, string> = {
        frontend:
          'Frontend',

        backend:
          'Backend',

        fullstack:
          'Fullstack',

        worker:
          'Worker',

        container:
          'Conteneur',

        unknown:
          'À confirmer',
      };

    return (
      labels[componentType]
      ?? componentType
    );
  }


  componentTechnology(
    component: AnalysisComponent,
  ): string {
    const values = [
      component.framework,
      component.runtime,
      component.packageManager,
    ]
      .map(
        value =>
          value?.trim(),
      )
      .filter(
        (
          value,
        ): value is string =>
          Boolean(value),
      );

    return values.length > 0
      ? Array.from(
          new Set(values),
        ).join(' · ')
      : 'Technologie non détectée';
  }


  displayValue(
    value:
      string
      | number
      | null
      | undefined,
  ): string {
    if (
      value === null
      || value === undefined
      || String(value).trim() === ''
    ) {
      return 'Non détecté';
    }

    return String(value);
  }


  shortVersion(
    version:
      string
      | null
      | undefined,
  ): string {
    return version
      ? version.slice(0, 12)
      : 'Non disponible';
  }


  formatBytes(
    value:
      number
      | null
      | undefined,
  ): string {
    if (!value) {
      return '0 o';
    }

    if (value < 1024) {
      return `${value} o`;
    }

    if (
      value < 1024 * 1024
    ) {
      return (
        `${(
          value / 1024
        ).toFixed(1)} Ko`
      );
    }

    return (
      `${(
        value
        / 1024
        / 1024
      ).toFixed(1)} Mo`
    );
  }


  private startPolling(
    projectId: number,
    analysisId: number,
  ): void {
    this.stopPolling();

    this.pollingSubscription =
      timer(0, 2000)
        .pipe(
          switchMap(() =>
            this.analysisService
              .getAnalysis(
                projectId,
                analysisId,
              )
          ),
        )
        .subscribe({
          next: analysis => {
            this.applyAnalysis(
              analysis,
            );

            if (
              this.isTerminalStatus(
                analysis.status,
              )
            ) {
              this.stopPolling();
            }
          },

          error: (
            error: HttpErrorResponse,
          ) => {
            this.stopPolling();

            this.analysisError.set(
              this.resolveError(error),
            );
          },
        });
  }


  private stopPolling(): void {
    this.pollingSubscription
      ?.unsubscribe();

    this.pollingSubscription = null;
  }


  private applyAnalysis(
    analysis: ProjectAnalysis,
  ): void {
    this.analysis.set(
      analysis,
    );

    const projectId =
      this.projectId();

    if (projectId !== null) {
      this.loadEvents(
        projectId,
        analysis.id,
      );
    }
  }


  private loadEvents(
    projectId: number,
    analysisId: number,
  ): void {
    this.analysisService
      .getEvents(
        projectId,
        analysisId,
        this.lastEventId,
      )
      .subscribe({
        next: newEvents => {
          if (
            newEvents.length === 0
          ) {
            return;
          }

          this.events.update(
            currentEvents => {
              const knownIds =
                new Set(
                  currentEvents.map(
                    event => event.id,
                  ),
                );

              return [
                ...currentEvents,

                ...newEvents.filter(
                  event =>
                    !knownIds.has(
                      event.id,
                    ),
                ),
              ];
            },
          );

          this.lastEventId =
            Math.max(
              this.lastEventId,

              ...newEvents.map(
                event => event.id,
              ),
            );
        },

        error: () => {
          // Une erreur du journal
          // ne bloque pas l’analyse.
        },
      });
  }


  private resetEvents(): void {
    this.events.set([]);

    this.lastEventId = 0;
  }


  private isRunningStatus(
    status:
      AnalysisStatus
      | undefined,
  ): boolean {
    return (
      status === 'pending'
      || status === 'preparing'
      || status === 'cloning'
      || status === 'analyzing'
    );
  }


  private isTerminalStatus(
    status: AnalysisStatus,
  ): boolean {
    return (
      status === 'completed'
      || status === 'failed'
      || status === 'cancelled'
      || status === 'confirmed'
    );
  }


  private currentStepIndex(
    analysis: ProjectAnalysis,
  ): number {
    const normalizedStep =
      this.normalizeStep(
        analysis.currentStep,
      );

    const exactIndex =
      this.analysisSteps
        .findIndex(
          step =>
            step.key
            === normalizedStep,
        );

    if (exactIndex >= 0) {
      return exactIndex;
    }

    let inferredIndex = 0;

    for (
      let index = 0;
      index
        < this.analysisSteps.length;
      index += 1
    ) {
      if (
        analysis.progress
        >= this.analysisSteps[
          index
        ].minimumProgress
      ) {
        inferredIndex = index;
      }
    }

    return inferredIndex;
  }


  private normalizeStep(
    step: string,
  ): string {
    const aliases:
      Record<string, string> = {
        pending:
          'preparing_source',

        preparing:
          'preparing_source',

        preparing_source:
          'preparing_source',

        cloning:
          'source_ready',

        checkout_completed:
          'source_ready',

        source_ready:
          'source_ready',

        inventory:
          'inventory',

        component_detection:
          'technology_detection',

        technology_detection:
          'technology_detection',

        deployment_analysis:
          'deployment_analysis',

        report_generation:
          'report_generation',
      };

    return aliases[step] ?? step;
  }


  private isZipSource(): boolean {
    return (
      this.analysis()
        ?.summary
        .source
        ?.type
      === 'zip'

      || String(
        this.project()
          ?.source
          .transport
        ?? '',
      ) === 'archive'
    );
  }


  private cloneComponent(
    component: AnalysisComponent,
  ): AnalysisComponent {
    return {
      ...component,

      kubernetesPaths: [
        ...(
          component.kubernetesPaths
          ?? []
        ),
      ],

      environmentVariables: (
        component.environmentVariables
        ?? []
      ).map(
        variable => ({
          ...variable,
        }),
      ),

      configuration: {
        ...(
          component.configuration
          ?? {}
        ),
      },
    };
  }


  private isAnalysisEvidence(
    value: unknown,
  ): value is AnalysisEvidence {
    if (
      typeof value !== 'object'
      || value === null
    ) {
      return false;
    }

    return (
      'file' in value
      && typeof value.file
        === 'string'

      && 'category' in value
      && typeof value.category
        === 'string'

      && 'message' in value
      && typeof value.message
        === 'string'

      && 'strength' in value
      && typeof value.strength
        === 'string'
    );
  }


  private resolveError(
    error: HttpErrorResponse,
  ): string {
    if (error.status === 0) {
      return (
        'Le backend Flask est inaccessible.'
      );
    }

    const payload =
      error.error;

    if (
      typeof payload === 'object'
      && payload !== null
      && 'error' in payload
    ) {
      const nestedError =
        payload.error;

      if (
        typeof nestedError
          === 'object'
        && nestedError !== null
        && 'message' in nestedError
        && typeof nestedError.message
          === 'string'
      ) {
        return nestedError.message;
      }
    }

    return (
      `Erreur HTTP ${error.status}.`
    );
  }
}