import {
  AuthenticationType,
  ProviderType,
} from './integrations';


export type IntegrationCategory =
  | 'source_control'
  | 'registry'
  | 'delivery'
  | 'cluster'
  | 'storage'
  | 'ai'
  | 'advanced';


export interface ProviderPort {
  port: number;
  label: string;
  usage: string;
}


export interface ProviderDefinition {
  type: ProviderType;
  category: IntegrationCategory;

  label: string;
  description: string;

  namePlaceholder: string;
  urlPlaceholder: string;

  endpointPath: string;
  exactUrl: boolean;

  ports: ProviderPort[];

  authTypes:
    AuthenticationType[];

  defaultAuthType:
    AuthenticationType;

  credentialLabel: string;
  credentialPlaceholder: string;

  usernameLabel: string;
  usernamePlaceholder: string;

  verifySslAvailable: boolean;

  helpTitle: string;
  helpMessages: string[];
}


export interface CategoryDefinition {
  type: IntegrationCategory;
  label: string;
  description: string;
}


export const CATEGORY_DEFINITIONS:
  CategoryDefinition[] = [
    {
      type: 'source_control',
      label: 'Code source',
      description:
        'Dépôts Git et gestion '
        + 'du code source.',
    },

    {
      type: 'registry',
      label:
        'Registre et artefacts',
      description:
        'Images de conteneurs '
        + 'et paquets.',
    },

    {
      type: 'delivery',
      label:
        'Déploiement continu',
      description:
        'GitOps et synchronisation '
        + 'des applications.',
    },

    {
      type: 'cluster',
      label:
        'Cluster Kubernetes',
      description:
        'API du cluster cible.',
    },

    {
      type: 'storage',
      label:
        'Stockage partagé',
      description:
        'Serveur NFS utilisé '
        + 'par les volumes.',
    },

    {
      type: 'ai',
      label:
        'Fournisseur IA',
      description:
        'Moteur local ou passerelle '
        + 'de modèles IA.',
    },

    {
      type: 'advanced',
      label:
        'Service HTTP personnalisé',
      description:
        'Contrôle avancé d’une URL '
        + 'HTTP ou HTTPS.',
    },
  ];


