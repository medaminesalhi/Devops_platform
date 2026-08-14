import {
  DestroyRef,
  Injectable,
  inject,
  signal,
} from '@angular/core';

import {
  HttpClient,
  HttpHeaders,
} from '@angular/common/http';

import {
  catchError,
  EMPTY,
  map,
  Observable,
  switchMap,
  tap,
  timer,
} from 'rxjs';

import {
  takeUntilDestroyed,
} from '@angular/core/rxjs-interop';

import {
  Auth,
} from '../auth/auth';


export type NotificationSeverity =
  | 'info'
  | 'warning'
  | 'critical'
  | 'success';


export interface PlatformNotification {
  id: number;
  type: string;
  severity: NotificationSeverity;
  title: string;
  message: string;
  connectionId: number | null;
  projectId: number | null;
  deploymentId: number | null;
  environmentId: number | null;
  resourceType: string | null;
  resourceId: number | null;
  actionUrl: string | null;
  metadata: Record<string, unknown>;
  readAt: string | null;
  resolvedAt: string | null;
  createdAt: string;
}


interface NotificationsResponse {
  success: boolean;
  data: {
    notifications: PlatformNotification[];
    unreadCount: number;
  };
}


interface ReadNotificationResponse {
  success: boolean;
  data: {
    notification: PlatformNotification;
    unreadCount: number;
  };
}


interface ReadAllResponse {
  success: boolean;
  data: {
    updatedCount: number;
    unreadCount: number;
  };
}


@Injectable({
  providedIn: 'root',
})
export class NotificationsService {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(Auth);
  private readonly destroyRef = inject(DestroyRef);

  readonly notifications = signal<PlatformNotification[]>([]);
  readonly unreadCount = signal(0);
  readonly isLoading = signal(false);


  constructor() {
    timer(0, 15_000)
      .pipe(
        switchMap(() => {
          if (!this.auth.getAccessToken()) {
            return EMPTY;
          }

          return this.fetchNotifications();
        }),
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe();
  }


  refresh(): void {
    if (!this.auth.getAccessToken()) {
      this.notifications.set([]);
      this.unreadCount.set(0);
      return;
    }

    this.fetchNotifications()
      .pipe(
        catchError(() => EMPTY),
      )
      .subscribe();
  }


  markAsRead(
    notificationId: number,
  ): Observable<PlatformNotification> {
    return this.http
      .post<ReadNotificationResponse>(
        `/api/notifications/${notificationId}/read`,
        {},
        {
          headers: this.authorizationHeaders(),
        },
      )
      .pipe(
        map((response) => response.data),
        tap((data) => {
          this.unreadCount.set(data.unreadCount);

          this.notifications.update((items) =>
            items.map((item) =>
              item.id === data.notification.id
                ? data.notification
                : item,
            ),
          );
        }),
        map((data) => data.notification),
      );
  }


  markAllAsRead(): Observable<number> {
    return this.http
      .post<ReadAllResponse>(
        '/api/notifications/read-all',
        {},
        {
          headers: this.authorizationHeaders(),
        },
      )
      .pipe(
        map((response) => response.data),
        tap((data) => {
          this.unreadCount.set(data.unreadCount);
          const now = new Date().toISOString();

          this.notifications.update((items) =>
            items.map((item) => ({
              ...item,
              readAt: item.readAt ?? now,
            })),
          );
        }),
        map((data) => data.updatedCount),
      );
  }


  private fetchNotifications(): Observable<PlatformNotification[]> {
    this.isLoading.set(true);

    return this.http
      .get<NotificationsResponse>(
        '/api/notifications?limit=20',
        {
          headers: this.authorizationHeaders(),
        },
      )
      .pipe(
        map((response) => response.data),
        tap((data) => {
          this.notifications.set(data.notifications);
          this.unreadCount.set(data.unreadCount);
          this.isLoading.set(false);
        }),
        map((data) => data.notifications),
        catchError((error) => {
          this.isLoading.set(false);
          throw error;
        }),
      );
  }


  private authorizationHeaders(): HttpHeaders {
    const token = this.auth.getAccessToken();

    return token
      ? new HttpHeaders({
          Authorization: `Bearer ${token}`,
        })
      : new HttpHeaders();
  }
}
