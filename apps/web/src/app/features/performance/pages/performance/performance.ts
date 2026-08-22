import { DatePipe, DecimalPipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { finalize, forkJoin } from 'rxjs';

import {
  PerformanceMode,
  PerformanceOverview,
  PerformanceRunStatus,
  PerformanceRunSummary,
  PerformanceService,
  PerformanceTest,
  PerformanceTestType,
} from '../../../../core/performance/performance';

@Component({
  selector: 'app-performance',
  imports: [DatePipe, DecimalPipe, ReactiveFormsModule, RouterLink],
  templateUrl: './performance.html',
  styleUrl: './performance.scss',
})
export class Performance implements OnInit {
  private readonly performanceService = inject(PerformanceService);
  private readonly formBuilder = inject(FormBuilder);

  readonly isLoading = signal(true);
  readonly isRefreshing = signal(false);
  readonly errorMessage = signal<string | null>(null);

  readonly overview = signal<PerformanceOverview>({
    totalTests: 0,
    totalRuns: 0,
    runningRuns: 0,
    passedRuns: 0,
    failedRuns: 0,
  });

  readonly tests = signal<PerformanceTest[]>([]);
  readonly runs = signal<PerformanceRunSummary[]>([]);

  readonly filterForm = this.formBuilder.nonNullable.group({
    search: '',
    mode: '',
  });

  readonly finishedRuns = computed(() => {
    const data = this.overview();
    return data.passedRuns + data.failedRuns;
  });

  readonly successRate = computed(() => {
    const finished = this.finishedRuns();
    return finished > 0
      ? Math.round((this.overview().passedRuns / finished) * 100)
      : 0;
  });

  readonly failureRate = computed(() => {
    const finished = this.finishedRuns();
    return finished > 0
      ? Math.round((this.overview().failedRuns / finished) * 100)
      : 0;
  });

  readonly recentRuns = computed(() => this.runs().slice(0, 6));

  readonly basicTestsCount = computed(
    () => this.tests().filter(test => test.mode === 'basic').length,
  );

  readonly observabilityTestsCount = computed(
    () => this.tests().filter(test => test.mode === 'observability').length,
  );

  ngOnInit(): void {
    this.loadData();
  }

  loadData(refresh = false): void {
    const filters = this.filterForm.getRawValue();

    refresh
      ? this.isRefreshing.set(true)
      : this.isLoading.set(true);

    this.errorMessage.set(null);

    forkJoin({
      overview: this.performanceService.getOverview(),
      tests: this.performanceService.listTests({
        search: filters.search.trim() || null,
        mode: this.toMode(filters.mode),
      }),
      runs: this.performanceService.listRuns(),
    })
      .pipe(
        finalize(() => {
          this.isLoading.set(false);
          this.isRefreshing.set(false);
        }),
      )
      .subscribe({
        next: result => {
          this.overview.set(result.overview);
          this.tests.set(result.tests);
          this.runs.set(result.runs);
        },
        error: error => {
          this.errorMessage.set(this.resolveError(error));
        },
      });
  }

  resetFilters(): void {
    this.filterForm.reset({ search: '', mode: '' });
    this.loadData();
  }

  modeLabel(mode: PerformanceMode): string {
    return mode === 'basic' ? 'Basic' : 'Observabilité';
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

  statusIcon(status: PerformanceRunStatus): string {
    const icons: Record<PerformanceRunStatus, string> = {
      queued: '•',
      running: '↻',
      passed: '✓',
      failed: '!',
      cancelled: '×',
    };

    return icons[status];
  }

  formatDuration(seconds: number): string {
    if (seconds < 60) {
      return `${seconds} s`;
    }

    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;

    return rest
      ? `${minutes} min ${rest} s`
      : `${minutes} min`;
  }

  private toMode(value: string): PerformanceMode | null {
    return value === 'basic' || value === 'observability'
      ? value
      : null;
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

    return error instanceof Error
      ? error.message
      : 'Impossible de charger le module Performance.';
  }
}
