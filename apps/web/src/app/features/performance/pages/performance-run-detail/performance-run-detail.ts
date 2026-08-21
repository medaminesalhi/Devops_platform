import { DatePipe, DecimalPipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { finalize } from 'rxjs';

import {
  PERFORMANCE_DEMO_MODE,
  PerformanceMode,
  PerformanceRun,
  PerformanceRunStatus,
  PerformanceService,
  PerformanceTestType,
} from '../../../../core/performance/performance';

@Component({
  selector: 'app-performance-run-detail',
  imports: [DatePipe, DecimalPipe, RouterLink],
  templateUrl: './performance-run-detail.html',
  styleUrl: './performance-run-detail.scss',
})
export class PerformanceRunDetail implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly performanceService = inject(PerformanceService);

  readonly demoMode = PERFORMANCE_DEMO_MODE;
  readonly run = signal<PerformanceRun | null>(null);
  readonly isLoading = signal(true);
  readonly isWorking = signal(false);
  readonly errorMessage = signal<string | null>(null);

  readonly canCancel = computed(() => this.run()?.status === 'queued' || this.run()?.status === 'running');

  ngOnInit(): void {
    const runId = Number(this.route.snapshot.paramMap.get('runId'));
    if (!Number.isInteger(runId) || runId <= 0) {
      this.errorMessage.set('Identifiant de run invalide.');
      this.isLoading.set(false);
      return;
    }
    this.loadRun(runId);
  }

  loadRun(runId = this.run()?.id ?? 0): void {
    if (!runId) return;
    this.isLoading.set(true);
    this.errorMessage.set(null);
    this.performanceService.getRun(runId)
      .pipe(finalize(() => this.isLoading.set(false)))
      .subscribe({
        next: run => this.run.set(run),
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  cancel(): void {
    const run = this.run();
    if (!run || !this.canCancel()) return;
    this.isWorking.set(true);
    this.performanceService.cancelRun(run.id)
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: updated => this.run.set(updated),
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  openGrafana(): void {
    const url = this.run()?.grafanaDashboardUrl;
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
  }

  modeLabel(mode: PerformanceMode): string {
    return mode === 'basic' ? 'Basic' : 'Grafana + Prometheus';
  }

  typeLabel(type: PerformanceTestType): string {
    const labels: Record<PerformanceTestType, string> = { smoke: 'Smoke', load: 'Load', stress: 'Stress', spike: 'Spike', soak: 'Soak', custom: 'Custom' };
    return labels[type];
  }

  statusLabel(status: PerformanceRunStatus): string {
    const labels: Record<PerformanceRunStatus, string> = { queued: 'En file', running: 'En cours', passed: 'Réussi', failed: 'Échoué', cancelled: 'Annulé' };
    return labels[status];
  }

  formatBytes(value: number): string {
    if (value < 1024) return `${value} B`;
    if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
    if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
    return `${(value / 1024 ** 3).toFixed(2)} GB`;
  }

  private resolveError(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 0) return 'Le backend Flask est inaccessible.';
      const body = error.error as { error?: { message?: string } } | null;
      return body?.error?.message || `Erreur HTTP ${error.status}.`;
    }
    return error instanceof Error ? error.message : 'Impossible de charger le run.';
  }
}
