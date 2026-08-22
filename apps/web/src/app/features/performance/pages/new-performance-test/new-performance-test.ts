import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { forkJoin, finalize, of } from 'rxjs';

import { DeploymentDetails, DeploymentsService } from '../../../../core/deployments/deployments';
import {
  PerformanceMode,
  PerformanceRuntimeConfig,
  PerformanceService,
  PerformanceTestType,
} from '../../../../core/performance/performance';
import { Project, ProjectsService } from '../../../../core/projects/projects';

@Component({
  selector: 'app-new-performance-test',
  imports: [FormsModule, RouterLink],
  templateUrl: './new-performance-test.html',
  styleUrl: './new-performance-test.scss',
})
export class NewPerformanceTest implements OnInit {
  private readonly performanceService = inject(PerformanceService);
  private readonly projectsService = inject(ProjectsService);
  private readonly deploymentsService = inject(DeploymentsService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  readonly projects = signal<Project[]>([]);
  readonly linkedDeployment = signal<DeploymentDetails | null>(null);
  readonly runtimeConfig = signal<PerformanceRuntimeConfig | null>(null);
  readonly isLoading = signal(true);
  readonly isSubmitting = signal(false);
  readonly errorMessage = signal<string | null>(null);

  readonly projectId = signal<number | null>(null);
  readonly deploymentId = signal<number | null>(null);
  readonly name = signal('Smoke après déploiement');
  readonly description = signal('');
  readonly targetUrl = signal('');
  readonly authorizationConfirmed = signal(false);
  readonly testType = signal<PerformanceTestType>('smoke');
  readonly mode = signal<PerformanceMode>('basic');

  readonly virtualUsers = signal(2);
  readonly maxVirtualUsers = signal(2);
  readonly durationSeconds = signal(30);
  readonly errorRatePercent = signal(1);
  readonly p95Ms = signal(500);
  readonly p99Ms = signal(1000);
  readonly checksRatePercent = signal(99);

  readonly observabilityNamespace = signal('');
  readonly retentionDays = signal(7);
  readonly prometheusRemoteWriteUrl = signal('');
  readonly grafanaBaseUrl = signal('');
  readonly grafanaDashboardUid = signal('k6-performance');

  readonly maxVirtualUsersLimit = computed(
    () => this.runtimeConfig()?.limits.maxVirtualUsers ?? 500,
  );

  readonly maxDurationSecondsLimit = computed(
    () => this.runtimeConfig()?.limits.maxDurationSeconds ?? 3600,
  );

  readonly maxRetentionDaysLimit = computed(
    () => this.runtimeConfig()?.limits.maxRetentionDays ?? 90,
  );

  readonly selectedProject = computed(() =>
    this.projects().find(project => project.id === this.projectId()) ?? null,
  );

  readonly canSubmit = computed(() => {
    const baseValid = !!this.projectId()
      && !!this.name().trim()
      && this.isValidHttpUrl(this.targetUrl())
      && this.authorizationConfirmed()
      && this.virtualUsers() > 0
      && this.virtualUsers() <= this.maxVirtualUsersLimit()
      && this.maxVirtualUsers() >= this.virtualUsers()
      && this.maxVirtualUsers() <= this.maxVirtualUsersLimit()
      && this.durationSeconds() >= 10
      && this.durationSeconds() <= this.maxDurationSecondsLimit()
      && this.errorRatePercent() >= 0
      && this.errorRatePercent() <= 100
      && this.p95Ms() > 0
      && this.p99Ms() >= this.p95Ms()
      && this.checksRatePercent() > 0
      && this.checksRatePercent() <= 100;

    if (!baseValid) return false;

    if (this.mode() === 'observability') {
      const grafanaUrl = this.grafanaBaseUrl().trim();
      return this.isValidHttpUrl(this.prometheusRemoteWriteUrl())
        && (!grafanaUrl || this.isValidHttpUrl(grafanaUrl))
        && this.retentionDays() >= 1
        && this.retentionDays() <= this.maxRetentionDaysLimit()
        && !!this.grafanaDashboardUid().trim();
    }

    return true;
  });

  ngOnInit(): void {
    const projectId = this.readNumberParam('projectId');
    const deploymentId = this.readNumberParam('deploymentId');
    this.projectId.set(projectId);
    this.deploymentId.set(deploymentId);

    forkJoin({
      projects: this.projectsService.getProjects({ status: 'active' }),
      deployment: deploymentId
        ? this.deploymentsService.getDeployment(deploymentId)
        : of(null),
      performanceConfig: this.performanceService.getConfig(),
    })
      .pipe(finalize(() => this.isLoading.set(false)))
      .subscribe({
        next: result => {
          this.projects.set(result.projects.projects);
          this.linkedDeployment.set(result.deployment);
          this.runtimeConfig.set(result.performanceConfig);

          if (result.deployment) {
            this.projectId.set(result.deployment.projectId);
            this.targetUrl.set(result.deployment.health.applicationUrl ?? '');
            this.name.set(
              `Performance · ${result.deployment.projectName} · ${result.deployment.version}`,
            );
          } else if (!this.projectId() && result.projects.projects.length) {
            this.projectId.set(result.projects.projects[0].id);
          }
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  setMode(mode: PerformanceMode): void {
    this.mode.set(mode);
    this.errorMessage.set(null);
  }

  setTestType(type: PerformanceTestType): void {
    this.testType.set(type);

    const presets: Record<PerformanceTestType, { vus: number; maxVus: number; duration: number }> = {
      smoke: { vus: 2, maxVus: 2, duration: 30 },
      load: { vus: 20, maxVus: 100, duration: 300 },
      stress: { vus: 50, maxVus: 250, duration: 600 },
      spike: { vus: 10, maxVus: 300, duration: 180 },
      soak: { vus: 20, maxVus: 100, duration: 3600 },
      custom: {
        vus: this.virtualUsers(),
        maxVus: this.maxVirtualUsers(),
        duration: this.durationSeconds(),
      },
    };

    const preset = presets[type];
    this.virtualUsers.set(Math.min(preset.vus, this.maxVirtualUsersLimit()));
    this.maxVirtualUsers.set(Math.min(preset.maxVus, this.maxVirtualUsersLimit()));
    this.durationSeconds.set(Math.min(preset.duration, this.maxDurationSecondsLimit()));
  }

  submit(): void {
    if (!this.canSubmit() || this.isSubmitting()) return;

    const project = this.selectedProject();
    if (!project) return;

    this.isSubmitting.set(true);
    this.errorMessage.set(null);

    this.performanceService.createAndRun({
      projectId: project.id,
      deploymentId: this.deploymentId(),
      name: this.name().trim(),
      description: this.description().trim() || null,
      targetUrl: this.targetUrl().trim(),
      authorizationConfirmed: this.authorizationConfirmed(),
      testType: this.testType(),
      mode: this.mode(),
      loadProfile: {
        virtualUsers: Number(this.virtualUsers()),
        maxVirtualUsers: Number(this.maxVirtualUsers()),
        durationSeconds: Number(this.durationSeconds()),
      },
      thresholds: {
        errorRatePercent: Number(this.errorRatePercent()),
        p95Ms: Number(this.p95Ms()),
        p99Ms: Number(this.p99Ms()),
        checksRatePercent: Number(this.checksRatePercent()),
      },
      observability: this.mode() === 'observability'
        ? {
          namespace: this.observabilityNamespace().trim() || null,
          retentionDays: Number(this.retentionDays()),
          prometheusRemoteWriteUrl: this.prometheusRemoteWriteUrl().trim(),
          grafanaBaseUrl: this.grafanaBaseUrl().trim() || null,
          grafanaDashboardUid: this.grafanaDashboardUid().trim() || 'k6-performance',
        }
        : null,
    })
      .pipe(finalize(() => this.isSubmitting.set(false)))
      .subscribe({
        next: run => this.router.navigate(['/performance/runs', run.id]),
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  formatDuration(seconds: number): string {
    if (seconds < 60) return `${seconds} secondes`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} minute${minutes > 1 ? 's' : ''}`;
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return `${hours} h${rest ? ` ${rest} min` : ''}`;
  }

  private readNumberParam(name: string): number | null {
    const value = Number(this.route.snapshot.queryParamMap.get(name));
    return Number.isInteger(value) && value > 0 ? value : null;
  }

  private isValidHttpUrl(value: string): boolean {
    try {
      const url = new URL(value.trim());
      return (url.protocol === 'http:' || url.protocol === 'https:') && !!url.hostname;
    } catch {
      return false;
    }
  }

  private resolveError(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 0) return 'Le backend Flask est inaccessible.';
      const body = error.error as { error?: { code?: string; message?: string } } | null;
      return body?.error?.message || `Erreur HTTP ${error.status}.`;
    }
    return error instanceof Error
      ? error.message
      : 'Impossible de créer le test de performance.';
  }
}
