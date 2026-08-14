BEGIN;

-- ============================================================
-- PIXIMIND — centre de notifications temps réel
--
-- Une notification appartient désormais à un destinataire précis.
-- Les incidents d'un utilisateur sont aussi dupliqués vers chaque
-- administrateur actif afin que la supervision globale reste possible
-- sans partager le même état lu/non-lu.
-- ============================================================

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS user_id BIGINT,
    ADD COLUMN IF NOT EXISTS project_id BIGINT,
    ADD COLUMN IF NOT EXISTS deployment_id BIGINT,
    ADD COLUMN IF NOT EXISTS environment_id BIGINT,
    ADD COLUMN IF NOT EXISTS resource_type VARCHAR(60),
    ADD COLUMN IF NOT EXISTS resource_id BIGINT,
    ADD COLUMN IF NOT EXISTS action_url TEXT,
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'notifications_user_fk'
    ) THEN
        ALTER TABLE notifications
            ADD CONSTRAINT notifications_user_fk
            FOREIGN KEY (user_id)
            REFERENCES users (id)
            ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'notifications_project_fk'
    ) THEN
        ALTER TABLE notifications
            ADD CONSTRAINT notifications_project_fk
            FOREIGN KEY (project_id)
            REFERENCES projects (id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'notifications_deployment_fk'
    ) THEN
        ALTER TABLE notifications
            ADD CONSTRAINT notifications_deployment_fk
            FOREIGN KEY (deployment_id)
            REFERENCES deployments (id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'notifications_environment_fk'
    ) THEN
        ALTER TABLE notifications
            ADD CONSTRAINT notifications_environment_fk
            FOREIGN KEY (environment_id)
            REFERENCES deployment_environments (id)
            ON DELETE SET NULL;
    END IF;
END;
$$;

-- Les anciennes notifications d'intégration sont rattachées au créateur
-- de la connexion lorsqu'il est connu.
UPDATE notifications AS notification
SET
    user_id = COALESCE(
        notification.user_id,
        integration.created_by
    ),
    resource_type = COALESCE(
        notification.resource_type,
        'integration'
    ),
    resource_id = COALESCE(
        notification.resource_id,
        integration.id
    ),
    action_url = COALESCE(
        notification.action_url,
        CASE
            WHEN integration.provider_type = 'kubernetes'
                THEN '/infrastructure'
            ELSE '/integrations'
        END
    )
FROM integration_connections AS integration
WHERE notification.connection_id = integration.id;

