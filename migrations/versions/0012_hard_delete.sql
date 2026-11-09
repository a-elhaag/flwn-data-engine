-- Frozen copy for migration 0012 (hard delete): the atom access, approval and identity
-- functions and triggers from app/db/sql/40_atom_access.sql, 50_atom_approvals.sql and
-- 70_identity.sql as they stand after deleted_at was removed. Idempotent, like the originals.
-- Migrations do not read app/ at run time, so this copy stays fixed.

-- ---- from app/db/sql/40_atom_access.sql ----
-- Restrictive atom policies supplement (never replace) tenant isolation.
-- SECURITY DEFINER helpers resolve grants and canonical parents without recursive RLS.
-- They return booleans only, pin search_path, and check workspace and live run identity.

create or replace function atom_context_valid(allow_finished boolean) returns boolean
language sql stable security definer set search_path = pg_catalog, public as $$
    select exists (
        select 1 from public.atoms a
        join public.members m on m.workspace_id = a.workspace_id and m.id = a.member_id
        join public.agent_runs r on r.workspace_id = a.workspace_id and r.atom_id = a.id
            and r.agent_id = m.id
        where a.workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
          and a.id = nullif(current_setting('app.atom_id', true), '')::uuid
          and m.id = nullif(current_setting('app.member_id', true), '')::uuid
          and r.id = nullif(current_setting('app.run_id', true), '')::uuid
          and m.type = 'AI' and m.agent_kind = 'atom' and m.status = 'active'
          and (nullif(current_setting('app.ctx', true), '') is null
               or current_setting('app.ctx', true) = a.kind)
          and a.status in ('active', 'paused')
          and (r.status in ('running', 'waiting_approval') or (allow_finished
              and r.status in ('succeeded', 'failed', 'cancelled')))
          and (a.kind = 'workspace' or exists (
              select 1 from public.members owner where owner.workspace_id = a.workspace_id
                and owner.id = a.owner_member_id and owner.type = 'HUMAN'
                and owner.status = 'active'
          ))
    )
$$;

create or replace function atom_context_valid() returns boolean
language sql stable security definer set search_path = pg_catalog, public as $$
    select public.atom_context_valid(false)
$$;

create or replace function atom_constraints_match(c jsonb, row_data jsonb) returns boolean
language plpgsql stable set search_path = pg_catalog, public as $$
declare labels jsonb; days numeric;
begin
    if c is null or jsonb_typeof(c) <> 'object' or (c - array['labels','since_days']) <> '{}'::jsonb then
        return false;
    end if;
    if c ? 'labels' then
        if jsonb_typeof(c->'labels') <> 'array' or exists (
            select 1 from jsonb_array_elements(c->'labels') v where jsonb_typeof(v) <> 'string'
        ) then return false; end if;
        labels := coalesce(row_data->'labels', row_data->'metadata'->'labels', '[]'::jsonb);
        if jsonb_typeof(labels) <> 'array' or not labels @> (c->'labels') then return false; end if;
    end if;
    if c ? 'since_days' then
        if jsonb_typeof(c->'since_days') <> 'number' then return false; end if;
        days := (c->>'since_days')::numeric;
        if days < 0 or days > 365000 or days <> trunc(days) or row_data->>'created_at' is null then
            return false;
        end if;
        if (row_data->>'created_at')::timestamptz < now() - days * interval '1 day' then return false; end if;
    end if;
    return true;
exception when others then return false;
end $$;

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
        if not exists(select 1 from public.projects where id = project and workspace_id = ws) then return false; end if;
    end if;
    if team is not null and not exists(select 1 from public.teams t where t.id = team and t.workspace_id = ws) then return false; end if;
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

create or replace function atom_grant_matches(resource text, resource_id uuid, row_data jsonb, required_level text)
returns boolean language sql stable security definer set search_path = pg_catalog, public as $$
    select required_level in ('summary','read','write') and exists (
        select 1 from public.atom_grants g
        where g.workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
          and g.atom_id = nullif(current_setting('app.atom_id', true), '')::uuid
          and g.resource_type = $1 and (g.resource_id is null or g.resource_id = $2)
          and case required_level when 'write' then g.level = 'write'
              when 'read' then g.level in ('read','write') else true end
          and public.atom_constraints_match(g.constraints, row_data)
    )
