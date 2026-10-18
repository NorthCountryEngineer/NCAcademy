-- Owner-run once, separate from API/migration identities. No password literal
-- belongs in this file; set a generated credential via private operator tooling.
-- Current Academy is one tenant per dedicated database. Use tenant-filtered,
-- security-barrier views and adapt the worker before sharing a database.
CREATE ROLE academy_archive_reader LOGIN NOINHERIT NOSUPERUSER NOCREATEDB
    NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- Apply only to this dedicated Academy database. NOINHERIT alone does not
-- remove privileges inherited through PUBLIC. Academy's normal owner migration
-- already applies these revocations; they are repeated here for standalone use.
REVOKE CREATE, TEMPORARY ON DATABASE academy FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE academy TO academy_archive_reader;
GRANT USAGE ON SCHEMA public TO academy_archive_reader;
GRANT SELECT ON TABLE public.audit_events, public.audit_heads TO academy_archive_reader;
ALTER ROLE academy_archive_reader SET default_transaction_read_only = on;
-- Do not grant records, logins, oauth_flows, integration/vault secrets, schema
-- ownership, CREATE/TEMP, memberships, or any write privilege.
