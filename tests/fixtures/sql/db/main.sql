-- Bootstrap script: pulls in the schema and a sub-script, then defines
-- a plpgsql procedure whose BEGIN/END body contains statement terminators.

\i schema.sql
\ir sub/more.sql

CREATE OR REPLACE PROCEDURE cleanup() LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM orders WHERE total = 0;
    DELETE FROM audit_log WHERE action = '';
END;
$$;
