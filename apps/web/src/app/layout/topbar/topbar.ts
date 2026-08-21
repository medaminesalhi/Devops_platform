import {
  Component,
  DestroyRef,
  EventEmitter,
  HostListener,
  Input,
  Output,
  inject,
  signal,
} from '@angular/core';

import {
  NavigationEnd,
  Router,
} from '@angular/router';

import {
  filter,
} from 'rxjs';

import {
  takeUntilDestroyed,
} from '@angular/core/rxjs-interop';

import {
  Auth,
} from '../../core/auth/auth';

import {
  NotificationsService,
  PlatformNotification,
} from '../../core/notifications/notifications';


interface PageInformation {
  title: string;
  subtitle: string;
  section: string;
}


@Component({
  selector: 'app-topbar',
  imports: [],
  templateUrl: './topbar.html',
  styleUrl: './topbar.scss',
})
export class Topbar {
  private readonly router = inject(Router);
  private readonly auth = inject(Auth);
  private readonly destroyRef = inject(DestroyRef);

  readonly notificationsService = inject(NotificationsService);

  @Input()
  sidebarOpen = true;

  @Output()
  readonly sidebarToggle = new EventEmitter<void>();

  readonly notificationsOpen = signal(false);

  readonly pageInformation = signal<PageInformation>({
    title: 'Vue générale',
    subtitle: 'Suivez l’activité de votre plateforme.',
    section: 'Dashboard',
  });

  readonly currentDate = new Intl.DateTimeFormat(
    'fr-FR',
    {
      weekday: 'short',
      day: '2-digit',
      month: 'short',
    },
  ).format(new Date());


  constructor() {
    this.updatePageInformation(this.router.url);

    this.router.events
      .pipe(
        filter(
          (event): event is NavigationEnd =>
            event instanceof NavigationEnd,
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((event) => {
        this.notificationsOpen.set(false);
        this.updatePageInformation(event.urlAfterRedirects);
      });
  }


  get displayName(): string {
    const user = this.auth.currentUser();

    if (!user) {
      return 'Utilisateur';
    }

    const fullName = [
      user.firstName,
      user.lastName,
    ]
      .filter(Boolean)
      .join(' ')
      .trim();

    return fullName || user.username;
  }


  get initials(): string {
    const user = this.auth.currentUser();

    if (!user) {
      return 'U';
    }

    const firstLetter =
      user.firstName?.charAt(0) ??
      user.username.charAt(0);

    const secondLetter =
      user.lastName?.charAt(0) ?? '';

    return (
      firstLetter + secondLetter
    ).toUpperCase();
  }


  get roleLabel(): string {
    const roles = this.auth.currentUser()?.roles ?? [];

    if (roles.includes('admin')) {
      return 'Administrateur';
    }

    if (roles.includes('devops')) {
      return 'DevOps';
    }

    if (roles.includes('developer')) {
      return 'Développeur';
    }

    if (roles.includes('viewer')) {
      return 'Lecteur';
    }

    return 'Utilisateur';
  }


  @HostListener('document:keydown.escape')
  closeNotificationsWithEscape(): void {
    this.notificationsOpen.set(false);
  }


  toggleSidebar(): void {
    this.sidebarToggle.emit();
  }


  toggleNotifications(): void {
    const nextValue = !this.notificationsOpen();
    this.notificationsOpen.set(nextValue);

    if (nextValue) {
      this.notificationsService.refresh();
    }
  }


  markAllNotificationsAsRead(): void {
    if (this.notificationsService.unreadCount() === 0) {
      return;
    }

    this.notificationsService
      .markAllAsRead()
      .subscribe();
  }


  openNotification(
    notification: PlatformNotification,
  ): void {
    const navigate = () => {
      this.notificationsOpen.set(false);

      if (notification.actionUrl) {
        this.router.navigateByUrl(notification.actionUrl);
      }
    };

    if (notification.readAt) {
      navigate();
      return;
    }

    this.notificationsService
      .markAsRead(notification.id)
      .subscribe({
        next: navigate,
        error: navigate,
      });
  }


  notificationIcon(
    notification: PlatformNotification,
  ): string {
    if (notification.severity === 'critical') {
      return '!';
    }

    if (notification.severity === 'warning') {
      return '!';
    }

    if (notification.severity === 'success') {
      return '✓';
    }

    return 'i';
  }


  notificationTimeLabel(
    createdAt: string,
  ): string {
    const createdTime = new Date(createdAt).getTime();

    if (Number.isNaN(createdTime)) {
      return '';
    }

    const differenceSeconds = Math.max(
      0,
      Math.floor((Date.now() - createdTime) / 1000),
    );

    if (differenceSeconds < 60) {
      return 'À l’instant';
    }

    const minutes = Math.floor(differenceSeconds / 60);

    if (minutes < 60) {
      return `Il y a ${minutes} min`;
    }

    const hours = Math.floor(minutes / 60);

    if (hours < 24) {
      return `Il y a ${hours} h`;
    }

    const days = Math.floor(hours / 24);

    if (days < 7) {
      return `Il y a ${days} j`;
    }

    return new Intl.DateTimeFormat(
      'fr-FR',
      {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
      },
    ).format(new Date(createdAt));
  }


  logout(): void {
    this.auth.logout();
  }


  private updatePageInformation(
    url: string,
  ): void {
    if (url.startsWith('/projects/new')) {
      this.pageInformation.set({
        title: 'Nouveau projet',
        subtitle: 'Analysez et préparez une application.',
        section: 'Projets',
      });
      return;
    }

    if (url.startsWith('/performance')) {
      this.pageInformation.set({
        title: 'Performance',
        subtitle: 'Validez la charge avec k6 et, si besoin, Grafana + Prometheus.',
        section: 'Validation',
      });
      return;
    }

    if (url.startsWith('/infrastructure')) {
      this.pageInformation.set({
        title: 'Infrastructure',
        subtitle: 'Gérez les environnements de déploiement.',
        section: 'Supervision',
      });
      return;
    }

    if (url.startsWith('/projects')) {
      this.pageInformation.set({
        title: 'Projets',
        subtitle: 'Gérez les applications de la plateforme.',
        section: 'Espace de travail',
      });
      return;
    }

    if (url.startsWith('/integrations')) {
      this.pageInformation.set({
        title: 'Intégrations',
        subtitle: 'Configurez les services externes.',
        section: 'Configuration',
      });
      return;
    }

    if (url.startsWith('/settings')) {
      this.pageInformation.set({
        title: 'Paramètres',
        subtitle: 'Gérez votre profil et votre sécurité.',
        section: 'Compte',
      });
      return;
    }

    if (url.startsWith('/admin')) {
      this.pageInformation.set({
        title: 'Administration',
        subtitle: 'Gérez les comptes, rôles et activités utilisateurs.',
        section: 'Administration',
      });
      return;
    }

    if (url.startsWith('/deployments')) {
      this.pageInformation.set({
        title: 'Déploiements',
        subtitle: 'Consultez les exécutions et leurs statuts.',
        section: 'Exécution',
      });
      return;
    }

    this.pageInformation.set({
      title: 'Vue générale',
      subtitle: 'Suivez l’activité de votre plateforme.',
      section: 'Dashboard',
    });
  }
}