CREATE INDEX IF NOT EXISTS notifications_user_created_idx
ON notifications (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS notifications_user_unread_idx
ON notifications (user_id, created_at DESC)
WHERE read_at IS NULL;

CREATE INDEX IF NOT EXISTS notifications_deployment_idx
ON notifications (deployment_id, created_at DESC)
WHERE deployment_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS notifications_resource_idx
ON notifications (resource_type, resource_id, created_at DESC);

-- ============================================================
-- Fonction centrale d'émission.
--
-- Destinataires :
-- 1. propriétaire de la ressource si présent ;
-- 2. tous les administrateurs actifs ;
-- UNION évite un doublon si le propriétaire est lui-même admin.
-- ============================================================

CREATE OR REPLACE FUNCTION piximind_emit_notification(
    p_owner_user_id BIGINT,
    p_connection_id BIGINT,
    p_project_id BIGINT,
    p_deployment_id BIGINT,
    p_environment_id BIGINT,
    p_notification_type VARCHAR,
    p_severity VARCHAR,
    p_title TEXT,
    p_message TEXT,
    p_resource_type VARCHAR,
    p_resource_id BIGINT,
    p_action_url TEXT,
    p_metadata JSONB DEFAULT '{}'::JSONB
)
RETURNS VOID AS $$
BEGIN
    INSERT INTO notifications (
        user_id,
        connection_id,
        project_id,
        deployment_id,
        environment_id,
        notification_type,
        severity,
        title,
        message,
        resource_type,
        resource_id,
        action_url,
        metadata
    )
    SELECT
        recipient.user_id,
        p_connection_id,
        p_project_id,
        p_deployment_id,
        p_environment_id,
        p_notification_type,
        p_severity,
        LEFT(p_title, 200),
        p_message,
        p_resource_type,
        p_resource_id,
        p_action_url,
        COALESCE(p_metadata, '{}'::JSONB)
    FROM (
        SELECT p_owner_user_id AS user_id
        WHERE p_owner_user_id IS NOT NULL

        UNION

        SELECT platform_user.id
        FROM users AS platform_user
        INNER JOIN user_roles AS user_role
            ON user_role.user_id = platform_user.id
        INNER JOIN roles AS role
            ON role.id = user_role.role_id
        WHERE role.code IN ('admin', 'administrator')
          AND platform_user.is_active = TRUE
          AND COALESCE(platform_user.status, 'active') = 'active'
    ) AS recipient
    WHERE recipient.user_id IS NOT NULL;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- INTÉGRATIONS / KUBERNETES
--
-- Le worker monitor-integrations met à jour integration_connections.status.
-- Ce trigger transforme chaque changement réel en notification.
-- ============================================================

CREATE OR REPLACE FUNCTION piximind_notify_integration_status_change()
RETURNS TRIGGER AS $$
DECLARE
    target_url TEXT;
    notification_title TEXT;
    notification_message TEXT;
BEGIN
    IF OLD.status IS NOT DISTINCT FROM NEW.status THEN
        RETURN NEW;
    END IF;

    target_url := CASE
        WHEN NEW.provider_type = 'kubernetes'
            THEN '/infrastructure'
        ELSE '/integrations'
    END;

    IF NEW.status = 'degraded' THEN
        notification_title := CASE
            WHEN NEW.provider_type = 'kubernetes'
                THEN 'Cluster Kubernetes dégradé — ' || NEW.name
            ELSE NEW.name || ' est dégradé'
        END;

        notification_message := COALESCE(
            NULLIF(NEW.last_error, ''),
            'Le service répond partiellement ou un contrôle de santé a échoué.'
        );

        PERFORM piximind_emit_notification(
            NEW.created_by,
            NEW.id,
            NULL,
            NULL,
            NULL,
            'integration.degraded',
            'warning',
            notification_title,
            notification_message,
            'integration',
            NEW.id,
            target_url,
            JSONB_BUILD_OBJECT(
                'providerType', NEW.provider_type,
                'previousStatus', OLD.status,
                'status', NEW.status
            )
        );

    ELSIF NEW.status = 'offline' THEN
        UPDATE notifications
        SET resolved_at = CURRENT_TIMESTAMP
        WHERE connection_id = NEW.id
          AND notification_type = 'integration.degraded'
          AND resolved_at IS NULL;

        notification_title := CASE
            WHEN NEW.provider_type = 'kubernetes'
                THEN 'Cluster Kubernetes indisponible — ' || NEW.name
            ELSE NEW.name || ' est inaccessible'
        END;

        notification_message := COALESCE(
            NULLIF(NEW.last_error, ''),
            'Le service ne répond plus aux contrôles de santé.'
        );

        PERFORM piximind_emit_notification(
            NEW.created_by,
            NEW.id,
            NULL,
            NULL,
            NULL,
            'integration.offline',
            'critical',
            notification_title,
            notification_message,
            'integration',
            NEW.id,
            target_url,
            JSONB_BUILD_OBJECT(
                'providerType', NEW.provider_type,
                'previousStatus', OLD.status,
                'status', NEW.status
            )
        );

    ELSIF NEW.status = 'online'
          AND OLD.status IN ('degraded', 'offline') THEN
        UPDATE notifications
        SET resolved_at = CURRENT_TIMESTAMP
        WHERE connection_id = NEW.id
          AND notification_type IN (
              'integration.degraded',
              'integration.offline'
          )
          AND resolved_at IS NULL;

        notification_title := CASE
            WHEN NEW.provider_type = 'kubernetes'
                THEN 'Cluster Kubernetes rétabli — ' || NEW.name
            ELSE NEW.name || ' est rétabli'
        END;

        PERFORM piximind_emit_notification(
            NEW.created_by,
            NEW.id,
            NULL,
            NULL,
            NULL,
            'integration.recovered',
            'success',
            notification_title,
            'Le service répond de nouveau correctement.',
            'integration',
            NEW.id,
            target_url,
            JSONB_BUILD_OBJECT(
                'providerType', NEW.provider_type,
                'previousStatus', OLD.status,
                'status', NEW.status
            )
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS integration_status_notification_trigger
ON integration_connections;

CREATE TRIGGER integration_status_notification_trigger
AFTER UPDATE OF status ON integration_connections
FOR EACH ROW
EXECUTE FUNCTION piximind_notify_integration_status_change();

-- ============================================================
-- DÉPLOIEMENTS
-- ============================================================

CREATE OR REPLACE FUNCTION piximind_notify_deployment_status_change()
RETURNS TRIGGER AS $$
DECLARE
    owner_user_id BIGINT;
    project_name TEXT;
    notification_type_value VARCHAR(50);
    severity_value VARCHAR(20);
    title_value TEXT;
    message_value TEXT;
BEGIN
    IF OLD.status IS NOT DISTINCT FROM NEW.status THEN
        RETURN NEW;
    END IF;

    IF NEW.status NOT IN (
        'failed',
        'succeeded',
        'cancelled',
        'waiting_confirmation'
    ) THEN
        RETURN NEW;
    END IF;

    SELECT
        project.created_by,
        project.name
    INTO
        owner_user_id,
        project_name
    FROM projects AS project
    WHERE project.id = NEW.project_id;

    IF NEW.status = 'failed' THEN
        notification_type_value := 'deployment.failed';
        severity_value := 'critical';
        title_value := 'Déploiement échoué — ' || COALESCE(project_name, 'Projet');
        message_value := COALESCE(
            NULLIF(NEW.error_message, ''),
            NULLIF(NEW.current_stage_label, ''),
            'Le déploiement a échoué. Consultez les détails pour lancer le diagnostic.'
        );

    ELSIF NEW.status = 'succeeded' THEN
        notification_type_value := 'deployment.succeeded';
        severity_value := 'success';
        title_value := 'Déploiement réussi — ' || COALESCE(project_name, 'Projet');
        message_value := COALESCE(
            NULLIF(NEW.current_stage_label, ''),
            'Le déploiement s’est terminé avec succès.'
        );

    ELSIF NEW.status = 'cancelled' THEN
        notification_type_value := 'deployment.cancelled';
        severity_value := 'info';
        title_value := 'Déploiement annulé — ' || COALESCE(project_name, 'Projet');
        message_value := 'Le déploiement a été annulé.';

    ELSE
        notification_type_value := 'deployment.confirmation_required';
        severity_value := 'warning';
        title_value := 'Confirmation Argo CD requise — ' || COALESCE(project_name, 'Projet');
        message_value := COALESCE(
            NULLIF(NEW.current_stage_label, ''),
            'Le déploiement attend votre confirmation avant la synchronisation Argo CD.'
        );
    END IF;

    PERFORM piximind_emit_notification(
        owner_user_id,
        NULL,
        NEW.project_id,
        NEW.id,
        NEW.environment_id,
        notification_type_value,
        severity_value,
        title_value,
        message_value,
        'deployment',
        NEW.id,
        '/deployments/' || NEW.id::TEXT,
        JSONB_BUILD_OBJECT(
            'previousStatus', OLD.status,
            'status', NEW.status,
            'environment', NEW.environment,
            'stage', NEW.current_stage,
            'errorCode', NEW.error_code
        )
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deployment_status_notification_trigger
ON deployments;

CREATE TRIGGER deployment_status_notification_trigger
AFTER UPDATE OF status ON deployments
FOR EACH ROW
EXECUTE FUNCTION piximind_notify_deployment_status_change();

INSERT INTO schema_migrations (
    version,
    description
)
VALUES (
    '025',
    'Centre de notifications utilisateur, alertes intégrations et déploiements'
)
ON CONFLICT (version)
DO NOTHING;

COMMIT;
