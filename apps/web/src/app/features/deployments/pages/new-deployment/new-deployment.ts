import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { finalize, switchMap } from 'rxjs';

import {
  DEPLOYMENTS_DEMO_MODE,
  DeploymentCreateRequest,
  DeploymentGenerationOption,
  DeploymentProjectOption,
  DeploymentSyncMode,
  DeploymentsService,
  ProjectDeploymentReadiness,
  ProjectSourceCommitOption,
  ProjectSourceHistory,
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
  readonly sourceHistory = signal<ProjectSourceHistory | null>(null);
  readonly readiness = signal<ProjectDeploymentReadiness | null>(null);
  readonly selectedProjectId = signal<number | null>(null);
  readonly selectedCommitSha = signal<string | null>(null);
  readonly selectedGenerationId = signal<number | null>(null);
  readonly version = signal('');
  readonly note = signal('');
  readonly syncMode = signal<DeploymentSyncMode>('confirm_before_sync');
  readonly isLoading = signal(true);
  readonly isLoadingCommits = signal(false);
  readonly isChecking = signal(false);
  readonly isSubmitting = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly commitLoadWarning = signal<string | null>(null);

  private commitSelectionExplicit = false;

  readonly selectedProject = computed(() =>
    this.projects().find(item => item.id === this.selectedProjectId()) ?? null,
  );

  readonly commitOptions = computed<ProjectSourceCommitOption[]>(() => {
    const history = this.sourceHistory();
    const project = this.selectedProject();
    const result = [...(history?.commits ?? [])];
    const known = new Set(result.map(item => item.sha.toLowerCase()));

    for (const generation of project?.generations ?? []) {
      const sha = generation.sourceCommit.toLowerCase();
      if (!sha || known.has(sha)) continue;
      result.push({
        sha: generation.sourceCommit,
        shortSha: generation.sourceCommit.slice(0, 8),
        message: `Version déjà générée (#${generation.id})`,
        authorName: null,
        authorEmail: null,
        committedAt: generation.createdAt,
        isHead: false,
      });
      known.add(sha);
    }

    return result;
  });

  readonly selectedCommit = computed(() => {
    const selected = this.selectedCommitSha()?.toLowerCase();
    if (!selected) return null;
    return this.commitOptions().find(item => item.sha.toLowerCase() === selected) ?? null;
  });

  readonly matchingGenerations = computed<DeploymentGenerationOption[]>(() => {
    const project = this.selectedProject();
    const commit = this.selectedCommitSha()?.toLowerCase();
    if (!project || !commit) return [];
    return project.generations.filter(
      generation => generation.sourceCommit.toLowerCase() === commit,
    );
  });

  readonly selectedGeneration = computed(() =>
    this.matchingGenerations().find(item => item.id === this.selectedGenerationId()) ?? null,
  );

  readonly sourceIsHistorical = computed(() => {
    const selected = this.selectedCommitSha()?.toLowerCase();
    const head = this.sourceHistory()?.head?.toLowerCase();
    return Boolean(selected && head && selected !== head);
  });

  readonly canSubmit = computed(() => {
    const readiness = this.readiness();
    return Boolean(
      this.selectedProjectId()
      && this.selectedCommitSha()
      && this.selectedGenerationId()
      && this.version().trim()
      && readiness?.ready
      && !this.isSubmitting(),
    );
  });

  ngOnInit(): void {
    const requestedProjectId = this.readNumberParam('projectId');
    const requestedGenerationId = this.readNumberParam('generationId');
    const requestedCommit = this.readCommitParam('commit');

    this.deploymentsService.getOptions()
      .pipe(finalize(() => this.isLoading.set(false)))
      .subscribe({
        next: projects => {
          this.projects.set(projects);
          const project = projects.find(item => item.id === requestedProjectId)
            ?? projects[0]
            ?? null;
          if (!project) return;

          const requestedGeneration = project.generations.find(
            item => item.id === requestedGenerationId,
          ) ?? null;
          const preferredCommit = requestedCommit
            ?? requestedGeneration?.sourceCommit
            ?? null;

          this.commitSelectionExplicit = Boolean(requestedCommit || requestedGeneration);
          this.activateProject(project, preferredCommit, requestedGeneration?.id ?? null);
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  selectProject(value: string): void {
    const projectId = Number(value);
    const project = this.projects().find(item => item.id === projectId) ?? null;
    this.selectedProjectId.set(project?.id ?? null);
    this.selectedCommitSha.set(null);
    this.selectedGenerationId.set(null);
    this.version.set('');
    this.readiness.set(null);
    this.sourceHistory.set(null);
    this.commitLoadWarning.set(null);
    this.commitSelectionExplicit = false;

    if (project) this.activateProject(project, null, null);
  }

  selectCommit(value: string): void {
    const commit = value.trim() || null;
    this.commitSelectionExplicit = true;
    this.applyCommitSelection(commit, null);
  }

  selectGeneration(value: string): void {
    const generationId = Number(value);
    const selectedId = Number.isInteger(generationId) && generationId > 0
      ? generationId
      : null;
    this.selectedGenerationId.set(selectedId);

    const generation = this.matchingGenerations().find(item => item.id === selectedId) ?? null;
    if (generation) this.version.set(this.suggestVersion(generation.sourceCommit));

    this.loadReadiness();
  }

  setSyncMode(value: DeploymentSyncMode): void {
    this.syncMode.set(value);
  }

  recheck(): void {
    const project = this.selectedProject();
    if (!project) return;

    const preferredCommit = this.commitSelectionExplicit
      ? this.selectedCommitSha()
      : null;
    const preferredGenerationId = this.commitSelectionExplicit
      ? this.selectedGenerationId()
      : null;

    this.loadSourceHistory(project, preferredCommit, preferredGenerationId);
  }

  analyzeSelectedCommit(): void {
    const projectId = this.selectedProjectId();
    const commit = this.selectedCommitSha();
    if (!projectId || !commit) return;

    void this.router.navigate(
      ['/projects', projectId, 'analysis'],
      { queryParams: { commit } },
    );
  }

  createAndStart(): void {
    const projectId = this.selectedProjectId();
    const generationId = this.selectedGenerationId();
    const sourceCommit = this.selectedCommitSha();
    if (!projectId || !generationId || !sourceCommit || !this.canSubmit()) return;

    const request: DeploymentCreateRequest = {
      projectId,
      generationId,
      sourceCommit,
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

  commitLabel(commit: ProjectSourceCommitOption): string {
    const marker = commit.isHead ? 'HEAD · ' : '';
    return `${marker}${commit.shortSha} · ${commit.message}`;
  }

  commitMeta(commit: ProjectSourceCommitOption | null): string {
    if (!commit) return '—';
    const parts = [commit.authorName, this.formatDate(commit.committedAt)].filter(Boolean);
    return parts.join(' · ') || 'Métadonnées indisponibles';
  }

  private activateProject(
    project: DeploymentProjectOption,
    preferredCommit: string | null,
    preferredGenerationId: number | null,
  ): void {
    this.selectedProjectId.set(project.id);
    this.loadSourceHistory(project, preferredCommit, preferredGenerationId);
  }

  private loadSourceHistory(
    project: DeploymentProjectOption,
    preferredCommit: string | null,
    preferredGenerationId: number | null,
  ): void {
    this.isLoadingCommits.set(true);
    this.commitLoadWarning.set(null);

    this.deploymentsService.getProjectSourceHistory(project.id, 30)
      .pipe(finalize(() => this.isLoadingCommits.set(false)))
      .subscribe({
        next: history => {
          this.sourceHistory.set(history);
          const defaultCommit = preferredCommit
            ?? history.head
            ?? project.generations[0]?.sourceCommit
            ?? null;
          this.applyCommitSelection(defaultCommit, preferredGenerationId);
        },
        error: error => {
          this.sourceHistory.set(null);
          this.commitLoadWarning.set(
            `Historique Git indisponible : ${this.resolveError(error)}. `
            + 'Les commits déjà associés à des générations restent sélectionnables.',
          );
          const fallbackCommit = preferredCommit
            ?? project.generations[0]?.sourceCommit
            ?? null;
          this.applyCommitSelection(fallbackCommit, preferredGenerationId);
        },
      });
  }

  private applyCommitSelection(
    commit: string | null,
    preferredGenerationId: number | null,
  ): void {
    this.selectedCommitSha.set(commit);

    const matching = this.matchingGenerations();
    const generation = matching.find(item => item.id === preferredGenerationId)
      ?? matching[0]
      ?? null;

    this.selectedGenerationId.set(generation?.id ?? null);
    this.version.set(commit ? this.suggestVersion(commit) : '');
    this.readiness.set(null);
    this.loadReadiness();
  }

  private loadReadiness(): void {
    const projectId = this.selectedProjectId();
    if (!projectId) return;

    this.isChecking.set(true);
    this.errorMessage.set(null);
    this.deploymentsService.getProjectReadiness(
      projectId,
      this.selectedGenerationId(),
      this.selectedCommitSha(),
    )
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

  private readCommitParam(name: string): string | null {
    const value = (this.route.snapshot.queryParamMap.get(name) ?? '').trim().toLowerCase();
    return /^[0-9a-f]{40}$|^[0-9a-f]{64}$/.test(value) ? value : null;
  }

  private suggestVersion(commit: string): string {
    return `commit-${commit.slice(0, 8)}`;
  }

  private formatDate(value: string | null): string | null {
    if (!value) return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    return new Intl.DateTimeFormat('fr-FR', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(date);
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
