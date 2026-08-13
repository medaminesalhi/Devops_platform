import {
  Component,
  inject,
  OnInit,
} from '@angular/core';

import {
  HttpErrorResponse,
} from '@angular/common/http';

import {
  FormBuilder,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';

import {
  finalize,
} from 'rxjs';

import {
  Auth,
  AuthUser,
} from '../../../../core/auth/auth';


interface ApiErrorResponse {
  success: false;
  error: {
    code: string;
    message: string;
  };
}


@Component({
  selector: 'app-settings',
  imports: [
    ReactiveFormsModule,
  ],
  templateUrl: './settings.html',
  styleUrl: './settings.scss',
})
export class Settings implements OnInit {
  private readonly formBuilder = inject(FormBuilder);
  readonly auth = inject(Auth);

  profileLoading = false;
  passwordLoading = false;
  sessionsLoading = false;

  profileMessage: string | null = null;
  profileError: string | null = null;
  securityMessage: string | null = null;
  securityError: string | null = null;

  readonly profileForm =
    this.formBuilder.nonNullable.group({
      firstName: [
        '',
        [Validators.maxLength(100)],
      ],
      lastName: [
        '',
        [Validators.maxLength(100)],
      ],
      email: [
        '',
        [
          Validators.required,
          Validators.email,
          Validators.maxLength(255),
        ],
      ],
      company: [
        '',
        [Validators.maxLength(180)],
      ],
    });

  readonly passwordForm =
    this.formBuilder.nonNullable.group({
      currentPassword: [
        '',
        [
          Validators.required,
          Validators.minLength(9),
        ],
      ],
      newPassword: [
        '',
        [
          Validators.required,
          Validators.minLength(9),
        ],
      ],
      confirmPassword: [
        '',
        [
          Validators.required,
          Validators.minLength(9),
        ],
      ],
    });


  ngOnInit(): void {
    const currentUser = this.auth.currentUser();

    if (currentUser) {
      this.patchProfile(currentUser);
    }

    this.auth.loadCurrentUser().subscribe({
      next: (user) => this.patchProfile(user),
      error: () => {
        /* Le profil stocké reste affiché si le backend est indisponible. */
      },
    });
  }


  get passwordMismatch(): boolean {
    const values = this.passwordForm.getRawValue();

    return Boolean(
      values.confirmPassword &&
      values.newPassword !== values.confirmPassword,
    );
  }


  saveProfile(): void {
    this.profileMessage = null;
    this.profileError = null;
    this.profileForm.markAllAsTouched();

    if (this.profileForm.invalid || this.profileLoading) {
      return;
    }

    this.profileLoading = true;
    const values = this.profileForm.getRawValue();

    this.auth
      .updateProfile({
        email: values.email.trim().toLowerCase(),
        firstName: values.firstName.trim(),
        lastName: values.lastName.trim(),
        company: values.company.trim(),
      })
      .pipe(
        finalize(() => {
          this.profileLoading = false;
        }),
      )
      .subscribe({
        next: (user) => {
          this.patchProfile(user);
          this.profileMessage = 'Profil mis à jour avec succès.';
        },
        error: (error: HttpErrorResponse) => {
          this.profileError = this.resolveError(error);
        },
      });
  }


  changePassword(): void {
    this.securityMessage = null;
    this.securityError = null;
    this.passwordForm.markAllAsTouched();

    if (
      this.passwordForm.invalid ||
      this.passwordMismatch ||
      this.passwordLoading
    ) {
      if (this.passwordMismatch) {
        this.securityError =
          'La confirmation du nouveau mot de passe ne correspond pas.';
      }
      return;
    }

    this.passwordLoading = true;
    const values = this.passwordForm.getRawValue();

    this.auth
      .changePassword({
        currentPassword: values.currentPassword,
        newPassword: values.newPassword,
      })
      .pipe(
        finalize(() => {
          this.passwordLoading = false;
        }),
      )
      .subscribe({
        next: (message) => {
          this.passwordForm.reset();
          this.securityMessage = message;
        },
        error: (error: HttpErrorResponse) => {
          this.securityError = this.resolveError(error);
        },
      });
  }


  revokeOtherSessions(): void {
    if (this.sessionsLoading) {
      return;
    }

    this.securityMessage = null;
    this.securityError = null;
    this.sessionsLoading = true;

    this.auth
      .revokeOtherSessions()
      .pipe(
        finalize(() => {
          this.sessionsLoading = false;
        }),
      )
      .subscribe({
        next: (message) => {
          this.securityMessage = message;
        },
        error: (error: HttpErrorResponse) => {
          this.securityError = this.resolveError(error);
        },
      });
  }


  private patchProfile(
    user: AuthUser,
  ): void {
    this.profileForm.patchValue({
      firstName: user.firstName ?? '',
      lastName: user.lastName ?? '',
      email: user.email,
      company: user.company ?? '',
    });
  }


  private resolveError(
    error: HttpErrorResponse,
  ): string {
    const response =
      error.error as ApiErrorResponse | undefined;

    return response?.error?.message ??
      'Une erreur est survenue. Réessayez.';
  }
}
