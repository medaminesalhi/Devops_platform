import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { finalize } from 'rxjs';

import {
  DeploymentSummary,
  DeploymentsService,
  ProjectDeploymentReadiness,
} from '../../../../core/deployments/deployments';
import { Project, ProjectsService } from '../../../../core/projects/projects';
import {
  ProjectPhaseItem,
  ProjectPhaseStepper,
} from '../../../projects/components/project-phase-stepper/project-phase-stepper';

@Component({
  selector: 'app-project-deployment-gateway',
  imports: [DatePipe, RouterLink, ProjectPhaseStepper],
  templateUrl: './project-deployment-gateway.html',
  styleUrl: './project-deployment-gateway.scss',
})
export class ProjectDeploymentGateway implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly projectsService = inject(ProjectsService);
  private readonly deploymentsService = inject(DeploymentsService);

  readonly projectId = signal<number | null>(null);
  readonly project = signal<Project | null>(null);
  readonly readiness = signal<ProjectDeploymentReadiness | null>(null);
  readonly recentDeployments = signal<DeploymentSummary[]>([]);
  readonly isLoading = signal(true);
  readonly errorMessage = signal<string | null>(null);

  readonly phases = computed<ProjectPhaseItem[]>(() => {
    const id = this.projectId();
    const base = id ? `/projects/${id}` : '/projects';
    return [
      { key: 'configuration', number: 1, label: 'Configuration', description: 'Projet, source et environnement', path: `${base}/configuration`, completed: true, unlocked: true },
      { key: 'analysis', number: 2, label: 'Analyse', description: 'Comprendre le code', path: `${base}/analysis`, completed: true, unlocked: true },
      { key: 'proposal', number: 3, label: 'Proposition', description: 'Stratégie de déploiement', path: `${base}/proposal`, completed: true, unlocked: true },
      { key: 'generation', number: 4, label: 'Génération', description: 'Fichiers et validation', path: `${base}/generation`, completed: true, unlocked: true },
      { key: 'deployment', number: 5, label: 'Déploiement', description: 'Passer à la console', path: `${base}/deployment`, completed: this.recentDeployments().some(item => item.status === 'succeeded'), unlocked: true },
    ];
  });

  ngOnInit(): void {
    const projectId = Number(this.route.snapshot.paramMap.get('projectId'));
    if (!Number.isInteger(projectId) || projectId <= 0) {
      this.errorMessage.set('Identifiant de projet invalide.');
      this.isLoading.set(false);
      return;
    }
    this.projectId.set(projectId);
    this.load(projectId);
  }

  private load(projectId: number): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    let completed = 0;
    const finish = (): void => {
      completed += 1;
      if (completed === 3) this.isLoading.set(false);
    };

    this.projectsService.getProject(projectId)
      .pipe(finalize(finish))
      .subscribe({
        next: project => this.project.set(project),
        error: error => this.errorMessage.set(this.resolveError(error)),
      });

    this.deploymentsService.getProjectReadiness(projectId)
      .pipe(finalize(finish))
      .subscribe({
        next: readiness => this.readiness.set(readiness),
        error: error => this.errorMessage.set(this.resolveError(error)),
      });

    this.deploymentsService.listDeployments({ projectId })
      .pipe(finalize(finish))
      .subscribe({
        next: result => this.recentDeployments.set(result.deployments.slice(0, 5)),
        error: error => this.errorMessage.set(this.resolveError(error)),
      });
  }

  statusLabel(status: DeploymentSummary['status']): string {
    const labels: Record<DeploymentSummary['status'], string> = {
      draft: 'Brouillon', ready: 'Prêt', queued: 'En file', running: 'En cours',
      waiting_confirmation: 'À confirmer', succeeded: 'Réussi', failed: 'Échoué', cancelled: 'Annulé',
    };
    return labels[status];
  }

  private resolveError(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 0) return 'Le backend Flask est inaccessible.';
      const body = error.error as { error?: { message?: string } } | null;
      return body?.error?.message || `Erreur HTTP ${error.status}.`;
    }
    return error instanceof Error ? error.message : 'Impossible de charger la phase Déploiement.';
  }
}