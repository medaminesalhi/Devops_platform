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


export interface AuthUser {
  id: number;
  username: string;
  email: string;
  firstName: string | null;
  lastName: string | null;
  roles: string[];
}


export interface LoginRequest {
  username: string;
  password: string;
  rememberMe: boolean;
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


interface CurrentUserResponse {
  success: boolean;
  data: {
    user: AuthUser;
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


  loadCurrentUser(): Observable<AuthUser> {
    const token = this.getAccessToken();

    if (!token) {
      throw new Error(
        'Aucun token de session disponible.',
      );
    }

    const headers = new HttpHeaders({
      Authorization: `Bearer ${token}`,
    });

    return this.http
      .get<CurrentUserResponse>(
        '/api/auth/me',
        {
          headers,
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


  logout(): void {
    const token = this.getAccessToken();

    if (!token) {
      this.clearSession();
      this.router.navigate(['/login']);
      return;
    }

    const headers = new HttpHeaders({
      Authorization: `Bearer ${token}`,
    });

    this.http
      .post(
        '/api/auth/logout',
        {},
        {
          headers,
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


  private saveSession(
    token: string,
    user: AuthUser,
    rememberMe: boolean,
  ): void {
    /*
     * Si rememberMe est coché :
     * localStorage conserve la session après
     * fermeture du navigateur.
     *
     * Sinon :
     * sessionStorage conserve la session uniquement
     * pendant l’onglet actuel.
     */

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