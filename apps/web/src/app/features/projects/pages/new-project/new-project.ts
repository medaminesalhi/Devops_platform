import {
  Component,
  OnInit,
  computed,
  inject,
  signal,
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
  Router,
} from '@angular/router';

import {
  finalize,
} from 'rxjs';

import {
  CreateProjectRequest,
  CredentialSource,
  GitTokenType,
  GitTransport,
  ProjectEnvironmentOption,
  ProjectOptions,
  ProjectsService,
  RepositoryVisibility,
  SourceAuthMethod,
  SourceValidationResult,
} from '../../../../core/projects/projects';


interface ApiErrorResponse {
  success: false;

  error: {
    code: string;
    message: string;
  };
}


@Component({
  selector: 'app-new-project',

  imports: [
    ReactiveFormsModule,
  ],

  templateUrl: './new-project.html',
  styleUrl: './new-project.scss',
})
export class NewProject implements OnInit {
  private readonly projectsService =
    inject(ProjectsService);

  private readonly formBuilder =
    inject(FormBuilder);

  private readonly router =
    inject(Router);


  readonly options =
    signal<ProjectOptions | null>(null);

  readonly visibility =
    signal<RepositoryVisibility>('private');

  readonly transport =
    signal<GitTransport>('https');

  readonly credentialSource =
    signal<CredentialSource>('integration');

  readonly authMethod =
    signal<SourceAuthMethod>('https_token');

  readonly selectedEnvironmentIds =
    signal<number[]>([]);

  readonly defaultEnvironmentId =
    signal<number | null>(null);

  readonly sourceValidation =
    signal<SourceValidationResult | null>(
      null,
    );

  readonly isLoading =
    signal(true);

  readonly isTesting =
    signal(false);

  readonly isCreating =
    signal(false);

  readonly pageError =
    signal<string | null>(null);

  readonly sourceError =
    signal<string | null>(null);

  readonly sourceSuccess =
    signal<string | null>(null);

  readonly environmentError =
    signal<string | null>(null);

  readonly creationError =
    signal<string | null>(null);


  readonly form =
    this.formBuilder.nonNullable.group({
      name: [
        '',
        [
          Validators.required,
          Validators.minLength(3),
          Validators.maxLength(140),
        ],
      ],

      description: [
        '',
        [
          Validators.maxLength(1000),
        ],
      ],

      sourceConnectionId: [
        0,
        [
          Validators.required,
          Validators.min(1),
        ],
      ],

      repositoryUrl: [
        '',
        [
          Validators.required,
        ],
      ],

      branch: [
        'main',
        [
          Validators.required,
        ],
      ],

      sourceSubdirectory: [''],

      visibility:
        this.formBuilder.nonNullable.control<
          RepositoryVisibility
        >('private'),

      transport:
        this.formBuilder.nonNullable.control<
          GitTransport
        >('https'),

      credentialSource:
        this.formBuilder.nonNullable.control<
          CredentialSource
        >('integration'),

      authMethod:
        this.formBuilder.nonNullable.control<
          SourceAuthMethod
        >('https_token'),

      tokenType:
        this.formBuilder.nonNullable.control<
          GitTokenType
        >('project_access_token'),

      username: ['oauth2'],
      secret: [''],
    });


  readonly selectedConnection =
    computed(() => {
      const connectionId = Number(
        this.form.controls
          .sourceConnectionId.value,
      );

      return (
        this.options()
          ?.gitConnections
          .find(
            connection =>
              connection.id === connectionId,
          )
        ?? null
      );
    });


  readonly environments =
    computed<ProjectEnvironmentOption[]>(
      () => this.options()?.environments ?? [],
    );


  readonly integrationCredentialCompatible =
    computed(() => {
      const connection =
        this.selectedConnection();

      if (
        !connection
        || !connection.credentialConfigured
        || this.visibility() === 'public'
      ) {
        return false;
      }

      if (this.transport() === 'https') {
        return (
          connection.credentialAuthType
            === 'basic'
          || connection.credentialAuthType
            === 'token'
        );
      }

      return (
        connection.credentialAuthType
        === 'ssh_key'
      );
    });


  ngOnInit(): void {
    this.loadOptions();
  }


  loadOptions(): void {
    this.isLoading.set(true);

    this.projectsService
      .getOptions()
      .pipe(
        finalize(() => {
          this.isLoading.set(false);
        }),
      )
      .subscribe({
        next: options => {
          this.options.set(options);

          const first =
            options.gitConnections[0];

          if (first) {
            this.form.controls
              .sourceConnectionId
              .setValue(first.id);

            this.adjustCredentialSource();
          }
        },

        error: error => {
          this.pageError.set(
            this.resolveError(error),
          );
        },
      });
  }


