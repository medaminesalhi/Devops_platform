import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Subscription, finalize, timer } from 'rxjs';

import {
  DEPLOYMENTS_DEMO_MODE,
  DeploymentCorrection,
  DeploymentDetails,
  DeploymentLogLevel,
  DeploymentLogScope,
  DeploymentResourceKind,
  DeploymentStatus,
  DeploymentStepStatus,
  DeploymentsService,
} from '../../../../core/deployments/deployments';

type DetailTab = 'pipeline' | 'logs' | 'resources' | 'assistant';

@Component({
  selector: 'app-deployment-detail',
  imports: [DatePipe, FormsModule, RouterLink],
  templateUrl: './deployment-detail.html',
  styleUrl: './deployment-detail.scss',
})
export class DeploymentDetail implements OnInit, OnDestroy {
  private readonly deploymentsService = inject(DeploymentsService);
  private readonly route = inject(ActivatedRoute);

  private pollingSubscription: Subscription | null = null;

  readonly demoMode = DEPLOYMENTS_DEMO_MODE;
  readonly deployment = signal<DeploymentDetails | null>(null);
  readonly activeTab = signal<DetailTab>('pipeline');
  readonly isLoading = signal(true);
  readonly isRefreshing = signal(false);
  readonly isWorking = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly successMessage = signal<string | null>(null);
  readonly liveMode = signal(true);
  readonly selectedLogScope = signal<'all' | DeploymentLogScope>('all');
  readonly selectedLogLevel = signal<'all' | DeploymentLogLevel>('all');
  readonly selectedResourceKind = signal<'all' | DeploymentResourceKind>('all');
  readonly selectedStepId = signal<string | null>(null);
  readonly chatInput = signal('');

  readonly filteredLogs = computed(() => {
    const deployment = this.deployment();
    if (!deployment) return [];
    return deployment.logs.filter(item =>
      (this.selectedLogScope() === 'all' || item.scope === this.selectedLogScope())
      && (this.selectedLogLevel() === 'all' || item.level === this.selectedLogLevel()),
    );
  });

  readonly filteredResources = computed(() => {
    const deployment = this.deployment();
    if (!deployment) return [];
    return deployment.resources.filter(item =>
      this.selectedResourceKind() === 'all' || item.kind === this.selectedResourceKind(),
    );
  });

  readonly selectedStep = computed(() => {
    const deployment = this.deployment();
    if (!deployment) return null;
    return deployment.steps.find(item => item.id === this.selectedStepId()) ?? null;
  });

  readonly canCancel = computed(() => {
    const status = this.deployment()?.status;
    return status === 'queued' || status === 'running' || status === 'waiting_confirmation';
  });

  readonly canRetry = computed(() => this.deployment()?.status === 'failed');
  readonly canConfirmSync = computed(() => this.deployment()?.status === 'waiting_confirmation');

  ngOnInit(): void {
    const deploymentId = Number(this.route.snapshot.paramMap.get('deploymentId'));
    if (!Number.isInteger(deploymentId) || deploymentId <= 0) {
      this.errorMessage.set('Identifiant de déploiement invalide.');
      this.isLoading.set(false);
      return;
    }
    this.loadDeployment(deploymentId);
    this.startPolling(deploymentId);
  }

  ngOnDestroy(): void {
    this.pollingSubscription?.unsubscribe();
  }

  setTab(tab: DetailTab): void {
    this.activeTab.set(tab);
  }

  refresh(): void {
    const id = this.deployment()?.id;
    if (id) this.loadDeployment(id, true);
  }

  toggleLiveMode(): void {
    this.liveMode.update(value => !value);
  }

  selectStep(stepId: string): void {
    this.selectedStepId.set(this.selectedStepId() === stepId ? null : stepId);
  }

  cancel(): void {
    const id = this.deployment()?.id;
    if (!id || !this.canCancel()) return;
    this.runAction(() => this.deploymentsService.cancelDeployment(id), 'Déploiement annulé.');
  }

  retry(): void {
    const id = this.deployment()?.id;
    if (!id || !this.canRetry()) return;
    this.runAction(() => this.deploymentsService.retryDeployment(id), 'Nouvelle tentative lancée.');
  }

  confirmSync(): void {
    const id = this.deployment()?.id;
    if (!id || !this.canConfirmSync()) return;
    this.runAction(
      () => this.deploymentsService.confirmSynchronization(id),
      'Synchronisation Argo CD confirmée.',
    );
  }

