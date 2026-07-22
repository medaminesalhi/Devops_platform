import {
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';

import {
  ActivatedRoute,
  RouterLink,
} from '@angular/router';

import {
  HttpErrorResponse,
} from '@angular/common/http';

import {
  finalize,
} from 'rxjs';

import {
  Project,
  ProjectsService,
} from '../../../../core/projects/projects';


interface ApiErrorResponse {
  success: false;

  error: {
    code: string;
    message: string;
  };
}


@Component({
  selector: 'app-project-detail',

  imports: [
    RouterLink,
  ],

  templateUrl: './project-detail.html',
  styleUrl: './project-detail.scss',
})
export class ProjectDetail implements OnInit {
  /*
   * Service utilisé pour récupérer le projet
   * depuis Flask.
   */
  private readonly projectsService =
    inject(ProjectsService);


  /*
   * ActivatedRoute permet de lire l’identifiant
   * présent dans l’URL :
   *
   * /projects/12
   *
   * projectId = 12
   */
  private readonly route =
    inject(ActivatedRoute);


  /*
   * Projet affiché dans la page.
   */
  readonly project =
    signal<Project | null>(null);


  /*
   * État de chargement.
   */
  readonly isLoading =
    signal(true);


  /*
   * Message d’erreur éventuel.
   */
  readonly errorMessage =
    signal<string | null>(null);


  ngOnInit(): void {
    /*
     * Lit le paramètre défini dans app.routes.ts :
     *
     * path: 'projects/:projectId'
     */
    const rawProjectId =
      this.route.snapshot.paramMap.get(
        'projectId',
      );

    const projectId =
      Number(rawProjectId);


    /*
     * Refuse :
     *
     * /projects/abc
     * /projects/0
     * /projects/-1
     */
    if (
      !Number.isInteger(projectId)
      || projectId <= 0
    ) {
      this.errorMessage.set(
        'L’identifiant du projet est invalide.',
      );

      this.isLoading.set(false);

      return;
    }


    this.loadProject(projectId);
  }


  loadProject(
    projectId: number,
  ): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);


    /*
     * Appel :
     *
     * GET /api/projects/{projectId}
     */
    this.projectsService
      .getProject(projectId)
      .pipe(
        finalize(() => {
          this.isLoading.set(false);
        }),
      )
      .subscribe({
        next: (
          project: Project,
        ) => {
          this.project.set(project);
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.errorMessage.set(
            this.resolveError(error),
          );
        },
      });
  }


  environmentTypeLabel(
    type: string,
  ): string {
    const labels:
      Record<string, string> = {
        lab:
          'Lab',

        staging:
          'Staging',

        production:
          'Production',

        custom:
          'Personnalisé',
      };

    return labels[type] ?? type;
  }


  private resolveError(
    error: HttpErrorResponse,
  ): string {
    if (error.status === 0) {
      return (
        'Le backend Flask est inaccessible. '
        + 'Vérifiez le port 5000 et '
        + 'le proxy Angular.'
      );
    }

    const response =
      error.error as
        ApiErrorResponse | null;

    if (response?.error?.message) {
      return response.error.message;
    }

    return (
      `Erreur HTTP `
      + `${error.status || 'inconnue'}.`
    );
  }
}