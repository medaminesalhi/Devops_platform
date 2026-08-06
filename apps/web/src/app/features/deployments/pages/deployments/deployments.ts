import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { finalize } from 'rxjs';

import {
  DEPLOYMENTS_DEMO_MODE,
  DeploymentStatus,
  DeploymentSummary,
  DeploymentsService,
} from '../../../../core/deployments/deployments';

interface ApiErrorResponse {
  success: false;
  error: { code: string; message: string };
}

@Component({
  selector: 'app-deployments',
  imports: [DatePipe, ReactiveFormsModule, RouterLink],
  templateUrl: './deployments.html',
  styleUrl: './deployments.scss',
})
export class Deployments implements OnInit {
  private readonly deploymentsService = inject(DeploymentsService);
  private readonly formBuilder = inject(FormBuilder);

  readonly demoMode = DEPLOYMENTS_DEMO_MODE;
  readonly deployments = signal<DeploymentSummary[]>([]);
  readonly total = signal(0);
  readonly isLoading = signal(true);
  readonly isRefreshing = signal(false);
  readonly errorMessage = signal<string | null>(null);

  readonly filterForm = this.formBuilder.nonNullable.group({
    search: '',
    status: '',
  });

  readonly runningCount = computed(() =>
    this.deployments().filter(item => item.status === 'running' || item.status === 'queued').length,
  );
  readonly waitingCount = computed(() =>
    this.deployments().filter(item => item.status === 'waiting_confirmation').length,
  );
  readonly succeededCount = computed(() =>
    this.deployments().filter(item => item.status === 'succeeded').length,
  );
  readonly failedCount = computed(() =>
    this.deployments().filter(item => item.status === 'failed').length,
  );

  ngOnInit(): void {
    this.loadDeployments();
  }

  loadDeployments(refresh = false): void {
    const filters = this.filterForm.getRawValue();
    refresh ? this.isRefreshing.set(true) : this.isLoading.set(true);
    this.errorMessage.set(null);

    this.deploymentsService
      .listDeployments({
        search: filters.search.trim() || null,
        status: this.toStatus(filters.status),
      })
      .pipe(finalize(() => {
        this.isLoading.set(false);
        this.isRefreshing.set(false);
      }))
      .subscribe({
        next: result => {
          this.deployments.set(result.deployments);
          this.total.set(result.total);
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  resetFilters(): void {
    this.filterForm.reset({ search: '', status: '' });
    this.loadDeployments();
  }

  statusLabel(status: DeploymentStatus): string {
    const labels: Record<DeploymentStatus, string> = {
      draft: 'Brouillon',
      ready: 'Prêt',
      queued: 'En file',
      running: 'En cours',
      waiting_confirmation: 'À confirmer',
      succeeded: 'Réussi',
      failed: 'Échoué',
      cancelled: 'Annulé',
    };
    return labels[status];
  }

  statusClass(status: DeploymentStatus): string {
    return `status-pill--${status.replace('_', '-')}`;
  }

  progressLabel(item: DeploymentSummary): string {
    if (item.status === 'succeeded') return 'Terminé';
    if (item.status === 'failed') return `Arrêté à ${item.progress} %`;
    if (item.status === 'waiting_confirmation') return 'Confirmation requise';
    return `${item.progress} %`;
  }

  private toStatus(value: string): DeploymentStatus | null {
    const accepted: DeploymentStatus[] = [
      'draft', 'ready', 'queued', 'running', 'waiting_confirmation',
      'succeeded', 'failed', 'cancelled',
    ];
    return accepted.includes(value as DeploymentStatus)
      ? value as DeploymentStatus
      : null;
  }

  private resolveError(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 0) return 'Le backend Flask est inaccessible.';
      const body = error.error as ApiErrorResponse | null;
      return body?.error?.message || `Erreur HTTP ${error.status}.`;
    }
    return error instanceof Error ? error.message : 'Impossible de charger les déploiements.';
  }
}