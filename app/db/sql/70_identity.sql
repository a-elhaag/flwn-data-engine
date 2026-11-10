-- Identity invariants the backend relies on (flwnBackend docs/AUTH.md §5, §6). Idempotent.
-- Migration 0005 creates the same objects; keep the two in step.

-- A user's memberships across every workspace. Row-level security limits a session to one
-- workspace, so this narrow SECURITY DEFINER function lifts it (app.service, for the duration of
-- the call only) and returns nothing but the listed columns. Not executable by PUBLIC: only the
-- application role gets it (app/db/install.py, and migration 0005 for existing databases).
create or replace function memberships_for_user(target_user uuid)
returns table (
    member_id uuid, workspace_id uuid, workspace_name text, workspace_slug text,
    user_id uuid, type text, role text, status text
)
language plpgsql security definer
set search_path = pg_catalog, public
as $$
declare prev text := current_setting('app.service', true);
begin
    -- app.service lifts row-level security for this call only: set for the transaction,
    -- then put back as it was (SET in a function definition is refused on Azure Postgres).
    perform set_config('app.service', 'on', true);
    return query
    select m.id, m.workspace_id, w.name::text, w.slug::text,
           m.user_id, m.type::text, m.role::text, m.status::text
    from public.members m
    join public.workspaces w on w.id = m.workspace_id
    where m.user_id = target_user
    order by w.created_at, w.id;
    perform set_config('app.service', coalesce(prev, ''), true);
end $$;

revoke all on function memberships_for_user(uuid) from public;

-- Backstop for the last-owner rule: at commit, a workspace that lost an active human owner must
-- still have one. The API checks this first (with the owner rows locked) and answers 409
-- last_owner; this catches any other writer. A deleted workspace is exempt.
create or replace function members_keep_an_owner() returns trigger
language plpgsql security definer
set search_path = pg_catalog, public
as $$
declare ws uuid := old.workspace_id;
  prev text := current_setting('app.service', true);
begin
    perform set_config('app.service', 'on', true);
    if not exists (select 1 from public.workspaces where id = ws) then
        perform set_config('app.service', coalesce(prev, ''), true);
        return null;
    end if;
    perform 1 from public.members
        where workspace_id = ws and role = 'owner'
        order by id for update;
    if not exists (
        select 1 from public.members m join public.users u on u.id = m.user_id
        where m.workspace_id = ws and m.role = 'owner' and m.type = 'HUMAN'
          and m.status = 'active'
    ) then
        raise exception 'last_owner' using errcode = '23514', constraint = 'last_owner',
            detail = format('workspace %s would have no active human owner', ws);
    end if;
    perform set_config('app.service', coalesce(prev, ''), true);
    return null;
end $$;

drop trigger if exists members_keep_an_owner_update on members;
-- Only when the row stops being an active human owner: renaming an owner takes no locks.
create constraint trigger members_keep_an_owner_update after update on members
    deferrable initially deferred for each row
    when (old.role = 'owner' and old.type = 'HUMAN' and old.status = 'active'
          and not (new.role = 'owner' and new.type = 'HUMAN' and new.status = 'active'))
    execute function members_keep_an_owner();

drop trigger if exists members_keep_an_owner_delete on members;
create constraint trigger members_keep_an_owner_delete after delete on members
    deferrable initially deferred for each row
    when (old.role = 'owner' and old.type = 'HUMAN' and old.status = 'active')
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

-- A name must not be blank and an email must look like an address, whichever path writes them.
-- The API checks the same rules first (app/api/schemas/identity.py) and answers 422.
alter table users drop constraint if exists ck_users_name_not_blank;
alter table users add constraint ck_users_name_not_blank check (name ~ '\S');
alter table users drop constraint if exists ck_users_email_shape;
alter table users add constraint ck_users_email_shape
    check (email ~ '^[^\s]+@[^\s@]+\.[^\s@]+$');
