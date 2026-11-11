-- Per-member privacy: inside a workspace, a request limited to one member (db/session.py bind_viewer)
-- sees only that member's rows of these tables. A request with no limit (the service key, an
-- admin token, background work) leaves app.member_private off and sees every row of its workspace.
-- Restrictive, so it narrows tenant_isolation and never widens it. Idempotent.
--   notifications, favorites, subscriptions, llm_usage: strictly the member's own.
--   memories: workspace, team and project memory stay shared; only agent memory is owned.

do $$
declare r record;
begin
    for r in select * from (values
        ('notifications', 'recipient_id', false),
        ('favorites', 'member_id', false),
        ('subscriptions', 'member_id', false),
        ('llm_usage', 'member_id', false),
        ('memories', 'owner_member_id', true)
    ) as v(tbl, col, shared) loop
        execute format('drop policy if exists member_private on %I', r.tbl);
        execute format(
            $p$create policy member_private on %1$I as restrictive for all
               using (current_setting('app.member_private', true) is distinct from 'on'
                      or %2$I = nullif(current_setting('app.viewer_id', true), '')::uuid
                      or (%3$L = 'true' and %2$I is null))
               with check (true)$p$,
            r.tbl, r.col, r.shared::text);
    end loop;
end $$;
