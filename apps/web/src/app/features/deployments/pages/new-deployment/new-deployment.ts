import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { finalize, switchMap } from 'rxjs';

import {
  DEPLOYMENTS_DEMO_MODE,
  DeploymentCreateRequest,
  DeploymentProjectOption,
  DeploymentSyncMode,
  DeploymentsService,
  ProjectDeploymentReadiness,
} from '../../../../core/deployments/deployments';

@Component({
  selector: 'app-new-deployment',
  imports: [FormsModule, RouterLink],
  templateUrl: './new-deployment.html',
  styleUrl: './new-deployment.scss',
})
export class NewDeployment implements OnInit {
  private readonly deploymentsService = inject(DeploymentsService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  readonly demoMode = DEPLOYMENTS_DEMO_MODE;
  readonly projects = signal<DeploymentProjectOption[]>([]);
  readonly readiness = signal<ProjectDeploymentReadiness | null>(null);
  readonly selectedProjectId = signal<number | null>(null);
  readonly selectedGenerationId = signal<number | null>(null);
  readonly version = signal('');
  readonly note = signal('');
  readonly syncMode = signal<DeploymentSyncMode>('confirm_before_sync');
  readonly isLoading = signal(true);
  readonly isChecking = signal(false);
  readonly isSubmitting = signal(false);
  readonly errorMessage = signal<string | null>(null);

  readonly selectedProject = computed(() =>
    this.projects().find(item => item.id === this.selectedProjectId()) ?? null,
  );

  readonly selectedGeneration = computed(() =>
    this.selectedProject()?.generations.find(item => item.id === this.selectedGenerationId()) ?? null,
  );

  readonly canSubmit = computed(() => {
    const readiness = this.readiness();
    return Boolean(
      this.selectedProjectId()
      && this.selectedGenerationId()
      && this.version().trim()
      && readiness?.ready
      && !this.isSubmitting(),
    );
  });

  ngOnInit(): void {
    const requestedProjectId = this.readNumberParam('projectId');
    const requestedGenerationId = this.readNumberParam('generationId');

    this.deploymentsService.getOptions()
      .pipe(finalize(() => this.isLoading.set(false)))
      .subscribe({
        next: projects => {
          this.projects.set(projects);
          const project = projects.find(item => item.id === requestedProjectId) ?? projects[0] ?? null;
          if (!project) return;
          this.selectedProjectId.set(project.id);
          const generation = project.generations.find(item => item.id === requestedGenerationId)
            ?? project.generations[0]
            ?? null;
          if (generation) {
            this.selectedGenerationId.set(generation.id);
            this.version.set(this.suggestVersion(generation.sourceCommit));
          }
          this.loadReadiness(project.id, generation?.id ?? null);
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  selectProject(value: string): void {
    const projectId = Number(value);
    this.selectedProjectId.set(Number.isInteger(projectId) ? projectId : null);
    const project = this.projects().find(item => item.id === projectId) ?? null;
    const generation = project?.generations[0] ?? null;
    this.selectedGenerationId.set(generation?.id ?? null);
    this.version.set(generation ? this.suggestVersion(generation.sourceCommit) : '');
    this.readiness.set(null);
    if (project) this.loadReadiness(project.id, generation?.id ?? null);
  }

  selectGeneration(value: string): void {
    const generationId = Number(value);
    const selectedId = Number.isInteger(generationId) ? generationId : null;
    this.selectedGenerationId.set(selectedId);

    const generation = this.selectedProject()?.generations.find(
      item => item.id === generationId,
    ) ?? null;
    if (generation) this.version.set(this.suggestVersion(generation.sourceCommit));

    const projectId = this.selectedProjectId();
    this.readiness.set(null);
    if (projectId) this.loadReadiness(projectId, selectedId);
  }

  setSyncMode(value: DeploymentSyncMode): void {
    this.syncMode.set(value);
  }

  recheck(): void {
    const projectId = this.selectedProjectId();
    if (projectId) this.loadReadiness(projectId, this.selectedGenerationId());
  }

  createAndStart(): void {
    const projectId = this.selectedProjectId();
    const generationId = this.selectedGenerationId();
    if (!projectId || !generationId || !this.canSubmit()) return;

    const request: DeploymentCreateRequest = {
      projectId,
      generationId,
      version: this.version().trim(),
      note: this.note().trim() || null,
      syncMode: this.syncMode(),
    };

    this.isSubmitting.set(true);
    this.errorMessage.set(null);

    this.deploymentsService.createDeployment(request)
      .pipe(
        switchMap(deployment => this.deploymentsService.startDeployment(deployment.id)),
        finalize(() => this.isSubmitting.set(false)),
      )
      .subscribe({
        next: deployment => void this.router.navigate(['/deployments', deployment.id]),
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  checkLabel(status: 'ready' | 'warning' | 'blocked'): string {
    return status === 'ready' ? 'Prêt' : status === 'warning' ? 'À vérifier' : 'Bloquant';
  }

  private loadReadiness(projectId: number, generationId: number | null): void {
    this.isChecking.set(true);
    this.errorMessage.set(null);
    this.deploymentsService.getProjectReadiness(projectId, generationId)
      .pipe(finalize(() => this.isChecking.set(false)))
      .subscribe({
        next: readiness => this.readiness.set(readiness),
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  private readNumberParam(name: string): number | null {
    const value = Number(this.route.snapshot.queryParamMap.get(name));
    return Number.isInteger(value) && value > 0 ? value : null;
  }

  private suggestVersion(commit: string): string {
    return `commit-${commit.slice(0, 8)}`;
  }

  private resolveError(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 0) return 'Le backend Flask est inaccessible.';
      const body = error.error as { error?: { message?: string } } | null;
      return body?.error?.message || `Erreur HTTP ${error.status}.`;
    }
    return error instanceof Error ? error.message : 'Impossible de préparer le déploiement.';
  }
}