$$;

-- Forward declaration permits bounded recursion through canonical parent rows.
create or replace function atom_row_allowed(table_name text, row_data jsonb, required_level text default 'read', depth integer default 0)
returns boolean language plpgsql stable security definer set search_path = pg_catalog, public as $$
begin return false; end $$;

create or replace function atom_parent_allowed(parent_table text, parent_id text, required_level text, depth integer)
returns boolean language plpgsql stable security definer set search_path = pg_catalog, public as $$
declare data jsonb;
begin
    if parent_id is null or depth > 16 or parent_table not in (
        'memories','files','docs','messages','meetings','transcripts','tasks','work_items',
        'projects','project_updates','sprints','comments','agent_reports','channels','folders','collections'
    ) then return false; end if;
    execute format('select to_jsonb(p) from public.%I p where p.id = $1 and p.workspace_id = $2', parent_table)
        into data using parent_id::uuid, nullif(current_setting('app.workspace_id', true), '')::uuid;
    return data is not null and public.atom_row_allowed(parent_table, data, required_level, depth + 1);
end $$;

create or replace function atom_row_allowed(table_name text, row_data jsonb, required_level text default 'read', depth integer default 0)
returns boolean language plpgsql stable security definer set search_path = pg_catalog, public as $$
declare ws uuid; atom uuid; member uuid; resource text; resource_id uuid;
    parent_table text; key text; has_parent boolean := false; allowed boolean := false;
    data jsonb := row_data; owner_id uuid; inherited_team uuid;
