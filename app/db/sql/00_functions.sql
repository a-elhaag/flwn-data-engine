-- Helper functions. Idempotent: safe to re-run on an existing database.

create or replace function set_updated_at() returns trigger language plpgsql as $$
begin
    new.updated_at = clock_timestamp();  -- real time, not the transaction's start
    return new;
end $$;

-- Per-project task numbers (FLWN-12): the counter lives on the project row.
create or replace function assign_task_number() returns trigger language plpgsql as $$
begin
    if new.number is null then
        update projects set task_counter = task_counter + 1
        where id = new.project_id
        returning task_counter into new.number;
    end if;
    return new;
end $$;

-- The event log is append-only: rows can be added, never rewritten. The one change allowed is
-- clearing actor_id when its member is removed (the foreign key's ON DELETE SET NULL), so the
-- history stays but a member can still be deleted.
create or replace function events_no_update() returns trigger language plpgsql as $$
begin
    if old.actor_id is not null and new.actor_id is null
       and (new.workspace_id, new.entity_type, new.entity_id, new.action, new.changes,
            new.run_id, new.request_id, new.created_at)
           is not distinct from
           (old.workspace_id, old.entity_type, old.entity_id, old.action, old.changes,
            old.run_id, old.request_id, old.created_at) then
        return new;
    end if;
    raise exception 'events is append-only';
end $$;
