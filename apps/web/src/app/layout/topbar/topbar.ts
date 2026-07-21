import {
  Component,
  DestroyRef,
  EventEmitter,
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

  @Input()
  sidebarOpen = true;

  @Output()
  readonly sidebarToggle =
    new EventEmitter<void>();

  readonly pageInformation =
    signal<PageInformation>({
      title: 'Vue générale',
      subtitle:
        'Suivez l’activité de votre plateforme.',
      section: 'Dashboard',
    });

  readonly currentDate =
    new Intl.DateTimeFormat(
      'fr-FR',
      {
        weekday: 'short',
        day: '2-digit',
        month: 'short',
      },
    ).format(new Date());


  constructor() {
    this.updatePageInformation(
      this.router.url,
    );

    this.router.events
      .pipe(
        filter(
          (
            event,
          ): event is NavigationEnd =>
            event instanceof NavigationEnd,
        ),

        takeUntilDestroyed(
          this.destroyRef,
        ),
      )
      .subscribe((event) => {
        this.updatePageInformation(
          event.urlAfterRedirects,
        );
      });
  }


  get displayName(): string {
    const user =
      this.auth.currentUser();

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
    const user =
      this.auth.currentUser();

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
    const roles =
      this.auth.currentUser()?.roles ?? [];

    if (roles.includes('admin')) {
      return 'Administrateur';
    }

    if (roles.includes('devops')) {
      return 'DevOps';
    }

    if (roles.includes('developer')) {
      return 'Développeur';
    }

    return 'Utilisateur';
  }


  toggleSidebar(): void {
    this.sidebarToggle.emit();
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
        subtitle:
          'Analysez et préparez une application.',
        section: 'Projets',
      });

      return;
    }

    if (url.startsWith('/infrastructure')) {
      this.pageInformation.set({
        title: 'Infrastructure',
        subtitle:
          'Gérez les environnements de déploiement.',
        section: 'Supervision',
      });

      return;
    }
    
    if (url.startsWith('/projects')) {
      this.pageInformation.set({
        title: 'Projets',
        subtitle:
          'Gérez les applications de la plateforme.',
        section: 'Espace de travail',
      });

      return;
    }

    if (url.startsWith('/integrations')) {
      this.pageInformation.set({
        title: 'Intégrations',
        subtitle:
          'Configurez les services externes.',
        section: 'Configuration',
      });

      return;
    }

    if (url.startsWith('/deployments')) {
      this.pageInformation.set({
        title: 'Déploiements',
        subtitle:
          'Consultez les exécutions et leurs statuts.',
        section: 'Exécution',
      });

      return;
    }

    this.pageInformation.set({
      title: 'Vue générale',
      subtitle:
        'Suivez l’activité de votre plateforme.',
      section: 'Dashboard',
    });
  }
}