begin
    if nullif(current_setting('app.atom_id', true), '') is null then return true; end if;
    if depth > 16 or required_level not in ('summary','read','write') or not public.atom_context_valid() then return false; end if;
    ws := nullif(current_setting('app.workspace_id', true), '')::uuid;
    atom := current_setting('app.atom_id')::uuid;
    member := current_setting('app.member_id')::uuid;
    if table_name <> 'catalog_skills' and (row_data->>'workspace_id')::uuid is distinct from ws then return false; end if;
    select owner_member_id into owner_id from public.atoms where workspace_id = ws and id = atom;

    if table_name in ('atoms','atom_versions','atom_grants','atom_connections','atom_schedules','atom_skills','agent_runs') then
        if required_level = 'write' then return false; end if;
        return case when table_name = 'atoms' then row_data->>'id' = atom::text
                    else row_data->>'atom_id' = atom::text end;
    end if;
    -- Paused runs retain lifecycle metadata for reporting/finishing, never raw data access.
    if not exists(select 1 from public.atoms where workspace_id = ws and id = atom
        and status = 'active') then return false; end if;
    if table_name = 'members' then return required_level <> 'write' and row_data->>'id' = member::text; end if;
    if table_name = 'catalog_skills' then return required_level <> 'write'; end if;
    if table_name = 'skills' then
        return required_level <> 'write' and (row_data->>'scope' = 'workspace'
            or row_data->>'owner_member_id' in (member::text, owner_id::text));
    end if;

    if table_name = 'tasks' then
        data := data || jsonb_build_object('labels', coalesce((
            select jsonb_agg(l.name) from public.task_labels tl join public.labels l
                on l.id = tl.label_id and l.workspace_id = ws
            where tl.workspace_id = ws and tl.task_id = (row_data->>'id')::uuid
        ), '[]'::jsonb));
    elsif table_name = 'work_items' then
        data := data || jsonb_build_object('labels', coalesce((
            select jsonb_agg(l.name) from public.work_item_labels wl join public.labels l
                on l.id = wl.label_id and l.workspace_id = ws
            where wl.workspace_id = ws and wl.work_item_id = (row_data->>'id')::uuid
        ), '[]'::jsonb));
    elsif table_name = 'messages' then
        select to_jsonb(ch) || row_data into data from public.channels ch
            where ch.id = (row_data->>'channel_id')::uuid and ch.workspace_id = ws;
        if data is null then return false; end if;
    end if;

    -- Derived rows must authorize their actual source, never just denormalized project tags.
    if table_name = 'chunks' then
        parent_table := case row_data->>'source_type'
            when 'file' then 'files' when 'doc' then 'docs' when 'task' then 'tasks'
            when 'work_item' then 'work_items' when 'comment' then 'comments'
            when 'message' then 'messages' when 'transcript' then 'transcripts'
            when 'report' then 'agent_reports' when 'project_update' then 'project_updates' end;
        return required_level <> 'write' and public.atom_parent_allowed(parent_table,row_data->>'source_id',required_level,depth);
    end if;
    if table_name in ('file_derivatives','doc_blocks','doc_versions','transcript_segments','decisions','memory_links',
                     'decision_conflicts','attachments','recordings','transcripts','comments','reactions',
                     'task_relations','task_labels','work_item_labels') then
        if required_level = 'write' then return false; end if;
        foreach key in array array['file_id','doc_id','transcript_id','memory_id','from_memory_id','to_memory_id',
            'decision_id','meeting_id','message_id','comment_id','task_id','related_task_id','work_item_id',
            'project_update_id','project_id','sprint_id','report_id','channel_id'] loop
            if row_data->>key is not null then
                parent_table := case key
                    when 'file_id' then 'files' when 'doc_id' then 'docs' when 'transcript_id' then 'transcripts'
                    when 'memory_id' then 'memories' when 'from_memory_id' then 'memories' when 'to_memory_id' then 'memories'
                    when 'decision_id' then 'memories' when 'meeting_id' then 'meetings' when 'message_id' then 'messages'
                    when 'comment_id' then 'comments' when 'task_id' then 'tasks' when 'related_task_id' then 'tasks'
                    when 'work_item_id' then 'work_items' when 'project_update_id' then 'project_updates'
                    when 'project_id' then 'projects' when 'sprint_id' then 'sprints' when 'report_id' then 'agent_reports'
                    when 'channel_id' then 'channels' end;
                has_parent := true;
                if not public.atom_parent_allowed(parent_table,row_data->>key,required_level,depth) then return false; end if;
            end if;
        end loop;
        return has_parent;
    end if;

    if table_name not in ('memories','files','docs','meetings','messages','projects','teams','collections','channels','folders',
        'tasks','work_items','project_updates','sprints','milestones','backlogs','agent_reports') then return false; end if;
    if required_level = 'write' then
        if owner_id is not null and exists(select 1 from public.members where id = owner_id
            and workspace_id = ws and role = 'viewer') then return false; end if;
        -- Existing memory/file services must attribute writes and cannot manufacture shared/private scope.
        if table_name = 'memories' then
            if row_data->>'created_by' is distinct from member::text or row_data->>'scope' <> 'agent'
                or row_data->>'owner_member_id' is distinct from member::text then return false; end if;
        elsif table_name = 'files' then
            if row_data->>'uploaded_by' is distinct from member::text then return false; end if;
        else return false; end if;
    end if;
    if table_name = 'memories' and row_data->>'scope' = 'agent'
        and row_data->>'owner_member_id' not in (member::text, coalesce(owner_id::text, member::text)) then return false; end if;

    resource := case table_name when 'memories' then 'memory' when 'projects' then 'project'
        when 'teams' then 'team' when 'collections' then 'collection' when 'channels' then 'channel'
        when 'folders' then 'folder' when 'meetings' then 'meeting' end;
    resource_id := (row_data->>'id')::uuid;
    if resource is not null then
        if resource in ('project','team','channel') then data := data || jsonb_build_object(resource || '_id', resource_id); end if;
        allowed := public.atom_grant_matches(resource,resource_id,data,required_level);
    end if;
    -- Intersect every personal grant with owner visibility.
    if data->>'project_id' is not null then
        select team_id into inherited_team from public.projects where id = (data->>'project_id')::uuid and workspace_id = ws;
        if inherited_team is not null and data->>'team_id' is null then data := data || jsonb_build_object('team_id',inherited_team); end if;
    end if;
    if not public.atom_owner_allowed(data) then return false; end if;
    foreach resource in array array['project','team','collection','channel','folder','meeting'] loop
        if data->>(resource || '_id') is not null then
            allowed := allowed or public.atom_grant_matches(resource,(data->>(resource || '_id'))::uuid,data,required_level);
        end if;
    end loop;
    return allowed;
