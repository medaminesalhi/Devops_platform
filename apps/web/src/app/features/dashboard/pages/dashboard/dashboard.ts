import {
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';

import {
  DatePipe,
  DecimalPipe,
} from '@angular/common';

import {
  RouterLink,
} from '@angular/router';

import {
  HttpErrorResponse,
} from '@angular/common/http';

import {
  finalize,
  forkJoin,
} from 'rxjs';

import {
  DashboardOverview,
  DashboardService,
} from '../../../../core/dashboard/dashboard';

import {
  PerformanceOverview,
  PerformanceRunStatus,
  PerformanceRunSummary,
  PerformanceService,
  PerformanceTestType,
} from '../../../../core/performance/performance';


@Component({
  selector: 'app-dashboard',

  imports: [
    RouterLink,
    DatePipe,
    DecimalPipe,
  ],

  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
})
export class Dashboard implements OnInit {
  private readonly dashboardService =
    inject(DashboardService);

  private readonly performanceService =
    inject(PerformanceService);

  readonly overview =
    signal<DashboardOverview | null>(
      null,
    );

  readonly isLoading =
    signal(true);

  readonly errorMessage =
    signal<string | null>(null);

  readonly performanceOverview =
    signal<PerformanceOverview | null>(
      null,
    );

  readonly recentPerformanceRuns =
    signal<PerformanceRunSummary[]>([]);

  readonly isPerformanceLoading =
    signal(true);

  readonly performanceErrorMessage =
    signal<string | null>(null);


  ngOnInit(): void {
    this.loadOverview();
    this.loadPerformanceActivity();
  }


  loadOverview(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.dashboardService
      .getOverview()
      .pipe(
        finalize(() => {
          this.isLoading.set(false);
        }),
      )
      .subscribe({
        next: (data) => {
          this.overview.set(data);
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          if (error.status === 401) {
            this.errorMessage.set(
              'Votre session est invalide ou expirée.',
            );

            return;
          }

          if (error.status === 0) {
            this.errorMessage.set(
              'Le backend Flask est inaccessible.',
            );

            return;
          }

          this.errorMessage.set(
            'Impossible de charger la vue générale.',
          );
        },
      });
  }


  loadPerformanceActivity(): void {
    this.isPerformanceLoading.set(true);
    this.performanceErrorMessage.set(null);

    forkJoin({
      overview: this.performanceService.getOverview(),
      runs: this.performanceService.listRuns(),
    })
      .pipe(
        finalize(() => {
          this.isPerformanceLoading.set(false);
        }),
      )
      .subscribe({
        next: ({ overview, runs }) => {
          this.performanceOverview.set(overview);
          this.recentPerformanceRuns.set(
            runs.slice(0, 5),
          );
        },

        error: (error: HttpErrorResponse) => {
          if (error.status === 0) {
            this.performanceErrorMessage.set(
              'Le service Performance est inaccessible.',
            );

            return;
          }

          this.performanceErrorMessage.set(
            'Impossible de charger l’activité Performance.',
          );
        },
      });
  }


  deploymentStatusLabel(
    status: string,
  ): string {
    const labels:
      Record<string, string> = {
        queued: 'En attente',
        running: 'En cours',
        succeeded: 'Réussi',
        failed: 'Échec',
        cancelled: 'Annulé',
      };

    return labels[status] ?? status;
  }


  performanceStatusLabel(
    status: PerformanceRunStatus,
  ): string {
    const labels:
      Record<PerformanceRunStatus, string> = {
        queued: 'En attente',
        running: 'En cours',
        passed: 'Réussi',
        failed: 'Échoué',
        cancelled: 'Annulé',
      };

    return labels[status];
  }


  performanceTypeLabel(
    type: PerformanceTestType,
  ): string {
    const labels:
      Record<PerformanceTestType, string> = {
        smoke: 'Smoke',
        load: 'Load',
        stress: 'Stress',
        spike: 'Spike',
        soak: 'Soak',
        custom: 'Custom',
      };

    return labels[type];
  }


  projectStatusLabel(
    status: string,
  ): string {
    const labels:
      Record<string, string> = {
        draft: 'Brouillon',
        active: 'Actif',
        paused: 'En pause',
        error: 'En erreur',
        archived: 'Archivé',
      };

    return labels[status] ?? status;
  }


  serviceStatusLabel(
    status: string,
  ): string {
    const labels: Record<string, string> = {
      online: 'Connecté',
      degraded: 'Dégradé',
      offline: 'Indisponible',
      unchecked: 'À vérifier',
      not_configured: 'Non configuré',
    };

    return labels[status] ?? 'Inconnu';
  }


  serviceIndicatorClass(
    status: string,
  ): string {
    return `service-indicator service-indicator--${status}`;
  }
}
