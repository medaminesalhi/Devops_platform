import {
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';

import {
  DatePipe,
} from '@angular/common';

import {
  FormBuilder,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';

import {
  HttpErrorResponse,
} from '@angular/common/http';

import {
  finalize,
} from 'rxjs';

import {
  AuthenticationType,
  ConnectionStatus,
  IntegrationConfiguration,
  IntegrationConnection,
  IntegrationTestResult,
  IntegrationsService,
  ProviderType,
} from '../../../../core/integrations/integrations';


interface ApiErrorResponse {
  success: false;

  error: {
    code: string;
    message: string;
  };
}


@Component({
  selector: 'app-integrations',

  imports: [
    ReactiveFormsModule,
    DatePipe,
  ],

  templateUrl: './integrations.html',
  styleUrl: './integrations.scss',
})
export class Integrations implements OnInit {
  private readonly integrationsService =
    inject(IntegrationsService);

  private readonly formBuilder =
    inject(FormBuilder);


  readonly connections =
    signal<IntegrationConnection[]>([]);

  readonly selectedConnectionId =
    signal<number | null>(null);

  readonly editorOpen =
    signal(false);

  readonly editingConnectionId =
    signal<number | null>(null);

  readonly isLoading =
    signal(true);

  readonly isSaving =
    signal(false);

  readonly isTestingDraft =
    signal(false);

  readonly testingConnectionId =
    signal<number | null>(null);

  readonly errorMessage =
    signal<string | null>(null);

  readonly successMessage =
    signal<string | null>(null);

  readonly draftTestResult =
    signal<IntegrationTestResult | null>(
      null,
    );

  readonly savedTestResult =
    signal<IntegrationTestResult | null>(
      null,
    );


  readonly connectionForm =
    this.formBuilder.nonNullable.group({
      name: [
        '',
        [
          Validators.required,
          Validators.maxLength(120),
        ],
      ],

      providerType: [
        'gitlab' as ProviderType,
        [
          Validators.required,
        ],
      ],

      baseUrl: [
        '',
        [
          Validators.required,
          Validators.pattern(
            /^https?:\/\/.+/i,
          ),
        ],
      ],

      environment: [
        'internal',
        [
          Validators.required,
          Validators.maxLength(80),
        ],
      ],

      description: [
        '',
        [
          Validators.maxLength(500),
        ],
      ],

      authType: [
        'token' as AuthenticationType,
        [
          Validators.required,
        ],
      ],

      username: [
        '',
        [
          Validators.maxLength(200),
        ],
      ],

      credential: [
        '',
        [
          Validators.maxLength(5000),
        ],
      ],

      monitoringEnabled: [
        true,
      ],

      checkIntervalSeconds: [
        300,
        [
          Validators.required,
          Validators.min(60),
          Validators.max(86400),
        ],
      ],

      failureThreshold: [
        3,
        [
          Validators.required,
          Validators.min(1),
          Validators.max(10),
        ],
      ],
    });


  readonly selectedConnection = computed(
    (): IntegrationConnection | null => {
      const selectedId =
        this.selectedConnectionId();

      if (selectedId === null) {
        return null;
      }

      return (
        this.connections().find(
          (connection) =>
            connection.id === selectedId,
        ) ?? null
      );
    },
  );


  readonly onlineCount = computed(
    () =>
      this.connections().filter(
        (connection) =>
          connection.status === 'online',
      ).length,
  );


  readonly degradedCount = computed(
    () =>
      this.connections().filter(
        (connection) =>
          connection.status ===
          'degraded',
      ).length,
  );


  readonly offlineCount = computed(
    () =>
      this.connections().filter(
        (connection) =>
          connection.status === 'offline',
      ).length,
  );


  ngOnInit(): void {
    this.loadConnections();
  }


  loadConnections(): void {
    this.isLoading.set(true);
    this.errorMessage.set(null);

    this.integrationsService
      .getAll()
      .pipe(
        finalize(() => {
          this.isLoading.set(false);
        }),
      )
      .subscribe({
        next: (
          connections:
            IntegrationConnection[],
        ) => {
          this.connections.set(connections);

          const currentSelection =
            this.selectedConnectionId();

          const selectionStillExists =
            connections.some(
              (connection) =>
                connection.id ===
                currentSelection,
            );

          if (
            !selectionStillExists &&
            connections.length > 0
          ) {
            this.selectedConnectionId.set(
              connections[0].id,
            );
          }
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.errorMessage.set(
            this.resolveError(error),
          );
        },
      });
  }


  selectConnection(
    connection: IntegrationConnection,
  ): void {
    this.selectedConnectionId.set(
      connection.id,
    );

    this.savedTestResult.set(null);
  }


  openCreateEditor(): void {
    this.editingConnectionId.set(null);

    this.connectionForm.reset({
      name: '',
      providerType: 'gitlab',
      baseUrl: '',
      environment: 'internal',
      description: '',
      authType: 'token',
      username: '',
      credential: '',
      monitoringEnabled: true,
      checkIntervalSeconds: 300,
      failureThreshold: 3,
    });

    this.draftTestResult.set(null);
    this.errorMessage.set(null);
    this.successMessage.set(null);

    this.editorOpen.set(true);
  }


  openEditEditor(
    connection: IntegrationConnection,
  ): void {
    this.editingConnectionId.set(
      connection.id,
    );

    this.connectionForm.reset({
      name: connection.name,

      providerType:
        connection.providerType,

      baseUrl:
        connection.baseUrl,

      environment:
        connection.environment,

      description:
        connection.description ?? '',

      authType:
        connection.authType,

      username:
        connection.username ?? '',

      credential: '',

      monitoringEnabled:
        connection.monitoringEnabled,

      checkIntervalSeconds:
        connection.checkIntervalSeconds,

      failureThreshold:
        connection.failureThreshold,
    });

    this.draftTestResult.set(null);
    this.errorMessage.set(null);
    this.successMessage.set(null);

    this.editorOpen.set(true);
  }


  closeEditor(): void {
    if (
      this.isSaving() ||
      this.isTestingDraft()
    ) {
      return;
    }

    this.editorOpen.set(false);
    this.draftTestResult.set(null);
  }


  onProviderChanged(): void {
    const provider =
      this.connectionForm.controls
        .providerType.value;

    const defaultAuthType:
      Record<
        ProviderType,
        AuthenticationType
      > = {
        gitlab: 'token',
        nexus: 'basic',
        argocd: 'token',
        kubernetes: 'token',
        ollama: 'none',
        generic_http: 'none',
      };

    this.connectionForm.controls
      .authType.setValue(
        defaultAuthType[provider],
      );

    this.connectionForm.controls
      .username.setValue('');

    this.connectionForm.controls
      .credential.setValue('');
  }


  saveConnection(): void {
    this.connectionForm.markAllAsTouched();

    if (
      this.connectionForm.invalid ||
      this.isSaving()
    ) {
      return;
    }

    const configuration =
      this.buildConfiguration();

    const editingId =
      this.editingConnectionId();

    this.isSaving.set(true);
    this.errorMessage.set(null);
    this.successMessage.set(null);

    const request$ =
      editingId === null
        ? this.integrationsService.create(
            configuration,
          )
        : this.integrationsService.update(
            editingId,
            configuration,
          );

    request$
      .pipe(
        finalize(() => {
          this.isSaving.set(false);
        }),
      )
      .subscribe({
        next: (
          connection:
            IntegrationConnection,
        ) => {
          this.upsertConnection(
            connection,
          );

          this.selectedConnectionId.set(
            connection.id,
          );

          this.editorOpen.set(false);

          this.successMessage.set(
            editingId === null
              ? 'Connexion créée avec succès.'
              : 'Connexion modifiée avec succès.',
          );
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.errorMessage.set(
            this.resolveError(error),
          );
        },
      });
  }


  testDraftConfiguration(): void {
    this.connectionForm.markAllAsTouched();

    if (
      this.connectionForm.invalid ||
      this.isTestingDraft()
    ) {
      return;
    }

    const configuration =
      this.buildConfiguration();

    const editingId =
      this.editingConnectionId();

    if (editingId !== null) {
      configuration.connectionId =
        editingId;
    }

    this.isTestingDraft.set(true);
    this.draftTestResult.set(null);
    this.errorMessage.set(null);

    this.integrationsService
      .testDraft(configuration)
      .pipe(
        finalize(() => {
          this.isTestingDraft.set(false);
        }),
      )
      .subscribe({
        next: (
          result:
            IntegrationTestResult,
        ) => {
          this.draftTestResult.set(
            result,
          );
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.errorMessage.set(
            this.resolveError(error),
          );
        },
      });
  }


  testSavedConnection(
    connection: IntegrationConnection,
  ): void {
    if (
      this.testingConnectionId()
      !== null
    ) {
      return;
    }

    this.testingConnectionId.set(
      connection.id,
    );

    this.errorMessage.set(null);
    this.savedTestResult.set(null);

    this.integrationsService
      .testSaved(connection.id)
      .pipe(
        finalize(() => {
          this.testingConnectionId.set(
            null,
          );
        }),
      )
      .subscribe({
        next: (result) => {
          this.upsertConnection(
            result.connection,
          );

          this.selectedConnectionId.set(
            result.connection.id,
          );

          this.savedTestResult.set(
            result.test,
          );
        },

        error: (
          error: HttpErrorResponse,
        ) => {
          this.errorMessage.set(
            this.resolveError(error),
          );
        },
      });
  }


  requiresUsername(): boolean {
    return (
      this.connectionForm.controls
        .authType.value === 'basic'
    );
  }


  requiresCredential(): boolean {
    return (
      this.connectionForm.controls
        .authType.value !== 'none'
    );
  }


  isEditing(): boolean {
    return (
      this.editingConnectionId()
      !== null
    );
  }


  providerLabel(
    provider: ProviderType,
  ): string {
    const labels:
      Record<ProviderType, string> = {
        gitlab: 'GitLab',
        nexus: 'Nexus Repository',
        argocd: 'Argo CD',
        kubernetes: 'Kubernetes',
        ollama: 'Ollama',
        generic_http: 'Service HTTP',
      };

    return labels[provider];
  }


  statusLabel(
    status: ConnectionStatus,
  ): string {
    const labels:
      Record<ConnectionStatus, string> = {
        not_configured: 'Non configuré',
        unchecked: 'Non testé',
        online: 'Opérationnel',
        degraded: 'Dégradé',
        offline: 'Hors ligne',
      };

    return labels[status];
  }


  authTypeLabel(
    authType: AuthenticationType,
  ): string {
    const labels:
      Record<
        AuthenticationType,
        string
      > = {
        none: 'Aucune',
        token: 'Token',
        basic: 'Username / mot de passe',
      };

    return labels[authType];
  }


  intervalLabel(
    seconds: number,
  ): string {
    if (seconds % 3600 === 0) {
      return (
        `${seconds / 3600} heure(s)`
      );
    }

    return (
      `${seconds / 60} minute(s)`
    );
  }


  private buildConfiguration():
    IntegrationConfiguration {
    const values =
      this.connectionForm.getRawValue();

    return {
      name:
        values.name.trim(),

      providerType:
        values.providerType,

      baseUrl:
        values.baseUrl.trim(),

      environment:
        values.environment.trim(),

      description:
        values.description.trim()
        || null,

      authType:
        values.authType,

      username:
        values.username.trim()
        || null,

      credential:
        values.credential.trim()
        || null,

      monitoringEnabled:
        values.monitoringEnabled,

      checkIntervalSeconds:
        values.checkIntervalSeconds,

      failureThreshold:
        values.failureThreshold,
    };
  }


  private upsertConnection(
    updatedConnection:
      IntegrationConnection,
  ): void {
    this.connections.update(
      (connections) => {
        const existingIndex =
          connections.findIndex(
            (connection) =>
              connection.id ===
              updatedConnection.id,
          );

        if (existingIndex === -1) {
          return [
            ...connections,
            updatedConnection,
          ].sort(
            (first, second) =>
              first.name.localeCompare(
                second.name,
              ),
          );
        }

        return connections.map(
          (connection) =>
            connection.id ===
            updatedConnection.id
              ? updatedConnection
              : connection,
        );
      },
    );
  }


  private resolveError(
    error: HttpErrorResponse,
  ): string {
    if (error.status === 0) {
      return (
        'Le backend Flask est inaccessible. '
        + 'Vérifiez le port 5000 et '
        + 'le proxy Angular.'
      );
    }

    const response =
      error.error as
        ApiErrorResponse | null;

    if (
      response?.error?.message
    ) {
      return response.error.message;
    }

    return (
      `Erreur HTTP `
      + `${error.status || 'inconnue'}.`
    );
  }
}