exception when invalid_text_representation or numeric_value_out_of_range then return false;
end $$;

-- Auditing approved memory writes does not grant access to the audit table itself.
create or replace function atom_record_memory_event(memory_id uuid, event_action text, event_changes jsonb)
returns void language plpgsql volatile security definer set search_path = pg_catalog, public as $$
declare ws uuid := nullif(current_setting('app.workspace_id',true),'')::uuid;
begin
    if not public.atom_context_valid() or event_action not in ('created','reconfirmed','updated','pinned','unpinned')
        or not public.atom_parent_allowed('memories',memory_id::text,'write',0) then
        raise exception 'No live atom grant permits this memory event' using errcode = '42501';
    end if;
    insert into public.events(workspace_id,actor_id,entity_type,entity_id,action,changes,run_id)
        values(ws,current_setting('app.member_id')::uuid,'memory',memory_id,event_action,
               coalesce(event_changes,'{}'::jsonb),current_setting('app.run_id')::uuid);
end $$;

-- Protect every tenant table, including future/unrecognized surfaces (deny by default).
do $$
declare t text; read_rule text; write_rule text;
begin
    for t in select table_name from information_schema.columns
        where table_schema = 'public' and column_name = 'workspace_id'
        union select 'catalog_skills' union select 'workspaces' union select 'users' loop
        execute format('alter table public.%I enable row level security', t);
        execute format('alter table public.%I force row level security', t);
        if t in ('catalog_skills','users') then
            execute format('drop policy if exists atom_base on public.%I', t);
            execute format('create policy atom_base on public.%I using (true) with check (true)', t);
        end if;
        read_rule := format('public.atom_row_allowed(%L,to_jsonb(%I),case when current_setting(''app.atom_aggregate'',true) = ''on'' then ''summary'' else ''read'' end)',t,t);
        write_rule := format('public.atom_row_allowed(%L,to_jsonb(%I),''write'')',t,t);
        execute format('drop policy if exists atom_select on public.%I', t);
        execute format('drop policy if exists atom_insert on public.%I', t);
        execute format('drop policy if exists atom_update on public.%I', t);
        execute format('drop policy if exists atom_delete on public.%I', t);
        execute format('create policy atom_select on public.%I as restrictive for select using (%s)',t,read_rule);
        execute format('create policy atom_insert on public.%I as restrictive for insert with check (%s)',t,write_rule);
        execute format('create policy atom_update on public.%I as restrictive for update using (%s) with check (%s)',t,write_rule,write_rule);
        execute format('create policy atom_delete on public.%I as restrictive for delete using (%s)',t,write_rule);
    end loop;
end $$;

