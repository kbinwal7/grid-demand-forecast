DO $$
DECLARE
  app_role TEXT := current_setting('gridcast.app_role');
  app_password TEXT := current_setting('gridcast.app_password');
  current_db TEXT := current_database();
BEGIN
  IF app_role = current_user THEN
    RAISE EXCEPTION 'The read-only app role must differ from the database owner';
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = app_role) THEN
    EXECUTE format(
      'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
      app_role,
      app_password
    );
  ELSE
    EXECUTE format(
      'CREATE ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
      app_role,
      app_password
    );
  END IF;

  EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_db, app_role);
  EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', app_role);
  EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I', app_role);
  EXECUTE format(
    'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO %I',
    app_role
  );
END
$$;