export const PROVIDER_DEFINITIONS:
  Record<
    ProviderType,
    ProviderDefinition
  > = {
    gitlab: {
      type: 'gitlab',
      category: 'source_control',

      label: 'GitLab',

      description:
        'Dépôts Git, projets et '
        + 'accès au code source.',

      namePlaceholder:
        'Exemple : GitLab principal',

      urlPlaceholder:
        'https://gitlab.example.com',

      endpointPath:
        '/api/v4/user',

      exactUrl:
        false,

      ports: [
        {
          port: 443,
          label: 'HTTPS',
          usage:
            'Accès sécurisé recommandé.',
        },
        {
          port: 80,
          label: 'HTTP',
          usage:
            'Accès interne sans TLS.',
        },
      ],

      authTypes: [
        'token',
        'basic',
        'none',
      ],

      defaultAuthType:
        'token',

      credentialLabel:
        'Token ou mot de passe',

      credentialPlaceholder:
        'Saisissez le credential GitLab',

      usernameLabel:
        'Nom d’utilisateur',

      usernamePlaceholder:
        'Requis uniquement avec Basic',

      verifySslAvailable:
        true,

      helpTitle:
        'Connexion au serveur GitLab',

      helpMessages: [
        (
          'Saisissez l’adresse du '
          + 'serveur, pas celle '
          + 'd’un projet.'
        ),
        (
          'Avec un token, SApixi '
          + 'teste automatiquement '
          + '/api/v4/user.'
        ),
      ],
    },


    github: {
      type: 'github',
      category: 'source_control',

      label: 'GitHub',

      description:
        'Dépôts GitHub, organisations et '
        + 'accès au code source.',

      namePlaceholder:
        'Exemple : GitHub principal',

      urlPlaceholder:
        'https://github.com',

      endpointPath:
        '/user',

      exactUrl:
        false,

      ports: [
        {
          port: 443,
          label: 'HTTPS',
          usage:
            'Accès sécurisé à GitHub.',
        },
      ],

      authTypes: [
        'token',
      ],

      defaultAuthType:
        'token',

      credentialLabel:
        'Personal Access Token',

      credentialPlaceholder:
        'github_pat_... ou ghp_...',

      usernameLabel:
        'Nom d’utilisateur',

      usernamePlaceholder:
        'Facultatif pour le test API',

      verifySslAvailable:
        true,

      helpTitle:
        'Connexion à GitHub',

      helpMessages: [
        (
          'Pour GitHub.com, utilisez '
          + 'https://github.com comme adresse.'
        ),
        (
          'SApixi utilise automatiquement '
          + 'https://api.github.com/user pour '
          + 'tester le token.'
        ),
        (
          'Le token doit au minimum pouvoir '
          + 'lire les repositories utilisés '
          + 'par les projets.'
        ),
      ],
    },


    nexus: {
      type: 'nexus',
      category: 'registry',

      label:
        'Nexus Repository',

      description:
        'Repositories Docker, Helm et autres '
        + 'artefacts Nexus.',

      namePlaceholder:
        'Exemple : Nexus principal',

      urlPlaceholder:
        'https://nexus.example.com',

      endpointPath:
        '/service/rest/v1/status',

      exactUrl:
        false,

      ports: [
        {
          port: 8081,
          label: 'API Nexus',
          usage:
            'Port fréquent de '
            + 'l’interface et '
            + 'de l’API REST.',
        },
        {
          port: 443,
          label: 'HTTPS',
          usage:
            'API exposée derrière '
            + 'un reverse proxy.',
        },
        {
          port: 8082,
          label: 'Docker',
          usage:
            'Exemple de port distinct '
            + 'pour un registre Docker.',
        },
      ],

      authTypes: [
        'basic',
        'none',
      ],

      defaultAuthType:
        'basic',

      credentialLabel:
        'Mot de passe',

      credentialPlaceholder:
        'Saisissez le mot de passe Nexus',

      usernameLabel:
        'Nom d’utilisateur',

      usernamePlaceholder:
        'Exemple : sapixi',

      verifySslAvailable:
        true,

      helpTitle:
        'Connexion à l’API Nexus',

      helpMessages: [
        (
          'Saisissez uniquement l’adresse '
          + 'du serveur Nexus (API/UI), par exemple :8081.'
        ),
        (
          'Après le test, SApixi détecte automatiquement '
          + 'les repositories Docker et Helm ainsi que '
          + 'les connecteurs Docker publiés.'
        ),
        (
          'Aucun repository n’est choisi dans Intégrations : '
          + 'le choix se fait dans la phase 3 du projet.'
        ),
      ],
    },


    argocd: {
      type: 'argocd',
      category: 'delivery',

      label: 'Argo CD',

      description:
        'Synchronisation GitOps et '
        + 'déploiement continu.',

      namePlaceholder:
        'Exemple : Argo CD principal',

      urlPlaceholder:
        'https://argocd.example.com',

      endpointPath:
        '/api/version',

      exactUrl:
        false,

      ports: [
        {
          port: 80,
          label: 'HTTP',
          usage:
            'Service interne ou '
            + 'Ingress sans TLS.',
        },
        {
          port: 443,
          label: 'HTTPS',
          usage:
            'Accès sécurisé.',
        },
      ],

      authTypes: [
        'token',
        'none',
      ],

      defaultAuthType:
        'none',

      credentialLabel:
        'Token Argo CD',

      credentialPlaceholder:
        'Saisissez un token Argo CD',

      usernameLabel:
        'Nom d’utilisateur',

      usernamePlaceholder:
        'Non utilisé avec un token',

      verifySslAvailable:
        true,

      helpTitle:
        'Connexion à l’API Argo CD',

      helpMessages: [
        (
          'SApixi ajoute automatiquement '
          + '/api/version pour '
          + 'le contrôle.'
        ),
        (
          'Un token sera nécessaire '
          + 'plus tard pour créer et '
          + 'synchroniser des applications.'
        ),
      ],
    },


    kubernetes: {
      type: 'kubernetes',
      category: 'cluster',

      label: 'Kubernetes',

      description:
        'API Server du cluster '
        + 'Kubernetes cible.',

      namePlaceholder:
        'Exemple : Cluster principal',

      urlPlaceholder:
        'https://kubernetes.default.svc',

      endpointPath:
        '/version',

      exactUrl:
        false,

      ports: [
        {
          port: 443,
          label:
            'Service interne',
          usage:
            'Port du service '
            + 'kubernetes.default.svc.',
        },
        {
          port: 6443,
          label:
            'API Server',
          usage:
            'Port habituel d’un '
            + 'control plane externe.',
        },
      ],

      authTypes: [
        'token',
      ],

      defaultAuthType:
        'token',

      credentialLabel:
        'Token de ServiceAccount',

      credentialPlaceholder:
        'Saisissez le token Kubernetes',

      usernameLabel:
        'Nom d’utilisateur',

      usernamePlaceholder:
        'Non utilisé',

      verifySslAvailable:
        true,

      helpTitle:
        'Connexion sécurisée au cluster',

      helpMessages: [
        (
          'Depuis le même cluster, '
          + 'utilisez généralement '
          + 'https://kubernetes.default.svc.'
        ),
        (
          'SApixi charge automatiquement '
          + 'la CA du ServiceAccount '
          + 'lorsqu’elle est disponible.'
        ),
      ],
    },


    nfs: {
      type: 'nfs',
      category: 'storage',

      label:
        'Serveur NFS',

      description:
        'Stockage partagé pour '
        + 'les volumes persistants.',

      namePlaceholder:
        'Exemple : NFS principal',

      urlPlaceholder:
        (
          'nfs://nfs.example.local:2049'
          + '/srv/nfs/k8s'
        ),

      endpointPath:
        '',

      exactUrl:
        true,

      ports: [
        {
          port: 2049,
          label: 'NFS',
          usage:
            'Port principal '
            + 'du protocole NFS.',
        },
      ],

      authTypes: [
        'none',
      ],

      defaultAuthType:
        'none',

      credentialLabel:
        'Credential',

      credentialPlaceholder:
        'Non utilisé',

      usernameLabel:
        'Nom d’utilisateur',

      usernamePlaceholder:
        'Non utilisé',

      verifySslAvailable:
        false,

      helpTitle:
        'Contrôle du serveur NFS',

      helpMessages: [
        (
          'Format attendu : '
          + 'nfs://serveur:2049/'
          + 'chemin-exporte.'
        ),
        (
          'Le contrôle vérifie que '
          + 'le port répond ; '
          + 'il ne monte pas le partage.'
        ),
      ],
    },


    ollama: {
      type: 'ollama',
      category: 'ai',

      label: 'Ollama',

      description:
        'Exécution simple '
        + 'de modèles locaux.',

      namePlaceholder:
        'Exemple : Ollama local',

      urlPlaceholder:
        (
          'http://ollama.'
          + 'example.local:11434'
        ),

      endpointPath:
        '/api/tags',

      exactUrl:
        false,

      ports: [
        {
          port: 11434,
          label:
            'API Ollama',
          usage:
            'Port habituel '
            + 'du serveur Ollama.',
        },
      ],

      authTypes: [
        'none',
        'token',
      ],

      defaultAuthType:
        'none',

      credentialLabel:
        'Clé API',

      credentialPlaceholder:
        'Facultatif selon votre proxy',

      usernameLabel:
        'Nom d’utilisateur',

      usernamePlaceholder:
        'Non utilisé',

      verifySslAvailable:
        true,

      helpTitle:
        'Moteur local Ollama',

      helpMessages: [
        (
          'Adapté aux modèles locaux '
          + 'et aux environnements '
          + 'de développement.'
        ),
        (
          'SApixi teste la liste '
          + 'des modèles via /api/tags.'
        ),
      ],
    },


    litellm: {
      type: 'litellm',
      category: 'ai',

      label:
        'LiteLLM Proxy',

      description:
        'Passerelle unique vers '
        + 'plusieurs fournisseurs IA.',

      namePlaceholder:
        'Exemple : Passerelle LiteLLM',

      urlPlaceholder:
        (
          'http://litellm.'
          + 'example.local:4000'
        ),

      endpointPath:
        '/health/readiness',

      exactUrl:
        false,

      ports: [
        {
          port: 4000,
          label:
            'Proxy LiteLLM',
          usage:
            'Port fréquemment utilisé '
            + 'par LiteLLM Proxy.',
        },
        {
          port: 443,
          label:
            'HTTPS',
          usage:
            'Passerelle exposée '
            + 'avec TLS.',
        },
      ],

      authTypes: [
        'token',
        'none',
      ],

      defaultAuthType:
        'token',

      credentialLabel:
        'Master key ou clé virtuelle',

      credentialPlaceholder:
        'Exemple : sk-...',

      usernameLabel:
        'Nom d’utilisateur',

      usernamePlaceholder:
        'Non utilisé',

      verifySslAvailable:
        true,

      helpTitle:
        'Passerelle multi-fournisseurs',

      helpMessages: [
        (
          'LiteLLM centralise plusieurs '
          + 'modèles et fournisseurs '
          + 'derrière une seule API.'
        ),
        (
          'SApixi teste l’état de '
          + 'préparation via '
          + '/health/readiness.'
        ),
      ],
    },


    vllm: {
      type: 'vllm',
      category: 'ai',

      label: 'vLLM',

      description:
        'Serveur de modèles '
        + 'optimisé pour les GPU.',

      namePlaceholder:
        'Exemple : vLLM GPU',

      urlPlaceholder:
        (
          'http://vllm.'
          + 'example.local:8000'
        ),

      endpointPath:
        '/health',

      exactUrl:
        false,

      ports: [
        {
          port: 8000,
          label:
            'API vLLM',
          usage:
            'Port fréquemment utilisé '
            + 'par le serveur vLLM.',
        },
        {
          port: 443,
          label:
            'HTTPS',
          usage:
            'Serveur exposé avec TLS.',
        },
      ],

      authTypes: [
        'none',
        'token',
      ],

      defaultAuthType:
        'none',

      credentialLabel:
        'Clé API',

      credentialPlaceholder:
        (
          'Facultatif selon '
          + 'la configuration'
        ),

      usernameLabel:
        'Nom d’utilisateur',

      usernamePlaceholder:
        'Non utilisé',

      verifySslAvailable:
        true,

      helpTitle:
        (
          'Serveur de modèles '
          + 'haute performance'
        ),

      helpMessages: [
        (
          'vLLM est adapté au service '
          + 'de modèles sur des '
          + 'machines GPU.'
        ),
        (
          'SApixi vérifie '
          + 'l’endpoint /health.'
        ),
      ],
    },


    openai_compatible: {
      type:
        'openai_compatible',

      category: 'ai',

      label:
        'API compatible OpenAI',

      description:
        'Service exposant le '
        + 'protocole d’API OpenAI.',

      namePlaceholder:
        (
          'Exemple : API IA '
          + 'compatible OpenAI'
        ),

      urlPlaceholder:
        'https://ai.example.com',

      endpointPath:
        '/v1/models',

      exactUrl:
        false,

      ports: [
        {
          port: 443,
          label: 'HTTPS',
          usage:
            'API distante sécurisée.',
        },
        {
          port: 8000,
          label:
            'HTTP local',
          usage:
            'Port fréquent pour '
            + 'un serveur local.',
        },
      ],

      authTypes: [
        'token',
        'none',
      ],

      defaultAuthType:
        'token',

      credentialLabel:
        'Clé API',

      credentialPlaceholder:
        'Saisissez la clé API',

      usernameLabel:
        'Nom d’utilisateur',

      usernamePlaceholder:
        'Non utilisé',

      verifySslAvailable:
        true,

      helpTitle:
        'API IA compatible OpenAI',

      helpMessages: [
        (
          'Utilisez ce choix pour '
          + 'OpenAI, LocalAI, LM Studio '
          + 'ou une passerelle compatible.'
        ),
        (
          'SApixi vérifie la liste '
          + 'des modèles via /v1/models.'
        ),
      ],
    },


    generic_http: {
      type:
        'generic_http',

      category:
        'advanced',

      label:
        'Service HTTP personnalisé',

      description:
        'Contrôle d’une URL HTTP/HTTPS '
        + 'non prise en charge '
        + 'nativement.',

      namePlaceholder:
        'Exemple : API interne',

      urlPlaceholder:
        (
          'https://service.example.com'
          + '/health'
        ),

      endpointPath:
        '',

      exactUrl:
        true,

      ports: [
        {
          port: 443,
          label: 'HTTPS',
          usage:
            'Endpoint sécurisé.',
        },
        {
          port: 80,
          label: 'HTTP',
          usage:
            'Endpoint interne.',
        },
      ],

      authTypes: [
        'none',
        'token',
        'basic',
      ],

      defaultAuthType:
        'none',

      credentialLabel:
        'Token ou mot de passe',

      credentialPlaceholder:
        (
          'Facultatif selon '
          + 'le service'
        ),

      usernameLabel:
        'Nom d’utilisateur',

      usernamePlaceholder:
        (
          'Requis uniquement '
          + 'avec Basic'
        ),

      verifySslAvailable:
        true,

      helpTitle:
        'À quoi sert ce service ?',

      helpMessages: [
        (
          'Il permet de surveiller '
          + 'une API ou une page de '
          + 'santé non supportée '
          + 'nativement par SApixi.'
        ),
        (
          'Exemples : SonarQube, '
          + 'Grafana, une API interne '
          + 'ou un endpoint /health.'
        ),
        (
          'SApixi utilise exactement '
          + 'l’URL saisie et n’ajoute '
          + 'aucun chemin.'
        ),
        (
          'Ce choix ne convient pas '
          + 'à NFS, SSH ou une '
          + 'base de données.'
        ),
      ],
    },
  };


