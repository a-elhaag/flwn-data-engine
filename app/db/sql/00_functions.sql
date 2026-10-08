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

-- The event log is append-only: rows can be added, never rewritten.
create or replace function events_no_update() returns trigger language plpgsql as $$
begin
    raise exception 'events is append-only';
end $$;
