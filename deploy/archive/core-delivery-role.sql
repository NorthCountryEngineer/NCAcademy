-- Owner-run once in Academy's dedicated database, after schema 11 migration.
-- Generate/set a password through private operator tooling; never commit it.
-- The dispatcher is separate from both the API and the read-only exporter.
CREATE ROLE academy_metadata_dispatcher LOGIN NOINHERIT NOSUPERUSER NOCREATEDB
    NOCREATEROLE NOREPLICATION NOBYPASSRLS;
REVOKE CREATE, TEMPORARY ON DATABASE academy FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE academy TO academy_metadata_dispatcher;
GRANT USAGE ON SCHEMA public TO academy_metadata_dispatcher;
GRANT SELECT ON TABLE public.audit_delivery_outbox,
    public.audit_delivery_state, public.audit_delivery_receipts
    TO academy_metadata_dispatcher;
GRANT UPDATE ON TABLE public.audit_delivery_state TO academy_metadata_dispatcher;
GRANT INSERT ON TABLE public.audit_delivery_receipts TO academy_metadata_dispatcher;
-- No raw audit events/heads, records, logins, identity binding, credentials,
-- outbox insertion/mutation, receipt mutation, schema ownership or memberships.
