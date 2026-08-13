import {
  Component,
  inject,
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
  selector: 'app-register',
  imports: [
    ReactiveFormsModule,
    RouterLink,
  ],
  templateUrl: './register.html',
  styleUrl: './register.scss',
})
export class Register {
  private readonly formBuilder = inject(FormBuilder);
  private readonly auth = inject(Auth);

  isSubmitting = false;
  showPassword = false;
  showConfirmation = false;
  errorMessage: string | null = null;
  successMessage: string | null = null;

  readonly registerForm =
    this.formBuilder.nonNullable.group({
      firstName: [
        '',
        [
          Validators.maxLength(100),
        ],
      ],
      lastName: [
        '',
        [
          Validators.maxLength(100),
        ],
      ],
      username: [
        '',
        [
          Validators.required,
          Validators.minLength(3),
          Validators.maxLength(60),
          Validators.pattern(/^[A-Za-z0-9._-]+$/),
        ],
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
        [
          Validators.maxLength(180),
        ],
      ],
      password: [
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


  get passwordsMismatch(): boolean {
    const formValue = this.registerForm.getRawValue();

    return Boolean(
      formValue.confirmPassword &&
      formValue.password !== formValue.confirmPassword,
    );
  }


  submit(): void {
    this.errorMessage = null;
    this.successMessage = null;
    this.registerForm.markAllAsTouched();

    if (
      this.registerForm.invalid ||
      this.passwordsMismatch ||
      this.isSubmitting
    ) {
      if (this.passwordsMismatch) {
        this.errorMessage =
          'La confirmation du mot de passe ne correspond pas.';
      }
      return;
    }

    this.isSubmitting = true;
    const formValue = this.registerForm.getRawValue();

    this.auth
      .register({
        username: formValue.username.trim(),
        email: formValue.email.trim().toLowerCase(),
        firstName: formValue.firstName.trim(),
        lastName: formValue.lastName.trim(),
        company: formValue.company.trim(),
        password: formValue.password,
      })
      .pipe(
        finalize(() => {
          this.isSubmitting = false;
        }),
      )
      .subscribe({
        next: (result) => {
          this.successMessage = result.message;
          this.registerForm.reset();
        },
        error: (error: HttpErrorResponse) => {
          this.errorMessage = this.resolveError(error);
        },
      });
  }


  private resolveError(
    error: HttpErrorResponse,
  ): string {
    if (error.status === 0) {
      return 'Le backend Flask est inaccessible.';
    }

    const apiResponse =
      error.error as ApiErrorResponse | undefined;

    if (apiResponse?.error?.message) {
      return apiResponse.error.message;
    }

    return 'L’inscription n’a pas pu être enregistrée.';
  }
}
