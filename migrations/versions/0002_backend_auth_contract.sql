-- Identity invariants the backend relies on (flwnBackend docs/AUTH.md §5, §6). Idempotent.
-- Frozen copy of app/db/sql/60_identity.sql as of migration 0002.

-- A user's memberships across every workspace. Row-level security limits a session to one
-- workspace, so this narrow SECURITY DEFINER function lifts it (app.service, for the duration of
-- the call only) and returns nothing but the listed columns. Not executable by PUBLIC: the
-- installer grants it to the application role.
create or replace function memberships_for_user(target_user uuid)
returns table (
    member_id uuid, workspace_id uuid, workspace_name text, workspace_slug text,
    user_id uuid, type text, role text, status text
)
language sql stable security definer
set search_path = pg_catalog, public
set app.service = 'on'
as $$
    select m.id, m.workspace_id, w.name::text, w.slug::text,
           m.user_id, m.type::text, m.role::text, m.status::text
    from public.members m
    join public.workspaces w on w.id = m.workspace_id
    where m.user_id = target_user and m.deleted_at is null and w.deleted_at is null
    order by w.created_at, w.id
$$;

revoke all on function memberships_for_user(uuid) from public;

-- Existing application roles (anything that can read members, apart from the owner and
-- built-in roles) keep working after an upgrade.
do $$
declare r text;
begin
    for r in select rolname from pg_roles
             where not rolsuper and rolname not like 'pg\_%'
               and oid <> (select proowner from pg_proc
                           where oid = 'public.memberships_for_user(uuid)'::regprocedure)
               and has_table_privilege(oid, 'public.members', 'SELECT') loop
        execute format('grant execute on function public.memberships_for_user(uuid) to %I', r);
    end loop;
end $$;

-- Backstop for the last-owner rule: at commit, a workspace that lost an active human owner must
-- still have one. The API checks this first (with the owner rows locked) and answers 409
-- last_owner; this catches any other writer. A deleted workspace is exempt.
create or replace function members_keep_an_owner() returns trigger
language plpgsql security definer
set search_path = pg_catalog, public
set app.service = 'on'
as $$
declare ws uuid := old.workspace_id;
begin
    if not exists (select 1 from public.workspaces where id = ws and deleted_at is null) then
        return null;
    end if;
    perform 1 from public.members
        where workspace_id = ws and role = 'owner' and deleted_at is null
        order by id for update;
    if not exists (
        select 1 from public.members m join public.users u on u.id = m.user_id
        where m.workspace_id = ws and m.role = 'owner' and m.type = 'HUMAN'
          and m.status = 'active' and m.deleted_at is null and u.deleted_at is null
    ) then
        raise exception 'last_owner' using errcode = '23514', constraint = 'last_owner',
            detail = format('workspace %s would have no active human owner', ws);
    end if;
    return null;
end $$;

drop trigger if exists members_keep_an_owner_update on members;
-- Only when the row stops being an active human owner: renaming an owner takes no locks.
create constraint trigger members_keep_an_owner_update after update on members
    deferrable initially deferred for each row
    when (old.role = 'owner' and old.type = 'HUMAN' and old.status = 'active'
          and old.deleted_at is null
          and not (new.role = 'owner' and new.type = 'HUMAN' and new.status = 'active'
                   and new.deleted_at is null))
    execute function members_keep_an_owner();

drop trigger if exists members_keep_an_owner_delete on members;
create constraint trigger members_keep_an_owner_delete after delete on members
    deferrable initially deferred for each row
    when (old.role = 'owner' and old.type = 'HUMAN' and old.status = 'active'
          and old.deleted_at is null)
    execute function members_keep_an_owner();

-- Only a human can own a workspace.
create or replace function members_owner_is_human() returns trigger
language plpgsql set search_path = pg_catalog, public as $$
begin
    if new.role = 'owner' and new.type <> 'HUMAN' then
        raise exception 'AI members cannot be owners' using errcode = '23514',
            constraint = 'members_owner_is_human';
    end if;
    return new;
end $$;

drop trigger if exists members_owner_is_human on members;
create trigger members_owner_is_human before insert or update of role, type on members
    for each row execute function members_owner_is_human();