export function providersForCategory(
  category:
    IntegrationCategory | null,
): ProviderDefinition[] {
  if (!category) {
    return [];
  }

  return Object.values(
    PROVIDER_DEFINITIONS,
  ).filter(
    (
      definition:
        ProviderDefinition,
    ) =>
      definition.category
      === category,
  );
}


export function buildCheckedUrl(
  providerType:
    ProviderType,

  baseUrl:
    string,
): string {
  const definition =
    PROVIDER_DEFINITIONS[
      providerType
    ];

  const normalized =
    baseUrl
      .trim()
      .replace(/\/+$/, '');

  if (providerType === 'github') {
    try {
      const parsed = new URL(normalized);
      if (parsed.hostname === 'github.com' || parsed.hostname === 'www.github.com') {
        return 'https://api.github.com/user';
      }
      return normalized + '/api/v3/user';
    } catch {
      return normalized;
    }
  }

  if (
    !normalized
    || definition.exactUrl
  ) {
    return normalized;
  }

  return (
    normalized
    + definition.endpointPath
  );
}


export function resolveUrlDetails(
  value: string,
): {
  protocol: string | null;
  host: string | null;
  port: number | null;
} {
  try {
    const parsed =
      new URL(value);

    let port:
      number | null = null;

    if (parsed.port) {
      port = Number(
        parsed.port
      );
    }

    else if (
      parsed.protocol === 'https:'
    ) {
      port = 443;
    }

    else if (
      parsed.protocol === 'http:'
    ) {
      port = 80;
    }

    else if (
      parsed.protocol === 'nfs:'
    ) {
      port = 2049;
    }

    return {
      protocol:
        parsed.protocol
          .replace(':', '')
          .toUpperCase(),

      host:
        parsed.hostname || null,

      port,
    };
  }

  catch {
    return {
      protocol: null,
      host: null,
      port: null,
    };
  }
}