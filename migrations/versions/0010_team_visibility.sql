-- A frozen copy of app/db/sql/40_atom_access.sql's atom_owner_allowed as of 0010: teams have no
-- visibility any more, so the owner check no longer reads it. Idempotent.

create or replace function atom_owner_allowed(row_data jsonb) returns boolean
language plpgsql stable security definer set search_path = pg_catalog, public as $$
declare owner_id uuid; ws uuid; team uuid; project uuid; channel uuid;
    parent_data jsonb; parent_key text; parent_table text;
    depth integer := coalesce((row_data->>'_owner_depth')::integer, 0);
begin
    if depth > 16 then return false; end if;
    ws := nullif(current_setting('app.workspace_id', true), '')::uuid;
    select owner_member_id into owner_id from public.atoms where workspace_id = ws
        and id = nullif(current_setting('app.atom_id', true), '')::uuid;
    if owner_id is null then return true; end if;
    if row_data->>'scope' = 'agent' and row_data->>'owner_member_id' not in (
        owner_id::text, current_setting('app.member_id', true)
    ) then return false; end if;
    team := (row_data->>'team_id')::uuid;
    project := (row_data->>'project_id')::uuid;
    channel := (row_data->>'channel_id')::uuid;
    if project is not null then
        if not exists(select 1 from public.projects where id = project and workspace_id = ws and deleted_at is null) then return false; end if;
    end if;
    if team is not null and not exists(select 1 from public.teams t where t.id = team and t.workspace_id = ws
        and t.deleted_at is null) then return false; end if;
    if channel is not null and not exists(select 1 from public.channels ch where ch.id = channel and ch.workspace_id = ws
        and ((not ch.is_private and ch.type not in ('dm','group','agent')) or exists(select 1 from public.channel_members cm
            where cm.workspace_id = ws and cm.channel_id = ch.id and cm.member_id = owner_id))) then return false; end if;
    foreach parent_key in array array['folder_id','parent_folder_id','collection_id','channel_id'] loop
        if row_data->>parent_key is not null then
            parent_table := case parent_key when 'folder_id' then 'folders'
                when 'parent_folder_id' then 'folders' when 'collection_id' then 'collections'
                when 'channel_id' then 'channels' end;
            execute format('select to_jsonb(p) from public.%I p where id = $1 and workspace_id = $2', parent_table)
                into parent_data using (row_data->>parent_key)::uuid, ws;
            if parent_data is null or not public.atom_owner_allowed(
                parent_data || jsonb_build_object('_owner_depth', depth + 1)
            ) then return false; end if;
        end if;
    end loop;
    return true;
end $$;
