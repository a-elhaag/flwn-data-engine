-- Atom schema invariants. Idempotent, like the other installation SQL.

create or replace function atom_version_immutable() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        -- Workspace/atom purges can cascade; a version cannot be removed on its own.
        if exists (select 1 from atoms where id = old.atom_id
                   and workspace_id = old.workspace_id) then
            raise exception 'atom versions are append-only' using errcode = '23514';
        end if;
        return old;
    end if;
    if (to_jsonb(new) - array['status', 'eval'])
       is distinct from (to_jsonb(old) - array['status', 'eval']) then
        raise exception 'only atom version status and eval may be updated' using errcode = '23514';
    end if;
    return new;
end $$;

drop trigger if exists atom_versions_immutable on atom_versions;
create trigger atom_versions_immutable before update or delete on atom_versions
    for each row execute function atom_version_immutable();

create or replace function atom_member_identity() returns trigger
language plpgsql as $$
begin
    if tg_op = 'UPDATE' and new.member_id is distinct from old.member_id then
        raise exception 'an atom member identity cannot be replaced' using errcode = '23514';
    end if;
    if not exists (select 1 from members where workspace_id = new.workspace_id
                   and id = new.member_id and type = 'AI' and agent_kind = 'atom') then
        raise exception 'atom member must be an AI member of kind atom in its workspace'
            using errcode = '23514';
    end if;
    return new;
end $$;

drop trigger if exists atoms_member_identity on atoms;
create trigger atoms_member_identity before insert or update of member_id, workspace_id on atoms
    for each row execute function atom_member_identity();

create or replace function preserve_atom_member_identity() returns trigger
language plpgsql as $$
begin
    if (new.type <> 'AI' or new.agent_kind is distinct from 'atom')
       and exists (select 1 from atoms where member_id = old.id
                   and workspace_id = old.workspace_id) then
        raise exception 'an atom member cannot change its identity kind' using errcode = '23514';
    end if;
    return new;
end $$;

drop trigger if exists members_atom_identity on members;
create trigger members_atom_identity before update of type, agent_kind on members
    for each row execute function preserve_atom_member_identity();

create or replace function atom_schedule_identity() returns trigger
language plpgsql as $$
begin
    if tg_op = 'UPDATE' and (new.atom_id is distinct from old.atom_id
       or new.workspace_id is distinct from old.workspace_id) then
        raise exception 'an atom schedule cannot move between atoms' using errcode = '23514';
    end if;
    if not exists (select 1 from pg_timezone_names where name = new.timezone) then
        raise exception 'unknown schedule timezone' using errcode = '23514';
    end if;
    return new;
end $$;

drop trigger if exists atom_schedules_identity on atom_schedules;
create trigger atom_schedules_identity
    before insert or update of atom_id, workspace_id, timezone on atom_schedules
    for each row execute function atom_schedule_identity();

create or replace function atom_run_identity() returns trigger
language plpgsql as $$
begin
    if new.atom_id is not null then
        if not exists (select 1 from atoms where id = new.atom_id
                       and workspace_id = new.workspace_id and member_id = new.agent_id) then
            raise exception 'atom run agent must match its atom member' using errcode = '23514';
        end if;
        if new.atom_version_id is not null and not exists (
            select 1 from atom_versions where id = new.atom_version_id
            and workspace_id = new.workspace_id and atom_id = new.atom_id
        ) then
            raise exception 'run version must belong to its atom' using errcode = '23514';
        end if;
        if new.schedule_id is not null and not exists (
            select 1 from atom_schedules where id = new.schedule_id
            and workspace_id = new.workspace_id and atom_id = new.atom_id
        ) then
            raise exception 'run schedule must belong to its atom' using errcode = '23514';
        end if;
    end if;
    return new;
end $$;

drop trigger if exists agent_runs_atom_identity on agent_runs;
create trigger agent_runs_atom_identity
    before insert or update of atom_id, atom_version_id, schedule_id, agent_id, workspace_id
    on agent_runs for each row execute function atom_run_identity();