  onConnectionChanged(): void {
    this.adjustCredentialSource();
    this.invalidateSource();
  }


  onVisibilityChanged(
    visibility: RepositoryVisibility,
  ): void {
    this.visibility.set(visibility);

    this.form.controls
      .visibility.setValue(visibility);

    if (visibility === 'public') {
      this.transport.set('https');

      this.credentialSource.set('none');

      this.authMethod.set('none');

      this.form.patchValue({
        transport: 'https',
        credentialSource: 'none',
        authMethod: 'none',
        username: '',
        secret: '',
      });
    } else {
      this.transport.set('https');

      this.authMethod.set('https_token');

      this.form.patchValue({
        transport: 'https',
        authMethod: 'https_token',
      });

      this.adjustCredentialSource();
    }

    this.invalidateSource();
  }


  onTransportChanged(
    transport: GitTransport,
  ): void {
    this.transport.set(transport);

    this.form.controls
      .transport.setValue(transport);

    if (transport === 'https') {
      this.authMethod.set('https_token');

      this.form.patchValue({
        authMethod: 'https_token',
        username: 'oauth2',
        secret: '',
      });
    } else {
      this.authMethod.set('ssh_key');

      this.form.patchValue({
        authMethod: 'ssh_key',
        username: '',
        secret: '',
      });
    }

    this.adjustCredentialSource();
    this.invalidateSource();
  }


  onCredentialSourceChanged(
    source: CredentialSource,
  ): void {
    this.credentialSource.set(source);

    this.form.controls
      .credentialSource.setValue(source);

    this.invalidateSource();
  }


  onAuthMethodChanged(
    method: SourceAuthMethod,
  ): void {
    this.authMethod.set(method);

    this.form.controls
      .authMethod.setValue(method);

    if (method === 'https_token') {
      this.form.controls
        .username.setValue('oauth2');
    } else if (
      method === 'https_password'
    ) {
      this.form.controls
        .username.setValue('');
    }

    this.form.controls
      .secret.setValue('');

    this.invalidateSource();
  }


  onTokenTypeChanged(): void {
    const tokenType =
      this.form.controls.tokenType.value;

    if (tokenType === 'deploy_token') {
      this.form.controls
        .username.setValue('');
    } else {
      this.form.controls
        .username.setValue('oauth2');
    }

    this.invalidateSource();
  }


  adjustCredentialSource(): void {
    if (this.visibility() === 'public') {
      return;
    }

    const selectedSource =
      this.integrationCredentialCompatible()
        ? 'integration'
        : 'project';

    this.credentialSource.set(
      selectedSource,
    );

    this.form.controls
      .credentialSource
      .setValue(selectedSource);
  }


  invalidateSource(): void {
    this.sourceValidation.set(null);
    this.sourceError.set(null);
    this.sourceSuccess.set(null);
  }


  validateSource(): void {
    const request =
      this.buildSourceRequest();

    if (!request) {
      return;
    }

    this.isTesting.set(true);
    this.sourceError.set(null);
    this.sourceSuccess.set(null);

    this.projectsService
      .validateSource(request)
      .pipe(
        finalize(() => {
          this.isTesting.set(false);
        }),
      )
      .subscribe({
        next: result => {
          this.sourceValidation.set(result);

          this.sourceSuccess.set(
            'Le repository et la branche sont accessibles.',
          );
        },

        error: error => {
          this.sourceError.set(
            this.resolveError(error),
          );
        },
      });
  }


  createProject(): void {
    this.creationError.set(null);
    this.environmentError.set(null);

    const sourceRequest =
      this.buildSourceRequest();

    if (!sourceRequest) {
      return;
    }

    if (!this.sourceValidation()) {
      this.creationError.set(
        (
          'Testez l’accès au repository '
          + 'avant de créer le projet.'
        ),
      );

      return;
    }

    if (
      this.selectedEnvironmentIds()
        .length === 0
    ) {
      this.environmentError.set(
        (
          'Sélectionnez au moins '
          + 'un environnement.'
        ),
      );

      return;
    }

    const defaultEnvironmentId =
      this.defaultEnvironmentId();

    if (defaultEnvironmentId === null) {
      this.environmentError.set(
        (
          'Sélectionnez un environnement '
          + 'par défaut.'
        ),
      );

      return;
    }

    const values =
      this.form.getRawValue();

    const request:
      CreateProjectRequest = {
        ...sourceRequest,

        name: values.name.trim(),

        description:
          values.description.trim()
          || null,

        allowedEnvironmentIds:
          this.selectedEnvironmentIds(),

        defaultEnvironmentId,
      };

    this.isCreating.set(true);

    this.projectsService
      .createProject(request)
      .pipe(
        finalize(() => {
          this.isCreating.set(false);
        }),
      )
      .subscribe({
        next: result => {
          void this.router.navigate([
            '/projects',
            result.project.id,
          ]);
        },

        error: error => {
          this.creationError.set(
            this.resolveError(error),
          );
        },
      });
  }


