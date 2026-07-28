import {
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';

import {
  DatePipe,
} from '@angular/common';

import {
  RouterLink,
} from '@angular/router';

import {
  HttpErrorResponse,
} from '@angular/common/http';

import {
  finalize,
} from 'rxjs';

import {
  DashboardOverview,
  DashboardService,
} from '../../../../core/dashboard/dashboard';


@Component({
  selector: 'app-dashboard',

  imports: [
    RouterLink,
    DatePipe,
  ],

  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
})
export class Dashboard implements OnInit {
  private readonly dashboardService =
    inject(DashboardService);

  readonly overview =
    signal<DashboardOverview | null>(
      null,
    );

  readonly isLoading =
    signal(true);

  readonly errorMessage =
    signal<string | null>(null);


  ngOnInit(): void {
    this.loadOverview();
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
    if (status === 'online') {
      return 'Opérationnel';
    }

    if (status === 'not_configured') {
      return 'Non configuré';
    }

    return 'Indisponible';
  }
}