  requestDiagnosis(): void {
    const deployment = this.deployment();
    if (!deployment) return;
    this.isWorking.set(true);
    this.errorMessage.set(null);
    this.activeTab.set('assistant');

    this.deploymentsService.requestDiagnosis(deployment.id)
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: diagnostic => {
          this.deployment.update(current => current ? { ...current, diagnostic } : current);
          this.refresh();
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  sendMessage(): void {
    const deployment = this.deployment();
    const content = this.chatInput().trim();
    if (!deployment || !content || this.isWorking()) return;

    this.chatInput.set('');
    this.isWorking.set(true);
    this.deploymentsService.sendDiagnosticMessage(deployment.id, content)
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: chat => this.deployment.update(current => current ? { ...current, chat } : current),
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  approveCorrection(correction: DeploymentCorrection): void {
    const deployment = this.deployment();
    if (!deployment) return;
    this.isWorking.set(true);
    this.deploymentsService.approveCorrection(deployment.id, correction.id)
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: diagnostic => {
          this.deployment.update(current => current ? { ...current, diagnostic } : current);
          this.successMessage.set('Correction approuvée. Le backend préparera la prochaine action contrôlée.');
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  openApplication(): void {
    const url = this.deployment()?.health.applicationUrl;
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
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

  stepStatusLabel(status: DeploymentStepStatus): string {
    const labels: Record<DeploymentStepStatus, string> = {
      pending: 'En attente',
      running: 'En cours',
      succeeded: 'Réussie',
      failed: 'Échouée',
      skipped: 'Ignorée',
      cancelled: 'Annulée',
    };
    return labels[status];
  }

  scopeLabel(scope: DeploymentLogScope): string {
    const labels: Record<DeploymentLogScope, string> = {
      system: 'Système',
      docker: 'Docker',
      nexus: 'Nexus',
      gitops: 'GitOps',
      argocd: 'Argo CD',
      kubernetes: 'Kubernetes',
      application: 'Application',
    };
    return labels[scope];
  }

  resourceKindLabel(kind: DeploymentResourceKind): string {
    const labels: Record<DeploymentResourceKind, string> = {
      argocd_application: 'Application Argo CD',
      deployment: 'Deployment',
      pod: 'Pod',
      service: 'Service',
      ingress: 'Ingress',
      job: 'Job',
      pvc: 'Volume',
    };
    return labels[kind];
  }

  phaseLabel(phase: DeploymentCorrection['targetPhase']): string {
    const labels: Record<DeploymentCorrection['targetPhase'], string> = {
      integration: 'Intégrations',
      analysis: 'Analyse',
      proposal: 'Proposition',
      generation: 'Génération',
      deployment: 'Déploiement',
    };
    return labels[phase];
  }

  private startPolling(deploymentId: number): void {
    this.pollingSubscription = timer(2200, 2200).subscribe(() => {
      if (!this.liveMode() || this.isRefreshing() || this.isWorking()) return;
      const status = this.deployment()?.status;
      if (status === 'running' || status === 'queued') this.loadDeployment(deploymentId, true, true);
    });
  }

  private loadDeployment(deploymentId: number, refresh = false, silent = false): void {
    if (!silent) refresh ? this.isRefreshing.set(true) : this.isLoading.set(true);
    this.deploymentsService.getDeployment(deploymentId)
      .pipe(finalize(() => {
        this.isLoading.set(false);
        this.isRefreshing.set(false);
      }))
      .subscribe({
        next: deployment => {
          this.deployment.set(deployment);
          if (!this.selectedStepId()) {
            const active = deployment.steps.find(step => step.status === 'running' || step.status === 'failed');
            this.selectedStepId.set(active?.id ?? null);
          }
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  private runAction(
    operation: () => ReturnType<DeploymentsService['cancelDeployment']>,
    success: string,
  ): void {
    this.isWorking.set(true);
    this.errorMessage.set(null);
    this.successMessage.set(null);
    operation()
      .pipe(finalize(() => this.isWorking.set(false)))
      .subscribe({
        next: deployment => {
          this.deployment.set(deployment);
          this.successMessage.set(success);
        },
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  private resolveError(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 0) return 'Le backend Flask est inaccessible.';
      const body = error.error as { error?: { message?: string } } | null;
      return body?.error?.message || `Erreur HTTP ${error.status}.`;
    }
    return error instanceof Error ? error.message : 'Une opération a échoué.';
  }
}