  buildSourceRequest():
    CreateProjectRequest | null {
    const values =
      this.form.getRawValue();

    if (
      Number(values.sourceConnectionId)
      <= 0
    ) {
      this.sourceError.set(
        'Sélectionnez le serveur GitLab.',
      );

      return null;
    }

    if (!values.repositoryUrl.trim()) {
      this.sourceError.set(
        'Saisissez l’URL de clonage Git.',
      );

      return null;
    }

    if (!values.branch.trim()) {
      this.sourceError.set(
        'Saisissez la branche Git.',
      );

      return null;
    }

    const visibility =
      this.visibility();

    const transport =
      visibility === 'public'
        ? 'https'
        : this.transport();

    const credentialSource =
      visibility === 'public'
        ? 'none'
        : this.credentialSource();

    let authMethod:
      SourceAuthMethod = 'none';

    let tokenType:
      GitTokenType | null = null;

    let username:
      string | null = null;

    let secret:
      string | null = null;

    if (
      visibility === 'private'
      && credentialSource === 'project'
    ) {
      authMethod =
        transport === 'ssh'
          ? 'ssh_key'
          : this.authMethod();

      username =
        values.username.trim()
        || null;

      secret =
        values.secret.trim()
        || null;

      if (
        authMethod === 'https_token'
      ) {
        tokenType =
          values.tokenType;
      }

      if (
        transport === 'https'
        && !username
      ) {
        this.sourceError.set(
          'Le username Git est obligatoire.',
        );

        return null;
      }

      if (!secret) {
        this.sourceError.set(
          (
            transport === 'ssh'
              ? 'La clé privée SSH est obligatoire.'
              : 'Le mot de passe ou le token est obligatoire.'
          ),
        );

        return null;
      }
    }

    return {
      sourceConnectionId:
        Number(values.sourceConnectionId),

      repositoryUrl:
        values.repositoryUrl.trim(),

      visibility,
      transport,

      credentialSource,
      authMethod,
      tokenType,
      username,
      secret,

      branch:
        values.branch.trim(),

      sourceSubdirectory:
        values.sourceSubdirectory.trim()
        || null,

      name: '',
      description: null,
      allowedEnvironmentIds: [],
      defaultEnvironmentId: 0,
    };
  }


  toggleEnvironment(
    environmentId: number,
    checked: boolean,
  ): void {
    this.environmentError.set(null);

    this.selectedEnvironmentIds.update(
      ids => {
        if (checked) {
          return ids.includes(environmentId)
            ? ids
            : [...ids, environmentId];
        }

        return ids.filter(
          id => id !== environmentId,
        );
      },
    );

    if (
      !checked
      && this.defaultEnvironmentId()
        === environmentId
    ) {
      this.defaultEnvironmentId.set(null);
    }
  }


  selectDefaultEnvironment(
    environmentId: number,
  ): void {
    if (
      !this.selectedEnvironmentIds()
        .includes(environmentId)
    ) {
      this.selectedEnvironmentIds.update(
        ids => [...ids, environmentId],
      );
    }

    this.defaultEnvironmentId.set(
      environmentId,
    );

    this.environmentError.set(null);
  }


  isEnvironmentSelected(
    environmentId: number,
  ): boolean {
    return this.selectedEnvironmentIds()
      .includes(environmentId);
  }


  credentialAuthLabel(): string {
    const connection =
      this.selectedConnection();

    if (!connection) {
      return 'Non configuré';
    }

    const labels:
      Record<string, string> = {
        basic:
          'Username et mot de passe',

        token:
          'Username et token',

        ssh_key:
          'Clé privée SSH',

        none:
          'Aucun',
      };

    return (
      labels[
        connection.credentialAuthType
      ]
      ?? connection.credentialAuthType
    );
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
      error.error as ApiErrorResponse | null;

    return (
      response?.error?.message
      || `Erreur HTTP ${error.status}.`
    );
  }
}