import {
  computed,
  inject,
  Injectable,
  signal,
} from '@angular/core';

import {
  HttpClient,
  HttpHeaders,
} from '@angular/common/http';

import {
  Router,
} from '@angular/router';

import {
  finalize,
  map,
  Observable,
  tap,
} from 'rxjs';


export type AccountStatus =
  | 'pending'
  | 'active'
  | 'rejected'
  | 'suspended';


export interface AuthUser {
  id: number;
  username: string;
  email: string;
  firstName: string | null;
  lastName: string | null;
  company: string | null;
  status: AccountStatus;
  lastLoginAt: string | null;
  createdAt: string | null;
  roles: string[];
}


export interface LoginRequest {
  username: string;
  password: string;
  rememberMe: boolean;
}


export interface RegisterRequest {
  username: string;
  email: string;
  firstName: string;
  lastName: string;
  company: string;
  password: string;
}


export interface RegisterResult {
  message: string;
  user: {
    id: number;
    username: string;
    email: string;
    status: AccountStatus;
    createdAt: string;
  };
}


export interface ProfileUpdateRequest {
  email: string;
  firstName: string;
  lastName: string;
  company: string;
}


export interface PasswordChangeRequest {
  currentPassword: string;
  newPassword: string;
}


interface LoginData {
  accessToken: string;
  tokenType: string;
  expiresAt: string;
  user: AuthUser;
}


interface LoginResponse {
  success: boolean;
  data: LoginData;
}


interface RegisterResponse {
  success: boolean;
  data: RegisterResult;
}


interface CurrentUserResponse {
  success: boolean;
  data: {
    user: AuthUser;
  };
}


interface ProfileResponse {
  success: boolean;
  data: {
    user: AuthUser;
    message: string;
  };
}


interface MessageResponse {
  success: boolean;
  data: {
    message: string;
    revokedSessions?: number;
  };
}


@Injectable({
  providedIn: 'root',
})
export class Auth {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  private readonly tokenStorageKey =
    'piximind_access_token';

  private readonly userStorageKey =
    'piximind_current_user';

  private readonly tokenSignal = signal<string | null>(
    this.readStoredToken(),
  );

  readonly currentUser = signal<AuthUser | null>(
    this.readStoredUser(),
  );

  readonly isAuthenticated = computed(
    () => this.tokenSignal() !== null,
  );

  readonly isAdmin = computed(
    () => this.currentUser()?.roles.includes('admin') ?? false,
  );


  login(
    credentials: LoginRequest,
  ): Observable<AuthUser> {
    return this.http
      .post<LoginResponse>(
        '/api/auth/login',
        credentials,
      )
      .pipe(
        tap((response) => {
          this.saveSession(
            response.data.accessToken,
            response.data.user,
            credentials.rememberMe,
          );
        }),

        map((response) => response.data.user),
      );
  }


  register(
    registration: RegisterRequest,
  ): Observable<RegisterResult> {
    return this.http
      .post<RegisterResponse>(
        '/api/auth/register',
        registration,
      )
      .pipe(
        map((response) => response.data),
      );
  }


  loadCurrentUser(): Observable<AuthUser> {
    return this.http
      .get<CurrentUserResponse>(
        '/api/auth/me',
        {
          headers: this.authorizationHeaders(),
        },
      )
      .pipe(
        map((response) => response.data.user),

        tap((user) => {
          this.currentUser.set(user);
          this.updateStoredUser(user);
        }),
      );
  }


  updateProfile(
    profile: ProfileUpdateRequest,
  ): Observable<AuthUser> {
    return this.http
      .put<ProfileResponse>(
        '/api/auth/profile',
        profile,
        {
          headers: this.authorizationHeaders(),
        },
      )
      .pipe(
        map((response) => response.data.user),

        tap((user) => {
          this.currentUser.set(user);
          this.updateStoredUser(user);
        }),
      );
  }


  changePassword(
    password: PasswordChangeRequest,
  ): Observable<string> {
    return this.http
      .post<MessageResponse>(
        '/api/auth/change-password',
        password,
        {
          headers: this.authorizationHeaders(),
        },
      )
      .pipe(
        map((response) => response.data.message),
      );
  }


  revokeOtherSessions(): Observable<string> {
    return this.http
      .post<MessageResponse>(
        '/api/auth/sessions/revoke-others',
        {},
        {
          headers: this.authorizationHeaders(),
        },
      )
      .pipe(
        map((response) => response.data.message),
      );
  }


  logout(): void {
    const token = this.getAccessToken();

    if (!token) {
      this.clearSession();
      this.router.navigate(['/login']);
      return;
    }

    this.http
      .post(
        '/api/auth/logout',
        {},
        {
          headers: this.authorizationHeaders(),
        },
      )
      .pipe(
        finalize(() => {
          this.clearSession();
          this.router.navigate(['/login']);
        }),
      )
      .subscribe({
        error: () => {
          /*
           * Même lorsque Flask est inaccessible,
           * nous supprimons la session locale.
           */
        },
      });
  }


  getAccessToken(): string | null {
    return this.tokenSignal();
  }


  hasRole(
    role: string,
  ): boolean {
    return this.currentUser()?.roles.includes(role) ?? false;
  }


  private authorizationHeaders(): HttpHeaders {
    const token = this.getAccessToken();

    if (!token) {
      return new HttpHeaders();
    }

    return new HttpHeaders({
      Authorization: `Bearer ${token}`,
    });
  }


  private saveSession(
    token: string,
    user: AuthUser,
    rememberMe: boolean,
  ): void {
    this.clearBrowserStorage();

    const storage = rememberMe
      ? localStorage
      : sessionStorage;

    storage.setItem(
      this.tokenStorageKey,
      token,
    );

    storage.setItem(
      this.userStorageKey,
      JSON.stringify(user),
    );

    this.tokenSignal.set(token);
    this.currentUser.set(user);
  }


  private clearSession(): void {
    this.clearBrowserStorage();

    this.tokenSignal.set(null);
    this.currentUser.set(null);
  }


  private clearBrowserStorage(): void {
    localStorage.removeItem(
      this.tokenStorageKey,
    );

    localStorage.removeItem(
      this.userStorageKey,
    );

    sessionStorage.removeItem(
      this.tokenStorageKey,
    );

    sessionStorage.removeItem(
      this.userStorageKey,
    );
  }


  private readStoredToken(): string | null {
    return (
      localStorage.getItem(
        this.tokenStorageKey,
      ) ??
      sessionStorage.getItem(
        this.tokenStorageKey,
      )
    );
  }


  private readStoredUser(): AuthUser | null {
    const serializedUser =
      localStorage.getItem(
        this.userStorageKey,
      ) ??
      sessionStorage.getItem(
        this.userStorageKey,
      );

    if (!serializedUser) {
      return null;
    }

    try {
      return JSON.parse(
        serializedUser,
      ) as AuthUser;
    } catch {
      this.clearBrowserStorage();
      return null;
    }
  }


  private updateStoredUser(
    user: AuthUser,
  ): void {
    const serializedUser =
      JSON.stringify(user);

    if (
      localStorage.getItem(
        this.tokenStorageKey,
      )
    ) {
      localStorage.setItem(
        this.userStorageKey,
        serializedUser,
      );

      return;
    }

    sessionStorage.setItem(
      this.userStorageKey,
      serializedUser,
    );
  }
}
