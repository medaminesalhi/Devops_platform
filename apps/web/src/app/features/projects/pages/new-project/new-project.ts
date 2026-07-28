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
  RouterLink,
} from '@angular/router';

import {
  finalize,
} from 'rxjs';

import {
  CreateGitProjectRequest,
  CredentialSource,
  GitTokenType,
  GitTransport,
  ProjectEnvironmentOption,
  ProjectOperationMode,
  ProjectOptions,
  ProjectSourceType,
  ProjectsService,
  RepositoryVisibility,
  SourceAuthMethod,
  SourceValidationResult,
  ValidateGitSourceRequest,
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
    RouterLink,
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

  readonly selectedConnectionId =
    signal(0);

  readonly operationMode =
    signal<ProjectOperationMode>(
      'new_application',
    );

  readonly sourceType =
    signal<ProjectSourceType>('git');

  readonly visibility =
    signal<RepositoryVisibility>('public');

  readonly transport =
    signal<GitTransport>('https');

  readonly credentialSource =
    signal<CredentialSource>('none');

  readonly authMethod =
    signal<SourceAuthMethod>(
      'https_token',
    );

  readonly selectedEnvironmentId =
    signal<number | null>(null);

  readonly selectedArchiveFile =
    signal<File | null>(null);

  readonly sourceValidation =
    signal<SourceValidationResult | null>(
      null,
    );

  readonly isLoading = signal(true);
  readonly isTesting = signal(false);
  readonly isCreating = signal(false);

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
      operationMode:
        this.formBuilder.nonNullable.control<
          ProjectOperationMode
        >('new_application'),

      sourceType:
        this.formBuilder.nonNullable.control<
          ProjectSourceType
        >('git'),

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

      sourceConnectionId: [0],

      repositoryUrl: [''],

      branch: ['main'],

      sourceSubdirectory: [''],

      visibility:
        this.formBuilder.nonNullable.control<
          RepositoryVisibility
        >('public'),

      transport:
        this.formBuilder.nonNullable.control<
          GitTransport
        >('https'),

      credentialSource:
        this.formBuilder.nonNullable.control<
          CredentialSource
        >('none'),

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
      const connectionId =
        this.selectedConnectionId();

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
      () =>
        this.options()?.environments
        ?? [],
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
    this.applySourceValidators();
    this.loadOptions();
  }


  loadOptions(): void {
    this.isLoading.set(true);
    this.pageError.set(null);

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

          const firstConnection =
            options.gitConnections[0];

          if (firstConnection) {
            this.form.controls
              .sourceConnectionId
              .setValue(firstConnection.id);

            this.selectedConnectionId.set(
              firstConnection.id,
            );
          }

          this.applySourceValidators();
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.pageError.set(
            this.resolveError(error),
          );
        },
      });
  }


  selectOperationMode(
    mode: ProjectOperationMode,
  ): void {
    this.operationMode.set(mode);

    this.form.controls
      .operationMode.setValue(mode);
  }


  selectSourceType(
    type: ProjectSourceType,
  ): void {
    this.sourceType.set(type);

    this.form.controls
      .sourceType.setValue(type);

    if (type === 'git') {
      this.visibility.set('public');
      this.transport.set('https');
      this.credentialSource.set('none');

      this.form.patchValue({
        visibility: 'public',
        transport: 'https',
        credentialSource: 'none',
      });
    }

    this.applySourceValidators();
    this.invalidateSource();
  }


  onConnectionChanged(): void {
    this.selectedConnectionId.set(
      Number(
        this.form.controls
          .sourceConnectionId.value,
      ),
    );

    if (
      this.visibility() === 'private'
    ) {
      this.adjustCredentialSource();
    }

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
        username: 'oauth2',
        secret: '',
      });

      this.adjustCredentialSource();
    }

    this.applySourceValidators();
    this.invalidateSource();
  }


  onTransportChanged(
    transport: GitTransport,
  ): void {
    this.transport.set(transport);

    this.form.controls
      .transport.setValue(transport);

    if (transport === 'https') {
      this.authMethod.set(
        'https_token',
      );

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
    this.applySourceValidators();
    this.invalidateSource();
  }


  onCredentialSourceChanged(
    source: CredentialSource,
  ): void {
    this.credentialSource.set(source);

    this.form.controls
      .credentialSource.setValue(source);

    this.applySourceValidators();
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

    this.applySourceValidators();
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


  onArchiveSelected(
    event: Event,
  ): void {
    const input =
      event.target as HTMLInputElement;

    const file =
      input.files?.item(0) ?? null;

    this.selectedArchiveFile.set(file);

    this.invalidateSource();

    if (!file) {
      return;
    }

    if (
      !file.name
        .toLowerCase()
        .endsWith('.zip')
    ) {
      this.sourceError.set(
        (
          'Sélectionnez un fichier '
          + 'avec l’extension .zip.'
        ),
      );

      return;
    }

    const maximumBytes =
      this.options()
        ?.archiveLimits.maxBytes;

    if (
      maximumBytes
      && file.size > maximumBytes
    ) {
      this.sourceError.set(
        (
          'L’archive dépasse la limite de '
          + `${this.options()
            ?.archiveLimits.maxMegabytes} Mo.`
        ),
      );
    }
  }


  selectEnvironment(
    environmentId: number,
  ): void {
    this.selectedEnvironmentId.set(
      environmentId,
    );

    this.environmentError.set(null);
  }


  adjustCredentialSource(): void {
    if (this.visibility() === 'public') {
      this.credentialSource.set('none');

      this.form.controls
        .credentialSource.setValue('none');

      return;
    }

    const source: CredentialSource =
      this.integrationCredentialCompatible()
        ? 'integration'
        : 'project';

    this.credentialSource.set(source);

    this.form.controls
      .credentialSource.setValue(source);
  }


  invalidateSource(): void {
    this.sourceValidation.set(null);
    this.sourceError.set(null);
    this.sourceSuccess.set(null);
  }


  validateSource(): void {
    this.sourceError.set(null);
    this.sourceSuccess.set(null);

    const request =
      this.buildValidationRequest();

    if (!request) {
      return;
    }

    this.isTesting.set(true);

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
            result.sourceType === 'git'
              ? (
                  'Le repository et la branche '
                  + 'sont accessibles.'
                )
              : (
                  'L’archive ZIP est valide '
                  + 'et prête pour l’analyse.'
                ),
          );
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.sourceError.set(
            this.resolveError(error),
          );
        },
      });
  }


  createProject(): void {
    this.form.controls
      .name.markAsTouched();

    this.creationError.set(null);
    this.environmentError.set(null);

    if (this.form.controls.name.invalid) {
      this.creationError.set(
        'Saisissez un nom de projet valide.',
      );

      return;
    }

    if (!this.sourceValidation()) {
      this.creationError.set(
        (
          'Vérifiez la source avant '
          + 'de créer le projet.'
        ),
      );

      return;
    }

    const environmentId =
      this.selectedEnvironmentId();

    if (environmentId === null) {
      this.environmentError.set(
        'Sélectionnez un environnement.',
      );

      return;
    }

    const request =
      this.buildCreationRequest(
        environmentId,
      );

    if (!request) {
      return;
    }

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

        error: (
          error: HttpErrorResponse,
        ) => {
          this.creationError.set(
            this.resolveError(error),
          );
        },
      });
  }


  canCreate(): boolean {
    return (
      this.form.controls.name.valid
      && this.sourceValidation() !== null
      && this.selectedEnvironmentId()
        !== null
      && !this.isCreating()
    );
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
          'Aucun credential',
      };

    return (
      labels[
        connection.credentialAuthType
      ]
      ?? connection.credentialAuthType
    );
  }


  formatBytes(
    value: number | null | undefined,
  ): string {
    if (!value) {
      return '0 o';
    }

    if (value < 1024) {
      return `${value} o`;
    }

    if (value < 1024 * 1024) {
      return (
        `${(value / 1024).toFixed(1)} Ko`
      );
    }

    return (
      `${(
        value
        / 1024
        / 1024
      ).toFixed(1)} Mo`
    );
  }


  private buildValidationRequest():
    ValidateGitSourceRequest
    | FormData
    | null {
    if (this.sourceType() === 'zip') {
      return this.buildZipFormData(false);
    }

    return this.buildGitSourceRequest();
  }


  private buildCreationRequest(
    environmentId: number,
  ): CreateGitProjectRequest
    | FormData
    | null {
    const values =
      this.form.getRawValue();

    if (this.sourceType() === 'zip') {
      return this.buildZipFormData(
        true,
        environmentId,
      );
    }

    const sourceRequest =
      this.buildGitSourceRequest();

    if (!sourceRequest) {
      return null;
    }

    return {
      ...sourceRequest,

      operationMode:
        this.operationMode(),

      name:
        values.name.trim(),

      description:
        values.description.trim()
        || null,

      environmentId,
    };
  }


  private buildGitSourceRequest():
    ValidateGitSourceRequest | null {
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

    const transport: GitTransport =
      visibility === 'public'
        ? 'https'
        : this.transport();

    const credentialSource:
      CredentialSource =
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
          transport === 'ssh'
            ? (
                'La clé privée SSH '
                + 'est obligatoire.'
              )
            : (
                'Le mot de passe ou le token '
                + 'est obligatoire.'
              ),
        );

        return null;
      }
    }

    return {
      sourceType: 'git',

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
    };
  }


  private buildZipFormData(
    includeProject: boolean,
    environmentId?: number,
  ): FormData | null {
    const archiveFile =
      this.selectedArchiveFile();

    if (!archiveFile) {
      this.sourceError.set(
        'Sélectionnez une archive ZIP.',
      );

      return null;
    }

    if (
      !archiveFile.name
        .toLowerCase()
        .endsWith('.zip')
    ) {
      this.sourceError.set(
        'Seuls les fichiers .zip sont acceptés.',
      );

      return null;
    }

    const maximumBytes =
      this.options()
        ?.archiveLimits.maxBytes;

    if (
      maximumBytes
      && archiveFile.size > maximumBytes
    ) {
      this.sourceError.set(
        (
          'L’archive dépasse la limite de '
          + `${this.options()
            ?.archiveLimits.maxMegabytes} Mo.`
        ),
      );

      return null;
    }

    const values =
      this.form.getRawValue();

    const payload:
      Record<string, unknown> = {
        sourceType: 'zip',

        sourceSubdirectory:
          values.sourceSubdirectory.trim()
          || null,
      };

    if (includeProject) {
      payload['operationMode'] =
        this.operationMode();

      payload['name'] =
        values.name.trim();

      payload['description'] =
        values.description.trim()
        || null;

      payload['environmentId'] =
        environmentId;
    }

    const formData =
      new FormData();

    formData.append(
      'payload',
      JSON.stringify(payload),
    );

    formData.append(
      'archiveFile',
      archiveFile,
      archiveFile.name,
    );

    return formData;
  }


  private applySourceValidators(): void {
    const connectionControl =
      this.form.controls.sourceConnectionId;

    const repositoryControl =
      this.form.controls.repositoryUrl;

    const branchControl =
      this.form.controls.branch;

    const usernameControl =
      this.form.controls.username;

    const secretControl =
      this.form.controls.secret;

    connectionControl.clearValidators();
    repositoryControl.clearValidators();
    branchControl.clearValidators();
    usernameControl.clearValidators();
    secretControl.clearValidators();

    if (this.sourceType() === 'git') {
      connectionControl.setValidators([
        Validators.required,
        Validators.min(1),
      ]);

      repositoryControl.setValidators([
        Validators.required,
      ]);

      branchControl.setValidators([
        Validators.required,
      ]);

      if (
        this.visibility() === 'private'
        && this.credentialSource()
          === 'project'
      ) {
        secretControl.setValidators([
          Validators.required,
        ]);

        if (
          this.transport() === 'https'
        ) {
          usernameControl.setValidators([
            Validators.required,
          ]);
        }
      }
    }

    connectionControl.updateValueAndValidity({
      emitEvent: false,
    });

    repositoryControl.updateValueAndValidity({
      emitEvent: false,
    });

    branchControl.updateValueAndValidity({
      emitEvent: false,
    });

    usernameControl.updateValueAndValidity({
      emitEvent: false,
    });

    secretControl.updateValueAndValidity({
      emitEvent: false,
    });
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
      error.error as
        ApiErrorResponse | null;

    return (
      response?.error?.message
      || `Erreur HTTP ${error.status}.`
    );
  }
}