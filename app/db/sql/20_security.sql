-- Row-level security: a transaction sees only the workspace named in app.workspace_id.
-- app.service = 'on' lifts it for trusted cross-workspace work (see db/session.py).
-- FORCE applies it to the table owner too. Superusers still bypass it, so the app must connect
-- as a normal role. Idempotent: policies are dropped before they are recreated.

do $$
declare t text;
declare allowed text := $p$(current_setting('app.service', true) = 'on'
    or %2$I = nullif(current_setting('app.workspace_id', true), '')::uuid)$p$;
begin
    for t in select table_name from information_schema.columns
             where table_schema = 'public' and column_name = 'workspace_id' loop
        execute format('alter table %I enable row level security', t);
        execute format('alter table %I force row level security', t);
        execute format('drop policy if exists tenant_isolation on %I', t);
        execute format('create policy tenant_isolation on %1$I using ' || allowed
                       || ' with check ' || allowed, t, 'workspace_id');
    end loop;

    -- workspaces has no workspace_id column; its own id is the tenant key.
    alter table workspaces enable row level security;
    alter table workspaces force row level security;
    execute 'drop policy if exists tenant_isolation on workspaces';
    execute format('create policy tenant_isolation on workspaces using ' || allowed
                   || ' with check ' || allowed, 'workspaces', 'id');
end $$;
