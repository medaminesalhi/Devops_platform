import {
  Component,
  OnDestroy,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';

import {
  DatePipe,
} from '@angular/common';

import {
  HttpErrorResponse,
} from '@angular/common/http';

import {
  FormsModule,
} from '@angular/forms';

import {
  ActivatedRoute,
  RouterLink,
} from '@angular/router';

import {
  Subscription,
  finalize,
  switchMap,
  timer,
} from 'rxjs';

import {
  Project,
  ProjectsService,
} from '../../../../core/projects/projects';

import {
  AnalysisComponent,
  AnalysisService,
  AnalysisStatus,
  CommitPolicy,
  ProjectAnalysis,
  ProjectAnalysisEvent,
} from '../../../../core/analysis/analysis';


interface ApiErrorResponse {
  success: false;

  error: {
    code: string;
    message: string;
  };
}


@Component({
  selector: 'app-project-detail',

  imports: [
    DatePipe,
    FormsModule,
    RouterLink,
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


  private pollingSubscription:
    Subscription | null = null;

  private lastEventId = 0;


  readonly projectId =
    signal<number | null>(null);

  readonly project =
    signal<Project | null>(null);

  readonly analysis =
    signal<ProjectAnalysis | null>(null);

  readonly events =
    signal<ProjectAnalysisEvent[]>([]);


  readonly commitPolicy =
    signal<CommitPolicy>('validated');


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


  readonly projectError =
    signal<string | null>(null);

  readonly analysisError =
    signal<string | null>(null);

  readonly componentError =
    signal<string | null>(null);

  readonly confirmationError =
    signal<string | null>(null);


  readonly analysisRunning =
    computed(() => {
      const status =
        this.analysis()?.status;

      return (
        status === 'pending'
        || status === 'preparing'
        || status === 'cloning'
        || status === 'analyzing'
      );
    });


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


  ngOnInit(): void {
    const rawProjectId =
      this.route.snapshot.paramMap.get(
        'projectId',
      )
      ?? this.route.snapshot.paramMap.get(
        'id',
      );

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

    this.loadLatestAnalysis(projectId);
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
          this.isLoadingProject.set(false);
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
          this.isLoadingAnalysis.set(false);
        }),
      )
      .subscribe({
        next: analysis => {
          this.applyAnalysis(analysis);

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
            this.events.set([]);
            return;
          }

          this.analysisError.set(
            this.resolveError(error),
          );
        },
      });
  }


  selectCommitPolicy(
    policy: CommitPolicy,
  ): void {
    if (this.analysisRunning()) {
      return;
    }

    this.commitPolicy.set(policy);
  }


  startAnalysis(): void {
    const projectId =
      this.projectId();

    if (projectId === null) {
      return;
    }

    this.analysisError.set(null);
    this.confirmationError.set(null);
    this.componentError.set(null);

    this.isStartingAnalysis.set(true);

    this.events.set([]);
    this.lastEventId = 0;

    this.analysisService
      .startAnalysis(
        projectId,
        this.commitPolicy(),
      )
      .pipe(
        finalize(() => {
          this.isStartingAnalysis.set(
            false,
          );
        }),
      )
      .subscribe({
        next: analysis => {
          this.applyAnalysis(analysis);

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
      || !currentAnalysis
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
          this.isRefreshing.set(false);
        }),
      )
      .subscribe({
        next: analysis => {
          this.applyAnalysis(analysis);
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


  saveComponent(
    component: AnalysisComponent,
  ): void {
    const projectId =
      this.projectId();

    const currentAnalysis =
      this.analysis();

    if (
      projectId === null
      || !currentAnalysis
      || currentAnalysis.status
        !== 'completed'
    ) {
      return;
    }

    this.componentError.set(null);

    this.savingComponentId.set(
      component.id,
    );

    this.analysisService
      .updateComponent(
        projectId,
        currentAnalysis.id,
        component.id,
        {
          name:
            component.name.trim(),

          componentType:
            component.componentType.trim(),

          runtime:
            component.runtime?.trim()
            || null,

          framework:
            component.framework?.trim()
            || null,

          packageManager:
            component.packageManager
              ?.trim()
            || null,

          buildCommand:
            component.buildCommand
              ?.trim()
            || null,

          startCommand:
            component.startCommand
              ?.trim()
            || null,

          detectedPort:
            component.detectedPort,

          deployable:
            component.deployable,
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
                    currentComponent =>
                      currentComponent.id
                      === updatedComponent.id
                        ? updatedComponent
                        : currentComponent,
                  ),
              };
            },
          );
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
      || !currentAnalysis
      || currentAnalysis.status
        !== 'completed'
    ) {
      return;
    }

    this.isConfirming.set(true);
    this.confirmationError.set(null);

    this.analysisService
      .confirmAnalysis(
        projectId,
        currentAnalysis.id,
      )
      .pipe(
        finalize(() => {
          this.isConfirming.set(false);
        }),
      )
      .subscribe({
        next: confirmed => {
          if (!confirmed) {
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

                confirmedAt:
                  new Date().toISOString(),
              };
            },
          );

          this.loadEvents(
            projectId,
            currentAnalysis.id,
          );
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
          'Clonage du repository',

        analyzing:
          'Analyse du code',

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


  stepLabel(
    step: string,
  ): string {
    const labels:
      Record<string, string> = {
        pending:
          'En attente',

        preparing:
          'Préparation du workspace',

        cloning:
          'Clonage du repository',

        checkout_completed:
          'Commit téléchargé',

        inventory:
          'Inventaire des fichiers',

        component_detection:
          'Détection des composants',

        report_generation:
          'Génération du rapport',

        completed:
          'Analyse terminée',

        confirmed:
          'Analyse confirmée',

        failed:
          'Échec de l’analyse',
      };

    return labels[step] ?? step;
  }


  shortSha(
    commitSha: string | null,
  ): string {
    if (!commitSha) {
      return 'Non disponible';
    }

    return commitSha.slice(0, 12);
  }


  formatBytes(
    value: number | undefined,
  ): string {
    if (!value) {
      return '0 o';
    }

    if (value < 1024) {
      return `${value} o`;
    }

    if (value < 1024 * 1024) {
      return (
        `${(value / 1024).toFixed(1)} Ko`
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
            this.applyAnalysis(analysis);

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
    this.analysis.set(analysis);

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
          if (newEvents.length === 0) {
            return;
          }

          this.events.update(
            currentEvents => [
              ...currentEvents,
              ...newEvents.filter(
                newEvent =>
                  !currentEvents.some(
                    currentEvent =>
                      currentEvent.id
                      === newEvent.id,
                  ),
              ),
            ],
          );

          this.lastEventId =
            Math.max(
              ...newEvents.map(
                event => event.id,
              ),
            );
        },

        error: () => {
          // Une erreur de logs ne doit pas
          // arrêter l'analyse principale.
        },
      });
  }


  private isRunningStatus(
    status: AnalysisStatus,
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


  private resolveError(
    error: HttpErrorResponse,
  ): string {
    if (error.status === 0) {
      return (
        'Le backend Flask est inaccessible.'
      );
    }

    const response =
      error.error as
        ApiErrorResponse | null;

    return (
      response?.error?.message
      || `Erreur HTTP ${error.status}.`
    );
  }
}