import {
  Component,
  inject,
} from '@angular/core';

import {
  ReactiveFormsModule,
  FormBuilder,
  Validators,
} from '@angular/forms';

import {
  HttpErrorResponse,
} from '@angular/common/http';

import {
  Router,
  RouterLink,
} from '@angular/router';

import {
  finalize,
} from 'rxjs';

import {
  Auth,
} from '../../../../core/auth/auth';


interface ApiErrorResponse {
  success: false;
  error: {
    code: string;
    message: string;
  };
}


@Component({
  selector: 'app-login',
  imports: [
    ReactiveFormsModule,
    RouterLink,
  ],
  templateUrl: './login.html',
  styleUrl: './login.scss',
})
export class Login {
  private readonly formBuilder = inject(FormBuilder);
  private readonly auth = inject(Auth);
  private readonly router = inject(Router);

  showPassword = false;
  isSubmitting = false;
  loginError: string | null = null;


  readonly loginForm =
    this.formBuilder.nonNullable.group({
      username: [
        '',
        [
          Validators.required,
          Validators.minLength(3),
          Validators.maxLength(60),
        ],
      ],

      password: [
        '',
        [
          Validators.required,
          Validators.minLength(9),
        ],
      ],

      rememberMe: [
        false,
      ],
    });


  /*
   * Ces deux getters permettent de conserver
   * la compatibilité avec votre HTML actuel
   * s’il utilise isLoading ou errorMessage.
   */

  get isLoading(): boolean {
    return this.isSubmitting;
  }


  get errorMessage(): string | null {
    return this.loginError;
  }


  togglePassword(): void {
    this.showPassword = !this.showPassword;
  }


  togglePasswordVisibility(): void {
    this.togglePassword();
  }


  submit(): void {
    this.loginError = null;

    this.loginForm.markAllAsTouched();

    if (
      this.loginForm.invalid ||
      this.isSubmitting
    ) {
      return;
    }

    this.isSubmitting = true;

    const credentials =
      this.loginForm.getRawValue();

    this.auth
      .login(credentials)
      .pipe(
        finalize(() => {
          this.isSubmitting = false;
        }),
      )
      .subscribe({
        next: () => {
          this.router.navigate([
            '/dashboard',
          ]);
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.loginError =
            this.resolveLoginError(error);
        },
      });
  }


  /*
   * Alias utile si votre HTML utilise encore
   * (ngSubmit)="onSubmit()".
   */

  onSubmit(): void {
    this.submit();
  }


  private resolveLoginError(
    error: HttpErrorResponse,
  ): string {
    if (error.status === 0) {
      return (
        'Le backend Flask est inaccessible. ' +
        'Vérifiez qu’il fonctionne sur le port 5000.'
      );
    }

    const apiResponse =
      error.error as ApiErrorResponse | undefined;

    if (
      apiResponse?.error?.message
    ) {
      return apiResponse.error.message;
    }

    if (error.status === 401) {
      return (
        'Nom d’utilisateur ou mot de passe incorrect.'
      );
    }

    if (error.status === 403) {
      return (
        'Ce compte utilisateur est désactivé.'
      );
    }

    return (
      'Une erreur est survenue pendant la connexion.'
    );
  }
}