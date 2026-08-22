import { DatePipe, DecimalPipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, exhaustMap, finalize, takeWhile, timer } from 'rxjs';

import {
  PerformanceMode,
  PerformanceRun,
  PerformanceRunStatus,
  PerformanceSample,
  PerformanceService,
  PerformanceTestType,
} from '../../../../core/performance/performance';

interface ChartSeries {
  points: string;
  max: number;
}

interface LatencyChart {
  p95Points: string;
  p99Points: string;
  max: number;
}

@Component({
  selector: 'app-performance-run-detail',
  imports: [DatePipe, DecimalPipe, RouterLink],
  templateUrl: './performance-run-detail.html',
  styleUrl: './performance-run-detail.scss',
})
export class PerformanceRunDetail implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly performanceService = inject(PerformanceService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  readonly run = signal<PerformanceRun | null>(null);
  readonly isLoading = signal(true);
  readonly isWorking = signal(false);
  readonly isAutoRefreshing = signal(false);
  readonly errorMessage = signal<string | null>(null);

  readonly canCancel = computed(
    () => this.run()?.status === 'queued' || this.run()?.status === 'running',
  );

  readonly canRerun = computed(() => {
    const status = this.run()?.status;
    return status === 'passed' || status === 'failed' || status === 'cancelled';
  });

  readonly hasSamples = computed(() => (this.run()?.samples?.length ?? 0) > 0);

  readonly latencyChart = computed<LatencyChart>(() => {
    const samples = this.run()?.samples ?? [];
    const p95 = samples.map(sample => sample.p95Ms);
    const p99 = samples.map(sample => sample.p99Ms);
    const max = Math.max(1, ...p95, ...p99);

    return {
      p95Points: this.toPoints(p95, max),
      p99Points: this.toPoints(p99, max),
      max,
    };
  });

  readonly rpsChart = computed<ChartSeries>(() =>
    this.buildSeries((this.run()?.samples ?? []).map(sample => sample.rps)),
  );

  readonly vusChart = computed<ChartSeries>(() =>
    this.buildSeries((this.run()?.samples ?? []).map(sample => sample.vus)),
  );

  readonly errorsChart = computed<ChartSeries>(() =>
    this.buildSeries(
      (this.run()?.samples ?? []).map(sample => sample.errorRatePercent),
      100,
    ),
  );

  ngOnInit(): void {
    const runId = Number(this.route.snapshot.paramMap.get('runId'));

    if (!Number.isInteger(runId) || runId <= 0) {
      this.errorMessage.set('Identifiant de run invalide.');
      this.isLoading.set(false);
      return;
    }

    this.startAutoRefresh(runId);
  }

  private startAutoRefresh(runId: number): void {
    this.isAutoRefreshing.set(true);

    timer(0, 2000)
      .pipe(
        exhaustMap(() =>
          this.performanceService.getRun(runId).pipe(
            catchError((error: unknown) => {
              this.errorMessage.set(this.resolveError(error));
              this.isLoading.set(false);
              return EMPTY;
            }),
          ),
        ),
        takeWhile(
          (current: PerformanceRun) =>
            current.status === 'queued' || current.status === 'running',
          true,
        ),
        finalize(() => {
          this.isLoading.set(false);
          this.isAutoRefreshing.set(false);
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (current: PerformanceRun) => {
          this.run.set(current);
          this.errorMessage.set(null);
          this.isLoading.set(false);
        },
      });
  }

  loadRun(runId = this.run()?.id ?? 0): void {
    if (!runId) {
      return;
    }

    this.errorMessage.set(null);

    this.performanceService.getRun(runId).subscribe({
      next: (current: PerformanceRun) => {
        this.run.set(current);
      },
      error: (error: unknown) => {
        this.errorMessage.set(this.resolveError(error));
      },
    });
  }

  cancel(): void {
    const current = this.run();

    if (!current || !this.canCancel()) {
      return;
    }

    this.isWorking.set(true);
    this.errorMessage.set(null);

    this.performanceService
      .cancelRun(current.id)
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: (updated: PerformanceRun) => {
          this.run.set(updated);
        },
        error: (error: unknown) => {
          this.errorMessage.set(this.resolveError(error));
        },
      });
  }

  rerun(): void {
    const current = this.run();

    if (!current || !this.canRerun() || this.isWorking()) {
      return;
    }

    this.isWorking.set(true);
    this.errorMessage.set(null);

    this.performanceService
      .rerunRun(current.id)
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: (created: PerformanceRun) => {
          this.run.set(created);
          void this.router.navigate(['/performance/runs', created.id]);
          this.startAutoRefresh(created.id);
        },
        error: (error: unknown) => {
          this.errorMessage.set(this.resolveError(error));
        },
      });
  }

  openGrafana(): void {
    const url = this.run()?.grafanaDashboardUrl;

    if (!url) {
      return;
    }

    window.open(url, '_blank', 'noopener,noreferrer');
  }

  modeLabel(mode: PerformanceMode): string {
    return mode === 'basic' ? 'Basic' : 'Grafana + Prometheus';
  }

  typeLabel(type: PerformanceTestType): string {
    const labels: Record<PerformanceTestType, string> = {
      smoke: 'Smoke',
      load: 'Load',
      stress: 'Stress',
      spike: 'Spike',
      soak: 'Soak',
      custom: 'Custom',
    };

    return labels[type];
  }

  statusLabel(status: PerformanceRunStatus): string {
    const labels: Record<PerformanceRunStatus, string> = {
      queued: 'En file',
      running: 'En cours',
      passed: 'Réussi',
      failed: 'Échoué',
      cancelled: 'Annulé',
    };

    return labels[status];
  }

  formatBytes(value: number): string {
    if (value < 1024) {
      return `${value} B`;
    }

    if (value < 1024 ** 2) {
      return `${(value / 1024).toFixed(1)} KB`;
    }

    if (value < 1024 ** 3) {
      return `${(value / 1024 ** 2).toFixed(1)} MB`;
    }

    return `${(value / 1024 ** 3).toFixed(2)} GB`;
  }

  chartDurationLabel(): string {
    const samples = this.run()?.samples ?? [];
    if (!samples.length) {
      return '0 s';
    }
    return `${samples[samples.length - 1].elapsedSeconds} s`;
  }

  private buildSeries(values: number[], fixedMax?: number): ChartSeries {
    const max = fixedMax ?? Math.max(1, ...values);
    return {
      points: this.toPoints(values, Math.max(1, max)),
      max: Math.max(1, max),
    };
  }

  private toPoints(values: number[], max: number): string {
    if (!values.length) {
      return '';
    }

    const width = 600;
    const height = 160;
    const left = 12;
    const right = 12;
    const top = 12;
    const bottom = 18;
    const drawableWidth = width - left - right;
    const drawableHeight = height - top - bottom;

    return values
      .map((rawValue, index) => {
        const value = Number.isFinite(rawValue) ? Math.max(0, rawValue) : 0;
        const x =
          values.length === 1
            ? width / 2
            : left + (index / (values.length - 1)) * drawableWidth;
        const y = top + (1 - Math.min(value / max, 1)) * drawableHeight;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  }

  private resolveError(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 0) {
        return 'Le backend Flask est inaccessible.';
      }

      const body = error.error as {
        error?: {
          message?: string;
        };
      } | null;

      return body?.error?.message || `Erreur HTTP ${error.status}.`;
    }

    if (error instanceof Error) {
      return error.message;
    }

    return 'Impossible de charger le run.';
  }
}
