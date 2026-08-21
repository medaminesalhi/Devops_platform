import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { forkJoin, finalize, of } from 'rxjs';

import { DeploymentDetails, DeploymentsService } from '../../../../core/deployments/deployments';
import {
  PERFORMANCE_DEMO_MODE,
  PerformanceMode,
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

  readonly demoMode = PERFORMANCE_DEMO_MODE;
  readonly projects = signal<Project[]>([]);
  readonly linkedDeployment = signal<DeploymentDetails | null>(null);
  readonly isLoading = signal(true);
  readonly isSubmitting = signal(false);
  readonly errorMessage = signal<string | null>(null);

  readonly projectId = signal<number | null>(null);
  readonly deploymentId = signal<number | null>(null);
  readonly name = signal('Smoke après déploiement');
  readonly description = signal('');
  readonly targetUrl = signal('');
  readonly testType = signal<PerformanceTestType>('smoke');
  readonly mode = signal<PerformanceMode>('basic');

  readonly virtualUsers = signal(2);
  readonly maxVirtualUsers = signal(2);
  readonly durationSeconds = signal(30);
  readonly errorRatePercent = signal(1);
  readonly p95Ms = signal(500);
  readonly p99Ms = signal(1000);
  readonly checksRatePercent = signal(99);

  readonly observabilityNamespace = signal('performance-observability');
  readonly retentionDays = signal(7);
  readonly grafanaIngressHost = signal('');

  readonly selectedProject = computed(() =>
    this.projects().find(project => project.id === this.projectId()) ?? null,
  );

  readonly canSubmit = computed(() => {
    const hasBaseFields = !!this.projectId()
      && !!this.name().trim()
      && this.isValidTargetUrl(this.targetUrl())
      && this.virtualUsers() > 0
      && this.maxVirtualUsers() >= this.virtualUsers()
      && this.durationSeconds() >= 10
      && this.errorRatePercent() >= 0
      && this.p95Ms() > 0
      && this.p99Ms() >= this.p95Ms()
      && this.checksRatePercent() > 0
      && this.checksRatePercent() <= 100;

    if (!hasBaseFields) return false;
    if (this.mode() === 'observability') {
      return !!this.observabilityNamespace().trim() && this.retentionDays() > 0;
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
    })
      .pipe(finalize(() => this.isLoading.set(false)))
      .subscribe({
        next: result => {
          this.projects.set(result.projects.projects);
          this.linkedDeployment.set(result.deployment);

          if (result.deployment) {
            this.projectId.set(result.deployment.projectId);
            this.targetUrl.set(result.deployment.health.applicationUrl ?? '');
            this.name.set(`Performance · ${result.deployment.projectName} · ${result.deployment.version}`);
          } else if (!this.projectId() && result.projects.projects.length) {
            this.projectId.set(result.projects.projects[0].id);
          }
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  setMode(mode: PerformanceMode): void {
    this.mode.set(mode);
  }

  setTestType(type: PerformanceTestType): void {
    this.testType.set(type);
    const presets: Record<PerformanceTestType, { vus: number; maxVus: number; duration: number }> = {
      smoke: { vus: 2, maxVus: 2, duration: 30 },
      load: { vus: 20, maxVus: 100, duration: 300 },
      stress: { vus: 50, maxVus: 250, duration: 600 },
      spike: { vus: 10, maxVus: 300, duration: 180 },
      soak: { vus: 20, maxVus: 100, duration: 3600 },
      custom: { vus: this.virtualUsers(), maxVus: this.maxVirtualUsers(), duration: this.durationSeconds() },
    };
    const preset = presets[type];
    this.virtualUsers.set(preset.vus);
    this.maxVirtualUsers.set(preset.maxVus);
    this.durationSeconds.set(preset.duration);
  }

  submit(): void {
    if (!this.canSubmit() || this.isSubmitting()) return;

    const project = this.selectedProject();
    if (!project) return;

    this.isSubmitting.set(true);
    this.errorMessage.set(null);

    this.performanceService.createAndRun({
      projectId: project.id,
      projectName: project.name,
      deploymentId: this.deploymentId(),
      name: this.name().trim(),
      description: this.description().trim() || null,
      targetUrl: this.targetUrl().trim(),
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
          namespace: this.observabilityNamespace().trim(),
          retentionDays: Number(this.retentionDays()),
          grafanaIngressHost: this.grafanaIngressHost().trim() || null,
          installPrometheus: true,
          installGrafana: true,
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

  private isValidTargetUrl(value: string): boolean {
    try {
      const url = new URL(value.trim());
      return url.protocol === 'http:' || url.protocol === 'https:';
    } catch {
      return false;
    }
  }

  private resolveError(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 0) return 'Le backend Flask est inaccessible.';
      const body = error.error as { error?: { message?: string } } | null;
      return body?.error?.message || `Erreur HTTP ${error.status}.`;
    }
    return error instanceof Error ? error.message : 'Impossible de créer le test de performance.';
  }
}
