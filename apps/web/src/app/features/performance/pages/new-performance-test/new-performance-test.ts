import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { finalize, forkJoin, of } from 'rxjs';

import { DeploymentDetails, DeploymentsService } from '../../../../core/deployments/deployments';
import {
  IntegrationConnection,
  IntegrationsService,
} from '../../../../core/integrations/integrations';
import {
  GrafanaCredentials,
  ObservabilityStack,
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
export class NewPerformanceTest implements OnInit, OnDestroy {
  private readonly performanceService = inject(PerformanceService);
  private readonly projectsService = inject(ProjectsService);
  private readonly deploymentsService = inject(DeploymentsService);
  private readonly integrationsService = inject(IntegrationsService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  private stackPollTimer: ReturnType<typeof setTimeout> | null = null;

  readonly projects = signal<Project[]>([]);
  readonly linkedDeployment = signal<DeploymentDetails | null>(null);
  readonly runtimeConfig = signal<PerformanceRuntimeConfig | null>(null);
  readonly kubernetesConnections = signal<IntegrationConnection[]>([]);
  readonly observabilityStacks = signal<ObservabilityStack[]>([]);

  readonly isLoading = signal(true);
  readonly isSubmitting = signal(false);
  readonly isProvisioning = signal(false);
  readonly isLoadingCredentials = signal(false);
  readonly grafanaCredentials = signal<GrafanaCredentials | null>(null);
  readonly credentialsStackId = signal<number | null>(null);
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

  // Managed Prometheus + Grafana provisioning.
  readonly selectedObservabilityStackId = signal<number | null>(null);
  readonly kubernetesConnectionId = signal<number | null>(null);
  readonly observabilityNamespace = signal('performance-observability');
  readonly retentionDays = signal(7);
  readonly prometheusStorageSize = signal('8Gi');
  readonly grafanaStorageSize = signal('2Gi');
  readonly storageClassName = signal('');
  readonly ingressEnabled = signal(false);
  readonly ingressClassName = signal('nginx');
  readonly grafanaHost = signal('');
  readonly grafanaTlsEnabled = signal(false);
  readonly grafanaTlsSecretName = signal('');

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

  readonly selectedObservabilityStack = computed(() => {
    const id = this.selectedObservabilityStackId();
    return this.observabilityStacks().find(stack => stack.id === id) ?? null;
  });

  readonly readyObservabilityStacks = computed(() =>
    this.observabilityStacks().filter(stack => stack.status === 'ready'),
  );

  readonly canProvisionObservability = computed(() => {
    const connectionId = this.kubernetesConnectionId();
    const namespace = this.observabilityNamespace().trim();
    if (!this.projectId() || !connectionId || !namespace) return false;
    if (this.retentionDays() < 1 || this.retentionDays() > 365) return false;
    if (!this.prometheusStorageSize().trim() || !this.grafanaStorageSize().trim()) return false;
    if (this.ingressEnabled() && !this.grafanaHost().trim()) return false;
    if (this.grafanaTlsEnabled() && !this.grafanaTlsSecretName().trim()) return false;
    return true;
  });

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
      return this.selectedObservabilityStack()?.status === 'ready';
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
      integrations: this.integrationsService.getAll(),
    })
      .pipe(finalize(() => this.isLoading.set(false)))
      .subscribe({
        next: result => {
          this.projects.set(result.projects.projects);
          this.linkedDeployment.set(result.deployment);
          this.runtimeConfig.set(result.performanceConfig);

          const kubernetes = result.integrations.filter(
            connection => connection.providerType === 'kubernetes' && connection.enabled,
          );
          this.kubernetesConnections.set(kubernetes);
          if (kubernetes.length) this.kubernetesConnectionId.set(kubernetes[0].id);

          if (result.deployment) {
            this.projectId.set(result.deployment.projectId);
            this.targetUrl.set(result.deployment.health.applicationUrl ?? '');
            this.name.set(
              `Performance · ${result.deployment.projectName} · ${result.deployment.version}`,
            );
          } else if (!this.projectId() && result.projects.projects.length) {
            this.projectId.set(result.projects.projects[0].id);
          }

          this.loadObservabilityStacks();
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  ngOnDestroy(): void {
    if (this.stackPollTimer) clearTimeout(this.stackPollTimer);
  }

  setMode(mode: PerformanceMode): void {
    this.mode.set(mode);
    this.errorMessage.set(null);
    if (mode === 'observability') this.loadObservabilityStacks();
  }

  selectProject(value: number | string): void {
    const id = Number(value);
    this.projectId.set(Number.isInteger(id) && id > 0 ? id : null);
    this.selectedObservabilityStackId.set(null);
    this.loadObservabilityStacks();
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

  provisionObservability(): void {
    const projectId = this.projectId();
    const connectionId = this.kubernetesConnectionId();
    if (!projectId || !connectionId || !this.canProvisionObservability()) return;

    this.isProvisioning.set(true);
    this.errorMessage.set(null);

    this.performanceService.createObservabilityStack({
      projectId,
      kubernetesConnectionId: connectionId,
      namespace: this.observabilityNamespace().trim(),
      retentionDays: Number(this.retentionDays()),
      prometheusStorageSize: this.prometheusStorageSize().trim(),
      grafanaStorageSize: this.grafanaStorageSize().trim(),
      storageClassName: this.storageClassName().trim() || null,
      ingressEnabled: this.ingressEnabled(),
      ingressClassName: this.ingressEnabled()
        ? this.ingressClassName().trim() || null
        : null,
      grafanaHost: this.ingressEnabled()
        ? this.grafanaHost().trim() || null
        : null,
      grafanaTlsEnabled: this.ingressEnabled() && this.grafanaTlsEnabled(),
      grafanaTlsSecretName: this.grafanaTlsEnabled()
        ? this.grafanaTlsSecretName().trim() || null
        : null,
    })
      .pipe(finalize(() => this.isProvisioning.set(false)))
      .subscribe({
        next: stack => {
          this.upsertStack(stack);
          this.selectedObservabilityStackId.set(stack.id);
          this.pollObservabilityStack(stack.id);
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  retryObservability(stack: ObservabilityStack): void {
    this.errorMessage.set(null);
    this.performanceService.retryObservabilityStack(stack.id).subscribe({
      next: updated => {
        this.upsertStack(updated);
        this.selectedObservabilityStackId.set(updated.id);
        this.pollObservabilityStack(updated.id);
      },
      error: error => this.errorMessage.set(this.resolveError(error)),
    });
  }

  useObservabilityStack(stack: ObservabilityStack): void {
    if (stack.status !== 'ready') return;
    this.selectedObservabilityStackId.set(stack.id);
  }

  showGrafanaCredentials(stack: ObservabilityStack): void {
    if (stack.status !== 'ready') return;

    if (this.credentialsStackId() === stack.id && this.grafanaCredentials()) {
      this.grafanaCredentials.set(null);
      this.credentialsStackId.set(null);
      return;
    }

    this.isLoadingCredentials.set(true);
    this.errorMessage.set(null);
    this.performanceService.getGrafanaCredentials(stack.id)
      .pipe(finalize(() => this.isLoadingCredentials.set(false)))
      .subscribe({
        next: credentials => {
          this.credentialsStackId.set(stack.id);
          this.grafanaCredentials.set(credentials);
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
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
      observability: null,
      observabilityStackId: this.mode() === 'observability'
        ? this.selectedObservabilityStackId()
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

  stackStatusLabel(stack: ObservabilityStack): string {
    const labels: Record<ObservabilityStack['status'], string> = {
      queued: 'En file',
      provisioning: 'Installation en cours',
      ready: 'Prête',
      failed: 'Échec',
      deleting: 'Suppression',
      deleted: 'Supprimée',
    };
    return labels[stack.status];
  }

  private loadObservabilityStacks(): void {
    const projectId = this.projectId();
    if (!projectId) {
      this.observabilityStacks.set([]);
      this.selectedObservabilityStackId.set(null);
      return;
    }

    this.performanceService.listObservabilityStacks(projectId).subscribe({
      next: stacks => {
        this.observabilityStacks.set(stacks);
        const selected = this.selectedObservabilityStackId();
        if (!selected) {
          const ready = stacks.find(stack => stack.status === 'ready');
          if (ready) this.selectedObservabilityStackId.set(ready.id);
        }
        const active = stacks.find(
          stack => stack.status === 'queued' || stack.status === 'provisioning',
        );
        if (active) this.pollObservabilityStack(active.id);
      },
      error: error => this.errorMessage.set(this.resolveError(error)),
    });
  }

  private pollObservabilityStack(stackId: number): void {
    if (this.stackPollTimer) clearTimeout(this.stackPollTimer);

    this.performanceService.getObservabilityStack(stackId).subscribe({
      next: stack => {
        this.upsertStack(stack);
        if (stack.status === 'ready') {
          this.selectedObservabilityStackId.set(stack.id);
          return;
        }
        if (stack.status === 'queued' || stack.status === 'provisioning') {
          this.stackPollTimer = setTimeout(
            () => this.pollObservabilityStack(stackId),
            2500,
          );
        }
      },
      error: error => this.errorMessage.set(this.resolveError(error)),
    });
  }

  private upsertStack(stack: ObservabilityStack): void {
    this.observabilityStacks.update(current => {
      const index = current.findIndex(item => item.id === stack.id);
      if (index < 0) return [stack, ...current];
      return current.map(item => item.id === stack.id ? stack : item);
    });
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
      : 'Impossible de traiter la configuration de performance.';
  }
}