-- ---- from app/db/sql/50_atom_approvals.sql ----
-- Additive approval contract; legacy rows (atom_id IS NULL) retain their semantics.
-- Columns/constraints are installed from metadata by upgrade_atoms before this SQL.
create or replace function atom_approval_contract() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        if old.atom_id is not null and exists (
            select 1 from workspaces where id = old.workspace_id
        ) then
            raise exception 'atom approvals are durable execution records' using errcode = '23514';
        end if;
        return old;
    end if;
    if tg_op = 'UPDATE' and old.atom_id is not null then
        if not exists (select 1 from workspaces where id = old.workspace_id) then return new; end if;
        if (to_jsonb(new) - array['status','decided_by','decision_comment','decided_at',
                'execution_run_id','claimed_at','executed_at','execution_status','execution_result'])
            is distinct from
           (to_jsonb(old) - array['status','decided_by','decision_comment','decided_at',
                'execution_run_id','claimed_at','executed_at','execution_status','execution_result']) then
            raise exception 'atom approval request is immutable' using errcode = '23514';
        end if;
        if old.status <> 'pending' and
            row(new.status,new.decided_by,new.decision_comment,new.decided_at) is distinct from
            row(old.status,old.decided_by,old.decision_comment,old.decided_at) then
            raise exception 'atom approval decision is immutable' using errcode = '23514';
        end if;
        if old.execution_run_id is not null and new.execution_run_id is distinct from old.execution_run_id then
            raise exception 'approval execution run cannot be replaced' using errcode = '23514';
        end if;
        if old.claimed_at is not null and new.claimed_at is distinct from old.claimed_at then
            raise exception 'approval claim cannot be replaced' using errcode = '23514';
        end if;
        if old.executed_at is not null and
            row(new.executed_at,new.execution_status,new.execution_result) is distinct from
            row(old.executed_at,old.execution_status,old.execution_result) then
            raise exception 'approval outcome is immutable' using errcode = '23514';
        end if;
    elsif tg_op = 'UPDATE' and new.atom_id is not null then
        raise exception 'legacy approvals cannot become execution approvals' using errcode = '23514';
    end if;
    if new.atom_id is null then return new; end if;
    if tg_op = 'INSERT' and (new.status <> 'pending' or new.decided_by is not null
        or new.decided_at is not null or new.execution_run_id is not null
        or new.claimed_at is not null or new.executed_at is not null) then
        raise exception 'new atom approval must be pending and unclaimed' using errcode = '23514';
    end if;
    if not exists (
        select 1 from atoms a join agent_runs r on r.workspace_id = a.workspace_id and r.atom_id = a.id
        where a.workspace_id = new.workspace_id and a.id = new.atom_id
          and a.member_id = new.requested_by and r.agent_id = a.member_id and r.id = new.run_id
    ) then
        raise exception 'approval requester must match its atom and originating run' using errcode = '23514';
    end if;
    if tg_op = 'UPDATE' and new.status is distinct from old.status
        and new.status in ('approved','rejected') then
        if new.expires_at <= clock_timestamp() then
            raise exception 'approval has expired' using errcode = '23514';
        end if;
        if new.decided_at is null or not exists (
            select 1 from members m join atoms a on a.workspace_id = m.workspace_id
            where m.workspace_id = new.workspace_id and m.id = new.decided_by and a.id = new.atom_id
              and m.type = 'HUMAN' and m.status = 'active' and m.role <> 'viewer'
              and (new.assigned_to = m.id or (new.assigned_to is null
                and (m.role in ('owner','admin') or a.owner_member_id = m.id)))
        ) then
            raise exception 'authorized human decision required' using errcode = '23514';
        end if;
    end if;
    if new.execution_run_id is not null and not exists (
        select 1 from agent_runs r where r.workspace_id = new.workspace_id
          and r.id = new.execution_run_id and r.atom_id = new.atom_id and r.agent_id = new.requested_by
          and r.schedule_id is null and r.idempotency_key = 'approval:' || new.id::text
          and r.input->>'approval_id' = new.id::text and r.input->'approval_payload' = new.payload
    ) then
        raise exception 'execution run must carry the immutable approval payload' using errcode = '23514';
    end if;
    if tg_op = 'UPDATE' and ((old.execution_run_id is null and new.execution_run_id is not null)
        or (old.claimed_at is null and new.claimed_at is not null)) then
        if new.status <> 'approved' or new.expires_at <= clock_timestamp() then
            raise exception 'approval is not approved or has expired' using errcode = '23514';
        end if;
        if not exists (select 1 from agent_runs r join atoms a on a.workspace_id = r.workspace_id
            and a.id = r.atom_id where r.workspace_id = new.workspace_id and r.id = new.execution_run_id
            and r.status = 'running' and a.status = 'active') then
            raise exception 'approval requires an active execution run' using errcode = '23514';
        end if;
    end if;
    return new;
