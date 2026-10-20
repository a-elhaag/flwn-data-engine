-- Triggers. Idempotent: each one is dropped before it is created.

-- updated_at is kept by the database, so every writer (ORM, raw SQL, other services) is covered.
do $$
declare t text;
begin
    for t in select table_name from information_schema.columns
             where table_schema = 'public' and column_name = 'updated_at' loop
        execute format('drop trigger if exists %I on %I', t || '_set_updated_at', t);
        execute format(
            'create trigger %I before update on %I
             for each row execute function set_updated_at()', t || '_set_updated_at', t);
    end loop;
end $$;

drop trigger if exists tasks_assign_number on tasks;
create trigger tasks_assign_number before insert on tasks
    for each row execute function assign_task_number();

drop trigger if exists events_append_only on events;
create trigger events_append_only before update on events
    for each row execute function events_no_update();
