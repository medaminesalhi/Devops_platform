import {
  Component,
  OnInit,
  inject,
  signal,
} from '@angular/core';

import {
  DatePipe,
} from '@angular/common';

import {
  HttpErrorResponse,
} from '@angular/common/http';

import {
  FormBuilder,
  ReactiveFormsModule,
} from '@angular/forms';

import {
  RouterLink,
} from '@angular/router';

import {
  finalize,
} from 'rxjs';

import {
  Project,
  ProjectStatus,
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
  selector: 'app-projects',

  imports: [
    ReactiveFormsModule,
    RouterLink,
    DatePipe,
  ],

  templateUrl: './projects.html',
  styleUrl: './projects.scss',
})
export class Projects implements OnInit {
  private readonly projectsService =
    inject(ProjectsService);

  private readonly formBuilder =
    inject(FormBuilder);


  readonly projects =
    signal<Project[]>([]);

  readonly total =
    signal(0);

  readonly isLoading =
    signal(true);

  readonly errorMessage =
    signal<string | null>(null);


  readonly filterForm =
    this.formBuilder.nonNullable.group({
      search: '',
      status: '',
    });


  ngOnInit(): void {
    this.loadProjects();
  }


  loadProjects(): void {
    const values =
      this.filterForm.getRawValue();

    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.projectsService
      .getProjects({
        search:
          values.search.trim()
          || null,

        status:
          this.toProjectStatus(
            values.status,
          ),
      })
      .pipe(
        finalize(() => {
          this.isLoading.set(false);
        }),
      )
      .subscribe({
        next: result => {
          this.projects.set(
            result.projects,
          );

          this.total.set(
            result.total,
          );
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


  resetFilters(): void {
    this.filterForm.reset({
      search: '',
      status: '',
    });

    this.loadProjects();
  }


  projectStatusLabel(
    status: ProjectStatus,
  ): string {
    const labels:
      Record<ProjectStatus, string> = {
        draft: 'Brouillon',
        active: 'Actif',
        source_error:
          'Erreur de source',
        archived: 'Archivé',
      };

    return labels[status];
  }


  visibilityLabel(
    visibility: string,
  ): string {
    return (
      visibility === 'public'
        ? 'Public'
        : 'Privé'
    );
  }


  private toProjectStatus(
    value: string,
  ): ProjectStatus | null {
    switch (value) {
      case 'draft':
        return 'draft';

      case 'active':
        return 'active';

      case 'source_error':
        return 'source_error';

      case 'archived':
        return 'archived';

      default:
        return null;
    }
  }


  private resolveError(
    error: HttpErrorResponse,
  ): string {
    if (error.status === 0) {
      return (
        'Le backend Flask est inaccessible.'
      );
    }

    const response =
      error.error as
        ApiErrorResponse | null;

    return (
      response?.error?.message
      || `Erreur HTTP ${error.status}.`
    );
  }
}