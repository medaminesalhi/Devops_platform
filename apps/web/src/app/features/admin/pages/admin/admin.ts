import {
  DatePipe,
} from '@angular/common';

import {
  Component,
  inject,
  OnInit,
  signal,
} from '@angular/core';

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
  forkJoin,
  Observable,
} from 'rxjs';

import {
  AdminApi,
  AdminRole,
  AdminSummary,
  AdminUser,
  AdminUserDetail,
} from '../../../../core/admin/admin';


interface ApiErrorResponse {
  success: false;
  error: {
    code: string;
    message: string;
  };
}


@Component({
  selector: 'app-admin',
  imports: [
    DatePipe,
    ReactiveFormsModule,
    RouterLink,
  ],
  templateUrl: './admin.html',
  styleUrl: './admin.scss',
})
export class Admin implements OnInit {
  private readonly formBuilder = inject(FormBuilder);
  private readonly adminApi = inject(AdminApi);

  readonly summary = signal<AdminSummary | null>(null);
  readonly roles = signal<AdminRole[]>([]);
  readonly users = signal<AdminUser[]>([]);
  readonly selectedUser = signal<AdminUserDetail | null>(null);

  loading = false;
  detailLoading = false;
  actionLoadingUserId: number | null = null;
  message: string | null = null;
  errorMessage: string | null = null;

  readonly filterForm =
    this.formBuilder.nonNullable.group({
      search: [''],
      status: [''],
      role: [''],
    });


  ngOnInit(): void {
    this.reloadAll();
  }


  reloadAll(
    keepSelectedUser = true,
  ): void {
    if (this.loading) {
      return;
    }

    this.loading = true;
    this.errorMessage = null;
    const selectedUserId = keepSelectedUser
      ? this.selectedUser()?.user.id ?? null
      : null;

    const filters = this.filterForm.getRawValue();

    forkJoin({
      overview: this.adminApi.getOverview(),
      users: this.adminApi.listUsers({
        search: filters.search.trim() || undefined,
        status: filters.status || undefined,
        role: filters.role || undefined,
      }),
    })
      .pipe(
        finalize(() => {
          this.loading = false;
        }),
      )
      .subscribe({
        next: ({ overview, users }) => {
          this.summary.set(overview.summary);
          this.roles.set(overview.roles);
          this.users.set(users);

          if (selectedUserId !== null) {
            this.loadUser(selectedUserId);
          }
        },
        error: (error: HttpErrorResponse) => {
          this.errorMessage = this.resolveError(error);
        },
      });
  }


  clearFilters(): void {
    this.filterForm.reset({
      search: '',
      status: '',
      role: '',
    });
    this.reloadAll(false);
  }


  loadUser(
    userId: number,
  ): void {
    this.detailLoading = true;
    this.errorMessage = null;

    this.adminApi
      .getUser(userId)
      .pipe(
        finalize(() => {
          this.detailLoading = false;
        }),
      )
      .subscribe({
        next: (detail) => {
          this.selectedUser.set(detail);
        },
        error: (error: HttpErrorResponse) => {
          this.errorMessage = this.resolveError(error);
        },
      });
  }


  approve(
    user: AdminUser,
    roleCode: string,
  ): void {
    this.performAction(
      user.id,
      this.adminApi.approveUser(user.id, roleCode),
    );
  }


  reject(
    user: AdminUser,
  ): void {
    const reason = window.prompt(
      'Motif du refus (optionnel) :',
      '',
    );

    if (reason === null) {
      return;
    }

    this.performAction(
      user.id,
      this.adminApi.rejectUser(user.id, reason),
    );
  }


  suspend(
    user: AdminUser,
  ): void {
    if (
      !window.confirm(
        `Suspendre le compte ${user.username} et révoquer ses sessions ?`,
      )
    ) {
      return;
    }

    this.performAction(
      user.id,
      this.adminApi.suspendUser(user.id),
    );
  }


  activate(
    user: AdminUser,
  ): void {
    this.performAction(
      user.id,
      this.adminApi.activateUser(user.id),
    );
  }


  updateRole(
    user: AdminUser,
    roleCode: string,
  ): void {
    this.performAction(
      user.id,
      this.adminApi.updateRole(user.id, roleCode),
    );
  }


  displayName(
    user: AdminUser,
  ): string {
    const name = [
      user.firstName,
      user.lastName,
    ]
      .filter(Boolean)
      .join(' ')
      .trim();

    return name || user.username;
  }


  roleLabel(
    roleCode: string | undefined,
  ): string {
    if (!roleCode) {
      return 'Aucun rôle';
    }

    return this.roles().find(
      (role) => role.code === roleCode,
    )?.name ?? roleCode;
  }


  isSelectedUser(
    userId: number,
  ): boolean {
    return this.selectedUser()?.user.id === userId;
  }


  statusLabel(
    status: AdminUser['status'],
  ): string {
    const labels: Record<AdminUser['status'], string> = {
      pending: 'En attente',
      active: 'Actif',
      rejected: 'Refusé',
      suspended: 'Suspendu',
    };

    return labels[status];
  }


  private performAction(
    userId: number,
    action: Observable<string>,
  ): void {
    if (this.actionLoadingUserId !== null) {
      return;
    }

    this.actionLoadingUserId = userId;
    this.message = null;
    this.errorMessage = null;

    action
      .pipe(
        finalize(() => {
          this.actionLoadingUserId = null;
        }),
      )
      .subscribe({
        next: (message) => {
          this.message = message;
          this.reloadAll(true);
        },
        error: (error: HttpErrorResponse) => {
          this.errorMessage = this.resolveError(error);
        },
      });
  }


  private resolveError(
    error: HttpErrorResponse,
  ): string {
    const response =
      error.error as ApiErrorResponse | undefined;

    return response?.error?.message ??
      'Une erreur est survenue dans l’administration.';
  }
}