end $$;

drop trigger if exists approvals_atom_contract on approvals;
create trigger approvals_atom_contract before insert or update or delete on approvals
    for each row execute function atom_approval_contract();

create or replace function atom_approval_run_contract() returns trigger
language plpgsql as $$
begin
    if tg_op = 'DELETE' then
        if old.atom_id is not null and old.input ? 'approval_id' and exists (
            select 1 from workspaces where id = old.workspace_id
        ) then
            raise exception 'approval execution runs cannot be deleted' using errcode = '23514';
        end if;
        return old;
    end if;
    if tg_op = 'UPDATE' and old.atom_id is not null and old.input ? 'approval_id' then
        if not exists (select 1 from workspaces where id = old.workspace_id) then return new; end if;
        if row(new.workspace_id,new.atom_id,new.agent_id,new.input,new.idempotency_key,new.schedule_id)
            is distinct from row(old.workspace_id,old.atom_id,old.agent_id,old.input,old.idempotency_key,old.schedule_id) then
            raise exception 'approval run identity and input are immutable' using errcode = '23514';
        end if;
    end if;
    if new.atom_id is not null and new.input ? 'approval_id' then
        if tg_op = 'UPDATE' and not (old.atom_id is not null and old.input ? 'approval_id') then
            raise exception 'existing runs cannot become approval execution runs' using errcode = '23514';
        end if;
        if tg_op = 'INSERT' and not exists (
            select 1 from approvals a where a.workspace_id = new.workspace_id
              and a.id::text = new.input->>'approval_id' and a.status = 'approved'
              and (a.expires_at is null or a.expires_at > clock_timestamp())
              and a.execution_run_id is null and a.claimed_at is null
        ) then
            raise exception 'fresh approved unexpired request required' using errcode = '23514';
        end if;
        if not exists (select 1 from approvals a where a.workspace_id = new.workspace_id
            and a.id::text = new.input->>'approval_id' and a.atom_id = new.atom_id
            and a.requested_by = new.agent_id and a.payload = new.input->'approval_payload'
            and new.idempotency_key = 'approval:' || a.id::text and new.schedule_id is null
            and (a.execution_run_id is null or a.execution_run_id = new.id)) then
            raise exception 'invalid approval execution run' using errcode = '23514';
        end if;
    end if;
    return new;
end $$;

drop trigger if exists agent_runs_approval_contract on agent_runs;
create trigger agent_runs_approval_contract before insert or update or delete on agent_runs
    for each row execute function atom_approval_run_contract();

-- No raw approval writes for atom sessions. Read only originating/executing run records;
-- the lifecycle service independently rechecks these bounds before privileged writes.
create or replace function atom_approval_visible(row_data jsonb) returns boolean
language sql stable security definer set search_path = pg_catalog, public as $$
    select nullif(current_setting('app.atom_id',true),'') is null or (
        public.atom_context_valid(true)
        and row_data->>'workspace_id' = current_setting('app.workspace_id',true)
        and row_data->>'atom_id' = current_setting('app.atom_id',true)
        and row_data->>'requested_by' = current_setting('app.member_id',true)
        and current_setting('app.run_id',true) in (row_data->>'run_id',row_data->>'execution_run_id')
    )
$$;

drop policy if exists atom_select on approvals;
create policy atom_select on approvals as restrictive for select
    using (public.atom_approval_visible(to_jsonb(approvals)));

-- ---- from app/db/sql/70_identity.sql ----
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
language sql stable security definer
set search_path = pg_catalog, public
set app.service = 'on'
as $$
    select m.id, m.workspace_id, w.name::text, w.slug::text,
           m.user_id, m.type::text, m.role::text, m.status::text
    from public.members m
    join public.workspaces w on w.id = m.workspace_id
    where m.user_id = target_user
    order by w.created_at, w.id
$$;

revoke all on function memberships_for_user(uuid) from public;

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
    if not exists (select 1 from public.workspaces where id = ws) then
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
