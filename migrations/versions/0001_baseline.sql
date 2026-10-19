--
-- PostgreSQL database dump
--

-- Dumped from database version 16.2
-- Dumped by pg_dump version 16.2

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

--
-- Name: assign_task_number(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.assign_task_number() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    if new.number is null then
        update projects set task_counter = task_counter + 1
        where id = new.project_id
        returning task_counter into new.number;
    end if;
    return new;
end $$;

--
-- Name: atom_approval_contract(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_approval_contract() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
              and m.type = 'HUMAN' and m.status = 'active' and m.deleted_at is null and m.role <> 'viewer'
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
            and r.status = 'running' and a.status = 'active' and a.deleted_at is null) then
            raise exception 'approval requires an active execution run' using errcode = '23514';
        end if;
    end if;
    return new;
end $$;

--
-- Name: atom_approval_run_contract(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_approval_run_contract() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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

--
-- Name: atom_approval_visible(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_approval_visible(row_data jsonb) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
    select nullif(current_setting('app.atom_id',true),'') is null or (
        public.atom_context_valid(true)
        and row_data->>'workspace_id' = current_setting('app.workspace_id',true)
        and row_data->>'atom_id' = current_setting('app.atom_id',true)
        and row_data->>'requested_by' = current_setting('app.member_id',true)
        and current_setting('app.run_id',true) in (row_data->>'run_id',row_data->>'execution_run_id')
    )
$$;

--
-- Name: atom_constraints_match(jsonb, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_constraints_match(c jsonb, row_data jsonb) RETURNS boolean
    LANGUAGE plpgsql STABLE
    SET search_path TO 'pg_catalog', 'public'
    AS $$
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

--
-- Name: atom_context_valid(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_context_valid() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
    select public.atom_context_valid(false)
$$;

--
-- Name: atom_context_valid(boolean); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_context_valid(allow_finished boolean) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
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
          and m.deleted_at is null and a.deleted_at is null
          and (nullif(current_setting('app.ctx', true), '') is null
               or current_setting('app.ctx', true) = a.kind)
          and a.status in ('active', 'paused')
          and (r.status in ('running', 'waiting_approval') or (allow_finished
              and r.status in ('succeeded', 'failed', 'cancelled')))
          and (a.kind = 'workspace' or exists (
              select 1 from public.members owner where owner.workspace_id = a.workspace_id
                and owner.id = a.owner_member_id and owner.type = 'HUMAN'
                and owner.status = 'active' and owner.deleted_at is null
          ))
    )
$$;

--
-- Name: atom_grant_matches(text, uuid, jsonb, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_grant_matches(resource text, resource_id uuid, row_data jsonb, required_level text) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $_$
    select required_level in ('summary','read','write') and exists (
        select 1 from public.atom_grants g
        where g.workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
          and g.atom_id = nullif(current_setting('app.atom_id', true), '')::uuid
          and g.resource_type = $1 and (g.resource_id is null or g.resource_id = $2)
          and case required_level when 'write' then g.level = 'write'
              when 'read' then g.level in ('read','write') else true end
          and public.atom_constraints_match(g.constraints, row_data)
    )
$_$;

--
-- Name: atom_member_identity(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_member_identity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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

--
-- Name: atom_owner_allowed(jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_owner_allowed(row_data jsonb) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $_$
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
        if exists(select 1 from public.projects p join public.teams t on t.id = p.team_id
            and t.workspace_id = ws where p.id = project and p.workspace_id = ws
            and t.visibility = 'private' and not exists(select 1 from public.team_members tm
                where tm.workspace_id = ws and tm.team_id = t.id and tm.member_id = owner_id)) then return false; end if;
    end if;
    if team is not null and not exists(select 1 from public.teams t where t.id = team and t.workspace_id = ws
        and t.deleted_at is null and (t.visibility = 'workspace' or exists(select 1 from public.team_members tm
            where tm.workspace_id = ws and tm.team_id = t.id and tm.member_id = owner_id))) then return false; end if;
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
end $_$;

--
-- Name: atom_parent_allowed(text, text, text, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_parent_allowed(parent_table text, parent_id text, required_level text, depth integer) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $_$
declare data jsonb;
begin
    if parent_id is null or depth > 16 or parent_table not in (
        'memories','files','docs','messages','meetings','transcripts','tasks','work_items',
        'projects','project_updates','sprints','comments','agent_reports','channels','folders','collections'
    ) then return false; end if;
    execute format('select to_jsonb(p) from public.%I p where p.id = $1 and p.workspace_id = $2', parent_table)
        into data using parent_id::uuid, nullif(current_setting('app.workspace_id', true), '')::uuid;
    return data is not null and public.atom_row_allowed(parent_table, data, required_level, depth + 1);
end $_$;

--
-- Name: atom_record_memory_event(uuid, text, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_record_memory_event(memory_id uuid, event_action text, event_changes jsonb) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
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

--
-- Name: atom_row_allowed(text, jsonb, text, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_row_allowed(table_name text, row_data jsonb, required_level text DEFAULT 'read'::text, depth integer DEFAULT 0) RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
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
    -- Intersect every personal grant with owner visibility, including inherited private teams.
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

--
-- Name: atom_run_identity(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_run_identity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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

--
-- Name: atom_schedule_identity(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_schedule_identity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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

--
-- Name: atom_version_immutable(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.atom_version_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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

--
-- Name: events_no_update(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.events_no_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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

--
-- Name: preserve_atom_member_identity(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.preserve_atom_member_identity() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    if (new.type <> 'AI' or new.agent_kind is distinct from 'atom')
       and exists (select 1 from atoms where member_id = old.id
                   and workspace_id = old.workspace_id) then
        raise exception 'an atom member cannot change its identity kind' using errcode = '23514';
    end if;
    return new;
end $$;

--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    new.updated_at = clock_timestamp();  -- real time, not the transaction's start
    return new;
end $$;

--
-- Name: agent_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_reports (
    run_id uuid,
    agent_id uuid,
    kind character varying NOT NULL,
    title character varying NOT NULL,
    summary character varying,
    body character varying,
    container character varying,
    blob_path character varying,
    external_url character varying,
    project_id uuid,
    work_item_id uuid,
    task_id uuid,
    sprint_id uuid,
    meeting_id uuid,
    status character varying DEFAULT 'draft'::character varying NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_agent_reports_has_content CHECK (((body IS NOT NULL) OR (blob_path IS NOT NULL))),
    CONSTRAINT ck_agent_reports_kind CHECK (((kind)::text = ANY ((ARRAY['plan'::character varying, 'review'::character varying, 'security'::character varying, 'architecture_debate'::character varying, 'sprint_summary'::character varying, 'standup'::character varying, 'conflict'::character varying, 'meeting_summary'::character varying, 'behavior'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT ck_agent_reports_status CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'published'::character varying, 'archived'::character varying])::text[])))
);

ALTER TABLE ONLY public.agent_reports FORCE ROW LEVEL SECURITY;

--
-- Name: agent_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_runs (
    agent_id uuid NOT NULL,
    atom_id uuid,
    atom_version_id uuid,
    schedule_id uuid,
    idempotency_key character varying,
    tokens_in integer DEFAULT 0 NOT NULL,
    tokens_out integer DEFAULT 0 NOT NULL,
    cost numeric(12,6) DEFAULT '0'::numeric NOT NULL,
    trigger character varying NOT NULL,
    triggered_by uuid,
    project_id uuid,
    work_item_id uuid,
    task_id uuid,
    channel_id uuid,
    meeting_id uuid,
    thread_id character varying,
    status character varying DEFAULT 'queued'::character varying NOT NULL,
    input jsonb DEFAULT '{}'::jsonb NOT NULL,
    output jsonb,
    error character varying,
    model character varying,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_agent_runs_atom_schedule_only CHECK (((atom_id IS NULL) OR ((trigger)::text = 'schedule'::text))),
    CONSTRAINT ck_agent_runs_nonnegative_usage CHECK (((tokens_in >= 0) AND (tokens_out >= 0) AND (cost >= (0)::numeric) AND (cost < 'Infinity'::numeric))),
    CONSTRAINT ck_agent_runs_status CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'waiting_approval'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'canceled'::character varying])::text[]))),
    CONSTRAINT ck_agent_runs_trigger CHECK (((trigger)::text = ANY ((ARRAY['chat'::character varying, 'ticket_event'::character varying, 'schedule'::character varying, 'user'::character varying, 'meeting'::character varying, 'webhook'::character varying])::text[])))
);

ALTER TABLE ONLY public.agent_runs FORCE ROW LEVEL SECURITY;

--
-- Name: approvals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.approvals (
    atom_id uuid,
    title character varying,
    request_key character varying,
    execution_run_id uuid,
    claimed_at timestamp with time zone,
    executed_at timestamp with time zone,
    execution_status character varying,
    execution_result jsonb,
    run_id uuid,
    report_id uuid,
    kind character varying NOT NULL,
    requested_by uuid,
    assigned_to uuid,
    status character varying DEFAULT 'pending'::character varying NOT NULL,
    decided_by uuid,
    decision_comment character varying,
    decided_at timestamp with time zone,
    expires_at timestamp with time zone,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_approvals_atom_execution CHECK (((atom_id IS NULL) OR (((claimed_at IS NULL) OR (execution_run_id IS NOT NULL)) AND ((execution_status IS NULL) OR ((claimed_at IS NOT NULL) AND (executed_at IS NOT NULL))) AND ((executed_at IS NULL) OR (execution_status IS NOT NULL))))),
    CONSTRAINT ck_approvals_atom_payload CHECK (((atom_id IS NULL) OR ((run_id IS NOT NULL) AND (requested_by IS NOT NULL) AND (title IS NOT NULL) AND (btrim((title)::text) <> ''::text) AND (request_key IS NOT NULL) AND (btrim((request_key)::text) <> ''::text) AND (jsonb_typeof(payload) = 'object'::text) AND (payload ?& ARRAY['tool_slug'::text, 'arguments'::text, 'connection_id'::text]) AND (jsonb_typeof((payload -> 'tool_slug'::text)) = 'string'::text) AND (btrim((payload ->> 'tool_slug'::text)) <> ''::text) AND (jsonb_typeof((payload -> 'arguments'::text)) = 'object'::text) AND (jsonb_typeof((payload -> 'connection_id'::text)) = 'string'::text)))),
    CONSTRAINT ck_approvals_execution_status CHECK (((execution_status)::text = ANY ((ARRAY['succeeded'::character varying, 'failed'::character varying, 'unknown'::character varying])::text[]))),
    CONSTRAINT ck_approvals_kind CHECK (((kind)::text = ANY ((ARRAY['plan'::character varying, 'pull_request'::character varying, 'deploy'::character varying, 'action'::character varying, 'atom_action'::character varying])::text[]))),
    CONSTRAINT ck_approvals_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'approved'::character varying, 'rejected'::character varying, 'expired'::character varying, 'canceled'::character varying])::text[])))
);

ALTER TABLE ONLY public.approvals FORCE ROW LEVEL SECURITY;

--
-- Name: atom_connections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.atom_connections (
    atom_id uuid NOT NULL,
    toolkit character varying NOT NULL,
    composio_user_id character varying NOT NULL,
    composio_account_ref character varying,
    toolkit_version character varying NOT NULL,
    permission_ceiling character varying NOT NULL,
    allowed_tools jsonb DEFAULT '[]'::jsonb NOT NULL,
    status character varying DEFAULT 'unknown'::character varying NOT NULL,
    status_checked_at timestamp with time zone,
    status_detail character varying,
    connected_by uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_atom_connections_active_account_required CHECK ((((status)::text <> 'active'::text) OR (composio_account_ref IS NOT NULL))),
    CONSTRAINT ck_atom_connections_allowed_tools_array CHECK ((jsonb_typeof(allowed_tools) = 'array'::text)),
    CONSTRAINT ck_atom_connections_allowed_tools_slugs CHECK ((NOT jsonb_path_exists(allowed_tools, '$[*]?(@.type() != "string" || @ == "")'::jsonpath))),
    CONSTRAINT ck_atom_connections_connection_identity CHECK (((btrim((toolkit)::text) <> ''::text) AND (btrim((composio_user_id)::text) <> ''::text))),
    CONSTRAINT ck_atom_connections_nonempty_account_ref CHECK (((composio_account_ref IS NULL) OR (btrim((composio_account_ref)::text) <> ''::text))),
    CONSTRAINT ck_atom_connections_permission_ceiling CHECK (((permission_ceiling)::text = ANY ((ARRAY['read'::character varying, 'draft'::character varying, 'write'::character varying, 'destructive'::character varying])::text[]))),
    CONSTRAINT ck_atom_connections_pinned_toolkit_version CHECK (((btrim((toolkit_version)::text) <> ''::text) AND (lower(btrim((toolkit_version)::text)) <> 'latest'::text))),
    CONSTRAINT ck_atom_connections_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'expired'::character varying, 'revoked'::character varying, 'unknown'::character varying])::text[])))
);

ALTER TABLE ONLY public.atom_connections FORCE ROW LEVEL SECURITY;

--
-- Name: atom_grants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.atom_grants (
    atom_id uuid NOT NULL,
    resource_type character varying NOT NULL,
    resource_id uuid,
    level character varying NOT NULL,
    constraints jsonb DEFAULT '{}'::jsonb NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_atom_grants_constraints_object CHECK ((jsonb_typeof(constraints) = 'object'::text)),
    CONSTRAINT ck_atom_grants_level CHECK (((level)::text = ANY ((ARRAY['summary'::character varying, 'read'::character varying, 'write'::character varying])::text[]))),
    CONSTRAINT ck_atom_grants_resource_type CHECK (((resource_type)::text = ANY ((ARRAY['project'::character varying, 'team'::character varying, 'collection'::character varying, 'channel'::character varying, 'folder'::character varying, 'memory'::character varying, 'meeting'::character varying, 'connection'::character varying])::text[])))
);

ALTER TABLE ONLY public.atom_grants FORCE ROW LEVEL SECURITY;

--
-- Name: atom_schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.atom_schedules (
    atom_id uuid NOT NULL,
    cron character varying,
    interval_minutes integer,
    timezone character varying DEFAULT 'UTC'::character varying NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    next_run_at timestamp with time zone,
    last_run_at timestamp with time zone,
    cursor jsonb DEFAULT '{}'::jsonb NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_atom_schedules_cursor_object CHECK ((jsonb_typeof(cursor) = 'object'::text)),
    CONSTRAINT ck_atom_schedules_minimum_interval CHECK (((interval_minutes IS NULL) OR (interval_minutes >= 5))),
    CONSTRAINT ck_atom_schedules_nonempty_cron CHECK (((cron IS NULL) OR (btrim((cron)::text) <> ''::text))),
    CONSTRAINT ck_atom_schedules_one_cadence CHECK ((num_nonnulls(cron, interval_minutes) = 1))
);

ALTER TABLE ONLY public.atom_schedules FORCE ROW LEVEL SECURITY;

--
-- Name: atom_skills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.atom_skills (
    atom_id uuid NOT NULL,
    skill_id uuid,
    catalog_skill_id uuid,
    skill_version integer NOT NULL,
    added_by character varying NOT NULL,
    added_by_member_id uuid NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_atom_skills_added_by CHECK (((added_by)::text = ANY ((ARRAY['human'::character varying, 'atomizer'::character varying, 'self'::character varying])::text[]))),
    CONSTRAINT ck_atom_skills_one_skill CHECK ((num_nonnulls(skill_id, catalog_skill_id) = 1)),
    CONSTRAINT ck_atom_skills_positive_version CHECK ((skill_version > 0))
);

ALTER TABLE ONLY public.atom_skills FORCE ROW LEVEL SECURITY;

--
-- Name: atom_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.atom_versions (
    atom_id uuid NOT NULL,
    version integer NOT NULL,
    instructions character varying NOT NULL,
    policy jsonb DEFAULT '{}'::jsonb NOT NULL,
    tools jsonb DEFAULT '[]'::jsonb NOT NULL,
    source character varying NOT NULL,
    status character varying DEFAULT 'candidate'::character varying NOT NULL,
    eval jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_atom_versions_eval_object CHECK ((jsonb_typeof(eval) = 'object'::text)),
    CONSTRAINT ck_atom_versions_policy_object CHECK ((jsonb_typeof(policy) = 'object'::text)),
    CONSTRAINT ck_atom_versions_positive_version CHECK ((version > 0)),
    CONSTRAINT ck_atom_versions_source CHECK (((source)::text = ANY ((ARRAY['human'::character varying, 'atomizer'::character varying, 'self'::character varying])::text[]))),
    CONSTRAINT ck_atom_versions_status CHECK (((status)::text = ANY ((ARRAY['candidate'::character varying, 'active'::character varying, 'rolled_back'::character varying, 'rejected'::character varying])::text[]))),
    CONSTRAINT ck_atom_versions_tools_array CHECK ((jsonb_typeof(tools) = 'array'::text))
);

ALTER TABLE ONLY public.atom_versions FORCE ROW LEVEL SECURITY;

--
-- Name: atoms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.atoms (
    member_id uuid NOT NULL,
    kind character varying NOT NULL,
    owner_member_id uuid,
    name character varying NOT NULL,
    description character varying,
    status character varying DEFAULT 'draft'::character varying NOT NULL,
    model_tier character varying NOT NULL,
    active_version_id uuid,
    max_runs_per_day integer NOT NULL,
    max_cost_per_day numeric(12,6) NOT NULL,
    max_actions_per_day integer NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT ck_atoms_active_version_required CHECK ((((status)::text <> 'active'::text) OR ((active_version_id IS NOT NULL) AND (deleted_at IS NULL)))),
    CONSTRAINT ck_atoms_kind CHECK (((kind)::text = ANY ((ARRAY['workspace'::character varying, 'personal'::character varying])::text[]))),
    CONSTRAINT ck_atoms_model_tier CHECK (((model_tier)::text = ANY ((ARRAY['small'::character varying, 'medium'::character varying])::text[]))),
    CONSTRAINT ck_atoms_nonnegative_caps CHECK (((max_runs_per_day >= 0) AND (max_cost_per_day >= (0)::numeric) AND (max_cost_per_day < 'Infinity'::numeric) AND (max_actions_per_day >= 0))),
    CONSTRAINT ck_atoms_owner_kind CHECK (((((kind)::text = 'personal'::text) AND (owner_member_id IS NOT NULL)) OR (((kind)::text = 'workspace'::text) AND (owner_member_id IS NULL)))),
    CONSTRAINT ck_atoms_status CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'active'::character varying, 'paused'::character varying, 'killed'::character varying])::text[])))
);

ALTER TABLE ONLY public.atoms FORCE ROW LEVEL SECURITY;

--
-- Name: attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attachments (
    file_id uuid NOT NULL,
    message_id uuid,
    comment_id uuid,
    task_id uuid,
    work_item_id uuid,
    project_id uuid,
    project_update_id uuid,
    doc_id uuid,
    agent_report_id uuid,
    meeting_id uuid,
    sort_order double precision DEFAULT '0'::double precision NOT NULL,
    created_by uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_attachments_single_target CHECK ((num_nonnulls(message_id, comment_id, task_id, work_item_id, project_id, project_update_id, doc_id, agent_report_id, meeting_id) = 1))
);

ALTER TABLE ONLY public.attachments FORCE ROW LEVEL SECURITY;

--
-- Name: backlogs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backlogs (
    project_id uuid NOT NULL,
    name character varying NOT NULL,
    description character varying,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.backlogs FORCE ROW LEVEL SECURITY;

--
-- Name: catalog_skills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.catalog_skills (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    name character varying NOT NULL,
    version integer NOT NULL,
    description character varying NOT NULL,
    when_to_use character varying NOT NULL,
    instructions character varying NOT NULL,
    tools_required jsonb DEFAULT '[]'::jsonb NOT NULL,
    trust_tier character varying DEFAULT 'community'::character varying NOT NULL,
    embedding public.vector(1536),
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, (((COALESCE(description, ''::character varying))::text || ' '::text) || (COALESCE(when_to_use, ''::character varying))::text))) STORED,
    status character varying DEFAULT 'active'::character varying NOT NULL,
    CONSTRAINT ck_catalog_skills_positive_version CHECK ((version > 0)),
    CONSTRAINT ck_catalog_skills_tools_required_array CHECK ((jsonb_typeof(tools_required) = 'array'::text)),
    CONSTRAINT ck_catalog_skills_trust_tier CHECK (((trust_tier)::text = ANY ((ARRAY['official'::character varying, 'verified'::character varying, 'community'::character varying])::text[])))
);

ALTER TABLE ONLY public.catalog_skills FORCE ROW LEVEL SECURITY;

--
-- Name: channel_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.channel_members (
    channel_id uuid NOT NULL,
    member_id uuid NOT NULL,
    role character varying DEFAULT 'member'::character varying NOT NULL,
    last_read_at timestamp with time zone,
    muted boolean DEFAULT false NOT NULL,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL,
    CONSTRAINT ck_channel_members_role CHECK (((role)::text = ANY ((ARRAY['owner'::character varying, 'member'::character varying])::text[])))
);

ALTER TABLE ONLY public.channel_members FORCE ROW LEVEL SECURITY;

--
-- Name: channels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.channels (
    team_id uuid,
    project_id uuid,
    type character varying NOT NULL,
    name character varying,
    topic character varying,
    is_private boolean DEFAULT false NOT NULL,
    created_by uuid,
    archived_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_channels_type CHECK (((type)::text = ANY ((ARRAY['team'::character varying, 'project'::character varying, 'group'::character varying, 'dm'::character varying, 'agent'::character varying])::text[])))
);

ALTER TABLE ONLY public.channels FORCE ROW LEVEL SECURITY;

--
-- Name: chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chunks (
    source_type character varying NOT NULL,
    source_id uuid NOT NULL,
    chunk_index integer DEFAULT 0 NOT NULL,
    text character varying NOT NULL,
    heading_path character varying,
    page integer,
    start_offset integer,
    end_offset integer,
    team_id uuid,
    project_id uuid,
    channel_id uuid,
    content_hash character varying,
    embedding public.vector(1536),
    embedding_model character varying,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, (COALESCE(text, ''::character varying))::text)) STORED,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_chunks_source_type CHECK (((source_type)::text = ANY ((ARRAY['file'::character varying, 'doc'::character varying, 'task'::character varying, 'work_item'::character varying, 'comment'::character varying, 'message'::character varying, 'transcript'::character varying, 'report'::character varying])::text[])))
);

ALTER TABLE ONLY public.chunks FORCE ROW LEVEL SECURITY;

--
-- Name: collections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collections (
    team_id uuid,
    project_id uuid,
    name character varying NOT NULL,
    description character varying,
    icon character varying,
    schema jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_by uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);

ALTER TABLE ONLY public.collections FORCE ROW LEVEL SECURITY;

--
-- Name: comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comments (
    task_id uuid,
    work_item_id uuid,
    project_id uuid,
    project_update_id uuid,
    sprint_id uuid,
    doc_id uuid,
    block_id uuid,
    parent_comment_id uuid,
    author_id uuid,
    body character varying NOT NULL,
    body_json jsonb,
    resolved_at timestamp with time zone,
    resolved_by uuid,
    edited_at timestamp with time zone,
    deleted_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_comments_block_needs_doc CHECK (((block_id IS NULL) OR (doc_id IS NOT NULL))),
    CONSTRAINT ck_comments_single_target CHECK ((num_nonnulls(task_id, work_item_id, project_id, project_update_id, sprint_id, doc_id) = 1))
);

ALTER TABLE ONLY public.comments FORCE ROW LEVEL SECURITY;

--
-- Name: decision_conflicts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.decision_conflicts (
    decision_id uuid NOT NULL,
    task_id uuid,
    work_item_id uuid,
    source_type character varying,
    source_id uuid,
    source_url character varying,
    similarity double precision,
    proposal character varying,
    explanation character varying NOT NULL,
    status character varying DEFAULT 'open'::character varying NOT NULL,
    flagged_by uuid,
    run_id uuid,
    resolved_by uuid,
    resolved_at timestamp with time zone,
    resolution_note character varying,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_decision_conflicts_status CHECK (((status)::text = ANY ((ARRAY['open'::character varying, 'accepted'::character varying, 'dismissed'::character varying, 'resolved'::character varying])::text[])))
);

ALTER TABLE ONLY public.decision_conflicts FORCE ROW LEVEL SECURITY;

--
-- Name: decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.decisions (
    memory_id uuid NOT NULL,
    statement character varying NOT NULL,
    rationale character varying,
    alternatives jsonb DEFAULT '{}'::jsonb NOT NULL,
    area character varying,
    scope_paths jsonb DEFAULT '[]'::jsonb NOT NULL,
    status character varying DEFAULT 'active'::character varying NOT NULL,
    owner_member_id uuid,
    work_item_id uuid,
    task_id uuid,
    decided_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_decisions_status CHECK (((status)::text = ANY ((ARRAY['proposed'::character varying, 'active'::character varying, 'superseded'::character varying, 'rejected'::character varying])::text[])))
);

ALTER TABLE ONLY public.decisions FORCE ROW LEVEL SECURITY;

--
-- Name: doc_blocks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doc_blocks (
    doc_id uuid NOT NULL,
    parent_block_id uuid,
    type character varying NOT NULL,
    content jsonb DEFAULT '{}'::jsonb NOT NULL,
    sort_order double precision DEFAULT '0'::double precision NOT NULL,
    created_by uuid,
    last_edited_by uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_doc_blocks_type CHECK (((type)::text = ANY ((ARRAY['paragraph'::character varying, 'heading_1'::character varying, 'heading_2'::character varying, 'heading_3'::character varying, 'bulleted_list'::character varying, 'numbered_list'::character varying, 'todo'::character varying, 'toggle'::character varying, 'quote'::character varying, 'callout'::character varying, 'code'::character varying, 'divider'::character varying, 'image'::character varying, 'file'::character varying, 'embed'::character varying, 'table'::character varying, 'table_row'::character varying, 'task_embed'::character varying, 'doc_link'::character varying, 'columns'::character varying, 'column'::character varying])::text[])))
);

ALTER TABLE ONLY public.doc_blocks FORCE ROW LEVEL SECURITY;

--
-- Name: doc_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doc_versions (
    doc_id uuid NOT NULL,
    version integer NOT NULL,
    title character varying NOT NULL,
    snapshot jsonb NOT NULL,
    created_by uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.doc_versions FORCE ROW LEVEL SECURITY;

--
-- Name: docs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.docs (
    team_id uuid,
    project_id uuid,
    parent_doc_id uuid,
    collection_id uuid,
    title character varying DEFAULT 'Untitled'::character varying NOT NULL,
    icon character varying,
    cover_file_id uuid,
    properties jsonb DEFAULT '{}'::jsonb NOT NULL,
    sort_order double precision DEFAULT '0'::double precision NOT NULL,
    is_template boolean DEFAULT false NOT NULL,
    is_locked boolean DEFAULT false NOT NULL,
    published_slug character varying,
    created_by uuid,
    last_edited_by uuid,
    archived_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);

ALTER TABLE ONLY public.docs FORCE ROW LEVEL SECURITY;

--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id bigint NOT NULL,
    actor_id uuid,
    entity_type character varying NOT NULL,
    entity_id uuid,
    action character varying NOT NULL,
    changes jsonb DEFAULT '{}'::jsonb NOT NULL,
    run_id uuid,
    request_id character varying,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.events FORCE ROW LEVEL SECURITY;

--
-- Name: events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.events_id_seq OWNED BY public.events.id;

--
-- Name: favorites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.favorites (
    member_id uuid NOT NULL,
    entity_type character varying NOT NULL,
    entity_id uuid NOT NULL,
    sort_order double precision DEFAULT '0'::double precision NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_favorites_entity_type CHECK (((entity_type)::text = ANY ((ARRAY['task'::character varying, 'project'::character varying, 'doc'::character varying, 'channel'::character varying, 'saved_view'::character varying, 'collection'::character varying])::text[])))
);

ALTER TABLE ONLY public.favorites FORCE ROW LEVEL SECURITY;

--
-- Name: file_derivatives; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.file_derivatives (
    file_id uuid NOT NULL,
    kind character varying NOT NULL,
    content character varying,
    container character varying,
    blob_path character varying,
    model character varying,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_file_derivatives_has_content CHECK (((content IS NOT NULL) OR (blob_path IS NOT NULL))),
    CONSTRAINT ck_file_derivatives_kind CHECK (((kind)::text = ANY ((ARRAY['thumbnail'::character varying, 'preview'::character varying, 'ocr_text'::character varying, 'caption'::character varying, 'parsed_text'::character varying])::text[])))
);

ALTER TABLE ONLY public.file_derivatives FORCE ROW LEVEL SECURITY;

--
-- Name: files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.files (
    folder_id uuid,
    team_id uuid,
    project_id uuid,
    uploaded_by uuid,
    kind character varying NOT NULL,
    source character varying DEFAULT 'workspace'::character varying NOT NULL,
    name character varying NOT NULL,
    content_type character varying,
    size_bytes bigint,
    sha256 character varying,
    container character varying NOT NULL,
    blob_path character varying NOT NULL,
    duration_ms integer,
    width integer,
    height integer,
    waveform smallint[],
    status character varying DEFAULT 'uploading'::character varying NOT NULL,
    error character varying,
    index_status character varying DEFAULT 'none'::character varying NOT NULL,
    index_error character varying,
    index_started_at timestamp with time zone,
    indexed_at timestamp with time zone,
    chunk_count integer DEFAULT 0 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT ck_files_blob_in_workspace CHECK ((POSITION((((workspace_id)::text || '/'::text)) IN (blob_path)) = 1)),
    CONSTRAINT ck_files_index_status CHECK (((index_status)::text = ANY ((ARRAY['none'::character varying, 'pending'::character varying, 'indexing'::character varying, 'done'::character varying, 'failed'::character varying, 'skipped'::character varying])::text[]))),
    CONSTRAINT ck_files_kind CHECK (((kind)::text = ANY ((ARRAY['document'::character varying, 'pdf'::character varying, 'image'::character varying, 'voice_note'::character varying, 'audio'::character varying, 'video'::character varying, 'recording'::character varying, 'report'::character varying, 'other'::character varying])::text[]))),
    CONSTRAINT ck_files_source CHECK (((source)::text = ANY ((ARRAY['workspace'::character varying, 'chat'::character varying, 'meeting'::character varying, 'agent'::character varying, 'import'::character varying])::text[]))),
    CONSTRAINT ck_files_status CHECK (((status)::text = ANY ((ARRAY['uploading'::character varying, 'scanning'::character varying, 'processing'::character varying, 'ready'::character varying, 'failed'::character varying, 'quarantined'::character varying])::text[])))
);

ALTER TABLE ONLY public.files FORCE ROW LEVEL SECURITY;

--
-- Name: folders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.folders (
    team_id uuid,
    project_id uuid,
    parent_folder_id uuid,
    name character varying NOT NULL,
    created_by uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);

ALTER TABLE ONLY public.folders FORCE ROW LEVEL SECURITY;

--
-- Name: invitations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invitations (
    email character varying NOT NULL,
    role character varying DEFAULT 'member'::character varying NOT NULL,
    token_hash character varying NOT NULL,
    invited_by uuid,
    status character varying DEFAULT 'pending'::character varying NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_invitations_role CHECK (((role)::text = ANY ((ARRAY['admin'::character varying, 'member'::character varying, 'viewer'::character varying])::text[]))),
    CONSTRAINT ck_invitations_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'accepted'::character varying, 'revoked'::character varying, 'expired'::character varying])::text[])))
);

ALTER TABLE ONLY public.invitations FORCE ROW LEVEL SECURITY;

--
-- Name: labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.labels (
    team_id uuid,
    name character varying NOT NULL,
    color character varying,
    description character varying,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.labels FORCE ROW LEVEL SECURITY;

--
-- Name: links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.links (
    kind character varying NOT NULL,
    source_type character varying NOT NULL,
    source_id uuid NOT NULL,
    target_type character varying NOT NULL,
    target_id uuid NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_links_kind CHECK (((kind)::text = ANY ((ARRAY['mention'::character varying, 'link'::character varying])::text[]))),
    CONSTRAINT ck_links_source_type CHECK (((source_type)::text = ANY ((ARRAY['comment'::character varying, 'message'::character varying, 'doc'::character varying, 'doc_block'::character varying, 'task'::character varying, 'work_item'::character varying, 'project'::character varying, 'agent_report'::character varying])::text[]))),
    CONSTRAINT ck_links_target_type CHECK (((target_type)::text = ANY ((ARRAY['member'::character varying, 'task'::character varying, 'work_item'::character varying, 'project'::character varying, 'sprint'::character varying, 'doc'::character varying, 'file'::character varying, 'meeting'::character varying, 'decision'::character varying, 'channel'::character varying])::text[])))
);

ALTER TABLE ONLY public.links FORCE ROW LEVEL SECURITY;

--
-- Name: llm_usage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_usage (
    run_id uuid,
    member_id uuid,
    task_type character varying NOT NULL,
    model character varying NOT NULL,
    input_tokens integer DEFAULT 0 NOT NULL,
    output_tokens integer DEFAULT 0 NOT NULL,
    cost_usd numeric(12,6),
    latency_ms integer,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY public.llm_usage FORCE ROW LEVEL SECURITY;

--
-- Name: meeting_participants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meeting_participants (
    meeting_id uuid NOT NULL,
    member_id uuid NOT NULL,
    joined_at timestamp with time zone,
    left_at timestamp with time zone,
    consent_status character varying DEFAULT 'pending'::character varying NOT NULL,
    consented_at timestamp with time zone,
    workspace_id uuid NOT NULL,
    CONSTRAINT ck_meeting_participants_consent_status CHECK (((consent_status)::text = ANY ((ARRAY['pending'::character varying, 'granted'::character varying, 'declined'::character varying])::text[])))
);

ALTER TABLE ONLY public.meeting_participants FORCE ROW LEVEL SECURITY;

--
-- Name: meetings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meetings (
    team_id uuid,
    project_id uuid,
    channel_id uuid,
    title character varying NOT NULL,
    host_id uuid,
    status character varying DEFAULT 'scheduled'::character varying NOT NULL,
    scheduled_at timestamp with time zone,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    room_ref character varying,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_meetings_status CHECK (((status)::text = ANY ((ARRAY['scheduled'::character varying, 'live'::character varying, 'ended'::character varying, 'canceled'::character varying])::text[])))
);

ALTER TABLE ONLY public.meetings FORCE ROW LEVEL SECURITY;

--
-- Name: members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.members (
    user_id uuid,
    type character varying NOT NULL,
    name character varying,
    role character varying DEFAULT 'member'::character varying NOT NULL,
    status character varying DEFAULT 'active'::character varying NOT NULL,
    agent_kind character varying,
    avatar_url character varying,
    settings jsonb DEFAULT '{}'::jsonb NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT ck_members_agent_kind CHECK (((agent_kind IS NULL) OR ((agent_kind)::text = ANY ((ARRAY['atom'::character varying, 'team_agent'::character varying, 'ghost_engineer'::character varying, 'decision_ledger'::character varying, 'memory_steward'::character varying, 'scrum_master'::character varying, 'security'::character varying, 'architecture'::character varying, 'behavioral'::character varying])::text[])))),
    CONSTRAINT ck_members_human_or_agent CHECK (((((type)::text = 'HUMAN'::text) AND (user_id IS NOT NULL) AND (agent_kind IS NULL) AND (name IS NULL) AND (avatar_url IS NULL)) OR (((type)::text = 'AI'::text) AND (user_id IS NULL) AND (agent_kind IS NOT NULL) AND (name IS NOT NULL)))),
    CONSTRAINT ck_members_role CHECK (((role)::text = ANY ((ARRAY['owner'::character varying, 'admin'::character varying, 'member'::character varying, 'viewer'::character varying])::text[]))),
    CONSTRAINT ck_members_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'suspended'::character varying])::text[]))),
    CONSTRAINT ck_members_type CHECK (((type)::text = ANY ((ARRAY['HUMAN'::character varying, 'AI'::character varying])::text[])))
);

ALTER TABLE ONLY public.members FORCE ROW LEVEL SECURITY;

--
-- Name: memories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memories (
    kind character varying NOT NULL,
    scope character varying DEFAULT 'workspace'::character varying NOT NULL,
    team_id uuid,
    project_id uuid,
    owner_member_id uuid,
    text character varying NOT NULL,
    raw_text character varying,
    importance smallint DEFAULT '3'::smallint NOT NULL,
    strength double precision DEFAULT '1'::double precision NOT NULL,
    confidence double precision DEFAULT '0.8'::double precision NOT NULL,
    pinned boolean DEFAULT false NOT NULL,
    status character varying DEFAULT 'active'::character varying NOT NULL,
    superseded_by uuid,
    superseded_at timestamp with time zone,
    recall_count integer DEFAULT 0 NOT NULL,
    last_recalled_at timestamp with time zone,
    source_type character varying,
    source_id uuid,
    source_member_id uuid,
    agent_name character varying,
    refreshed_at timestamp with time zone,
    embedding public.vector(1536),
    embedding_model character varying,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, (COALESCE(text, ''::character varying))::text)) STORED,
    created_by uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_memories_confidence_range CHECK (((confidence >= (0)::double precision) AND (confidence <= (1)::double precision))),
    CONSTRAINT ck_memories_importance_range CHECK (((importance >= 1) AND (importance <= 5))),
    CONSTRAINT ck_memories_kind CHECK (((kind)::text = ANY ((ARRAY['episode'::character varying, 'fact'::character varying, 'decision'::character varying, 'procedure'::character varying, 'ownership'::character varying, 'constraint'::character varying, 'insight'::character varying])::text[]))),
    CONSTRAINT ck_memories_scope CHECK (((scope)::text = ANY ((ARRAY['workspace'::character varying, 'team'::character varying, 'project'::character varying, 'agent'::character varying])::text[]))),
    CONSTRAINT ck_memories_scope_target CHECK ((((scope)::text = 'workspace'::text) OR (((scope)::text = 'team'::text) AND (team_id IS NOT NULL)) OR (((scope)::text = 'project'::text) AND (project_id IS NOT NULL)) OR (((scope)::text = 'agent'::text) AND (owner_member_id IS NOT NULL)))),
    CONSTRAINT ck_memories_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'faded'::character varying, 'superseded'::character varying])::text[])))
);

ALTER TABLE ONLY public.memories FORCE ROW LEVEL SECURITY;

--
-- Name: memory_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_links (
    from_memory_id uuid NOT NULL,
    to_memory_id uuid NOT NULL,
    type character varying NOT NULL,
    weight double precision DEFAULT '1'::double precision NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_memory_links_distinct_memories CHECK ((from_memory_id <> to_memory_id)),
    CONSTRAINT ck_memory_links_type CHECK (((type)::text = ANY ((ARRAY['derived_from'::character varying, 'supports'::character varying, 'contradicts'::character varying, 'about'::character varying, 'supersedes'::character varying, 'related'::character varying])::text[])))
);

ALTER TABLE ONLY public.memory_links FORCE ROW LEVEL SECURITY;

--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    channel_id uuid NOT NULL,
    author_id uuid,
    parent_message_id uuid,
    kind character varying DEFAULT 'text'::character varying NOT NULL,
    body character varying,
    body_json jsonb,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    pinned_at timestamp with time zone,
    edited_at timestamp with time zone,
    deleted_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_messages_kind CHECK (((kind)::text = ANY ((ARRAY['text'::character varying, 'voice_note'::character varying, 'image'::character varying, 'file'::character varying, 'system'::character varying, 'agent_report'::character varying])::text[])))
);

ALTER TABLE ONLY public.messages FORCE ROW LEVEL SECURITY;

--
-- Name: milestones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.milestones (
    project_id uuid NOT NULL,
    name character varying NOT NULL,
    description character varying,
    target_date date,
    status character varying DEFAULT 'planned'::character varying NOT NULL,
    sort_order double precision DEFAULT '0'::double precision NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_milestones_status CHECK (((status)::text = ANY ((ARRAY['planned'::character varying, 'in_progress'::character varying, 'done'::character varying])::text[])))
);

ALTER TABLE ONLY public.milestones FORCE ROW LEVEL SECURITY;

--
-- Name: notifications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notifications (
    recipient_id uuid NOT NULL,
    actor_id uuid,
    type character varying NOT NULL,
    entity_type character varying,
    entity_id uuid,
    title character varying NOT NULL,
    body character varying,
    read_at timestamp with time zone,
    archived_at timestamp with time zone,
    snoozed_until timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_notifications_type CHECK (((type)::text = ANY ((ARRAY['assigned'::character varying, 'mentioned'::character varying, 'commented'::character varying, 'replied'::character varying, 'status_changed'::character varying, 'due_soon'::character varying, 'approval_requested'::character varying, 'approval_decided'::character varying, 'conflict_flagged'::character varying, 'agent_report'::character varying, 'invited'::character varying, 'meeting_started'::character varying, 'meeting_summary'::character varying])::text[])))
);

ALTER TABLE ONLY public.notifications FORCE ROW LEVEL SECURITY;

--
-- Name: project_updates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_updates (
    project_id uuid NOT NULL,
    author_id uuid,
    health character varying NOT NULL,
    body character varying NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_project_updates_health CHECK (((health)::text = ANY ((ARRAY['on_track'::character varying, 'at_risk'::character varying, 'off_track'::character varying])::text[])))
);

ALTER TABLE ONLY public.project_updates FORCE ROW LEVEL SECURITY;

--
-- Name: projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.projects (
    team_id uuid,
    key character varying NOT NULL,
    name character varying NOT NULL,
    description character varying,
    status character varying DEFAULT 'planned'::character varying NOT NULL,
    lead_id uuid,
    start_date date,
    target_date date,
    icon character varying,
    color character varying,
    task_counter integer DEFAULT 0 NOT NULL,
    created_by uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT ck_projects_status CHECK (((status)::text = ANY ((ARRAY['backlog'::character varying, 'planned'::character varying, 'in_progress'::character varying, 'paused'::character varying, 'completed'::character varying, 'canceled'::character varying])::text[])))
);

ALTER TABLE ONLY public.projects FORCE ROW LEVEL SECURITY;

--
-- Name: reactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reactions (
    member_id uuid NOT NULL,
    emoji character varying NOT NULL,
    comment_id uuid,
    message_id uuid,
    task_id uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_reactions_single_target CHECK ((num_nonnulls(comment_id, message_id, task_id) = 1))
);

ALTER TABLE ONLY public.reactions FORCE ROW LEVEL SECURITY;

--
-- Name: recordings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recordings (
    meeting_id uuid NOT NULL,
    file_id uuid,
    consent_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    duration_ms integer,
    status character varying DEFAULT 'recording'::character varying NOT NULL,
    retention_until timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_recordings_status CHECK (((status)::text = ANY ((ARRAY['recording'::character varying, 'processing'::character varying, 'ready'::character varying, 'failed'::character varying, 'deleted'::character varying])::text[])))
);

ALTER TABLE ONLY public.recordings FORCE ROW LEVEL SECURITY;

--
-- Name: saved_views; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.saved_views (
    owner_id uuid,
    team_id uuid,
    project_id uuid,
    name character varying NOT NULL,
    entity_type character varying NOT NULL,
    layout character varying DEFAULT 'list'::character varying NOT NULL,
    filter jsonb DEFAULT '{}'::jsonb NOT NULL,
    sort jsonb DEFAULT '[]'::jsonb NOT NULL,
    is_shared boolean DEFAULT false NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_saved_views_entity_type CHECK (((entity_type)::text = ANY ((ARRAY['task'::character varying, 'work_item'::character varying, 'project'::character varying, 'doc'::character varying, 'collection'::character varying])::text[]))),
    CONSTRAINT ck_saved_views_layout CHECK (((layout)::text = ANY ((ARRAY['list'::character varying, 'board'::character varying, 'table'::character varying, 'calendar'::character varying, 'timeline'::character varying, 'gallery'::character varying])::text[])))
);

ALTER TABLE ONLY public.saved_views FORCE ROW LEVEL SECURITY;

--
-- Name: skills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.skills (
    scope character varying NOT NULL,
    owner_member_id uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    name character varying NOT NULL,
    version integer NOT NULL,
    description character varying NOT NULL,
    when_to_use character varying NOT NULL,
    instructions character varying NOT NULL,
    tools_required jsonb DEFAULT '[]'::jsonb NOT NULL,
    trust_tier character varying DEFAULT 'community'::character varying NOT NULL,
    embedding public.vector(1536),
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, (((COALESCE(description, ''::character varying))::text || ' '::text) || (COALESCE(when_to_use, ''::character varying))::text))) STORED,
    status character varying DEFAULT 'active'::character varying NOT NULL,
    CONSTRAINT ck_skills_owner_scope CHECK (((((scope)::text = 'personal'::text) AND (owner_member_id IS NOT NULL)) OR (((scope)::text = 'workspace'::text) AND (owner_member_id IS NULL)))),
    CONSTRAINT ck_skills_positive_version CHECK ((version > 0)),
    CONSTRAINT ck_skills_scope CHECK (((scope)::text = ANY ((ARRAY['workspace'::character varying, 'personal'::character varying])::text[]))),
    CONSTRAINT ck_skills_tools_required_array CHECK ((jsonb_typeof(tools_required) = 'array'::text)),
    CONSTRAINT ck_skills_trust_tier CHECK (((trust_tier)::text = ANY ((ARRAY['official'::character varying, 'verified'::character varying, 'community'::character varying])::text[])))
);

ALTER TABLE ONLY public.skills FORCE ROW LEVEL SECURITY;

--
-- Name: sprints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sprints (
    project_id uuid NOT NULL,
    name character varying NOT NULL,
    goal character varying,
    status character varying DEFAULT 'planned'::character varying NOT NULL,
    start_date date,
    end_date date,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_sprints_dates CHECK (((end_date IS NULL) OR (start_date IS NULL) OR (end_date >= start_date))),
    CONSTRAINT ck_sprints_status CHECK (((status)::text = ANY ((ARRAY['planned'::character varying, 'active'::character varying, 'completed'::character varying])::text[])))
);

ALTER TABLE ONLY public.sprints FORCE ROW LEVEL SECURITY;

--
-- Name: subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subscriptions (
    member_id uuid NOT NULL,
    entity_type character varying NOT NULL,
    entity_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_subscriptions_entity_type CHECK (((entity_type)::text = ANY ((ARRAY['task'::character varying, 'work_item'::character varying, 'project'::character varying, 'doc'::character varying, 'channel'::character varying, 'sprint'::character varying])::text[])))
);

ALTER TABLE ONLY public.subscriptions FORCE ROW LEVEL SECURITY;

--
-- Name: task_labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_labels (
    task_id uuid NOT NULL,
    label_id uuid NOT NULL,
    workspace_id uuid NOT NULL
);

ALTER TABLE ONLY public.task_labels FORCE ROW LEVEL SECURITY;

--
-- Name: task_relations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_relations (
    task_id uuid NOT NULL,
    related_task_id uuid NOT NULL,
    type character varying NOT NULL,
    created_by uuid,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_task_relations_distinct_tasks CHECK ((task_id <> related_task_id)),
    CONSTRAINT ck_task_relations_type CHECK (((type)::text = ANY ((ARRAY['blocks'::character varying, 'relates'::character varying, 'duplicates'::character varying])::text[])))
);

ALTER TABLE ONLY public.task_relations FORCE ROW LEVEL SECURITY;

--
-- Name: tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tasks (
    project_id uuid NOT NULL,
    work_item_id uuid NOT NULL,
    sprint_id uuid,
    milestone_id uuid,
    parent_task_id uuid,
    number integer,
    title character varying NOT NULL,
    description character varying,
    requirements character varying,
    acceptance_criteria character varying,
    priority character varying DEFAULT 'none'::character varying NOT NULL,
    status character varying DEFAULT 'backlog'::character varying NOT NULL,
    estimate smallint,
    due_date date,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    sort_order double precision DEFAULT '0'::double precision NOT NULL,
    assignee_id uuid,
    created_by uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT ck_tasks_priority CHECK (((priority)::text = ANY ((ARRAY['none'::character varying, 'low'::character varying, 'medium'::character varying, 'high'::character varying, 'urgent'::character varying])::text[]))),
    CONSTRAINT ck_tasks_status CHECK (((status)::text = ANY ((ARRAY['backlog'::character varying, 'todo'::character varying, 'in_progress'::character varying, 'in_review'::character varying, 'done'::character varying, 'canceled'::character varying])::text[])))
);

ALTER TABLE ONLY public.tasks FORCE ROW LEVEL SECURITY;

--
-- Name: team_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.team_members (
    team_id uuid NOT NULL,
    member_id uuid NOT NULL,
    role character varying DEFAULT 'member'::character varying NOT NULL,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    workspace_id uuid NOT NULL,
    CONSTRAINT ck_team_members_role CHECK (((role)::text = ANY ((ARRAY['lead'::character varying, 'member'::character varying])::text[])))
);

ALTER TABLE ONLY public.team_members FORCE ROW LEVEL SECURITY;

--
-- Name: teams; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.teams (
    key character varying NOT NULL,
    name character varying NOT NULL,
    description character varying,
    icon character varying,
    color character varying,
    visibility character varying DEFAULT 'workspace'::character varying NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT ck_teams_visibility CHECK (((visibility)::text = ANY ((ARRAY['workspace'::character varying, 'private'::character varying])::text[])))
);

ALTER TABLE ONLY public.teams FORCE ROW LEVEL SECURITY;

--
-- Name: transcript_segments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transcript_segments (
    transcript_id uuid NOT NULL,
    sequence integer NOT NULL,
    speaker_member_id uuid,
    speaker_label character varying,
    start_ms integer NOT NULL,
    end_ms integer NOT NULL,
    text character varying NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    CONSTRAINT ck_transcript_segments_span CHECK ((end_ms >= start_ms))
);

ALTER TABLE ONLY public.transcript_segments FORCE ROW LEVEL SECURITY;

--
-- Name: transcripts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transcripts (
    file_id uuid,
    meeting_id uuid,
    message_id uuid,
    language character varying,
    status character varying DEFAULT 'pending'::character varying NOT NULL,
    full_text character varying,
    model character varying,
    duration_ms integer,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_transcripts_has_source CHECK ((num_nonnulls(file_id, meeting_id, message_id) >= 1)),
    CONSTRAINT ck_transcripts_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'ready'::character varying, 'failed'::character varying])::text[])))
);

ALTER TABLE ONLY public.transcripts FORCE ROW LEVEL SECURITY;

--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    email character varying NOT NULL,
    name character varying NOT NULL,
    avatar_url character varying,
    settings jsonb DEFAULT '{}'::jsonb NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);

ALTER TABLE ONLY public.users FORCE ROW LEVEL SECURITY;

--
-- Name: work_item_labels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.work_item_labels (
    work_item_id uuid NOT NULL,
    label_id uuid NOT NULL,
    workspace_id uuid NOT NULL
);

ALTER TABLE ONLY public.work_item_labels FORCE ROW LEVEL SECURITY;

--
-- Name: work_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.work_items (
    project_id uuid NOT NULL,
    backlog_id uuid NOT NULL,
    title character varying NOT NULL,
    description character varying,
    type character varying NOT NULL,
    priority character varying DEFAULT 'none'::character varying NOT NULL,
    status character varying DEFAULT 'backlog'::character varying NOT NULL,
    created_by uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT ck_work_items_priority CHECK (((priority)::text = ANY ((ARRAY['none'::character varying, 'low'::character varying, 'medium'::character varying, 'high'::character varying, 'urgent'::character varying])::text[]))),
    CONSTRAINT ck_work_items_status CHECK (((status)::text = ANY ((ARRAY['backlog'::character varying, 'todo'::character varying, 'in_progress'::character varying, 'in_review'::character varying, 'done'::character varying, 'canceled'::character varying])::text[]))),
    CONSTRAINT ck_work_items_type CHECK (((type)::text = ANY ((ARRAY['FEATURE'::character varying, 'STORY'::character varying, 'BUG'::character varying])::text[])))
);

ALTER TABLE ONLY public.work_items FORCE ROW LEVEL SECURITY;

--
-- Name: workspaces; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workspaces (
    name character varying NOT NULL,
    slug character varying NOT NULL,
    description character varying,
    settings jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);

ALTER TABLE ONLY public.workspaces FORCE ROW LEVEL SECURITY;

--
-- Name: events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events ALTER COLUMN id SET DEFAULT nextval('public.events_id_seq'::regclass);

--
-- Name: agent_reports pk_agent_reports; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT pk_agent_reports PRIMARY KEY (id);

--
-- Name: agent_runs pk_agent_runs; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT pk_agent_runs PRIMARY KEY (id);

--
-- Name: approvals pk_approvals; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT pk_approvals PRIMARY KEY (id);

--
-- Name: atom_connections pk_atom_connections; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_connections
    ADD CONSTRAINT pk_atom_connections PRIMARY KEY (id);

--
-- Name: atom_grants pk_atom_grants; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_grants
    ADD CONSTRAINT pk_atom_grants PRIMARY KEY (id);

--
-- Name: atom_schedules pk_atom_schedules; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_schedules
    ADD CONSTRAINT pk_atom_schedules PRIMARY KEY (id);

--
-- Name: atom_skills pk_atom_skills; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_skills
    ADD CONSTRAINT pk_atom_skills PRIMARY KEY (id);

--
-- Name: atom_versions pk_atom_versions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_versions
    ADD CONSTRAINT pk_atom_versions PRIMARY KEY (id);

--
-- Name: atoms pk_atoms; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atoms
    ADD CONSTRAINT pk_atoms PRIMARY KEY (id);

--
-- Name: attachments pk_attachments; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT pk_attachments PRIMARY KEY (id);

--
-- Name: backlogs pk_backlogs; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backlogs
    ADD CONSTRAINT pk_backlogs PRIMARY KEY (id);

--
-- Name: catalog_skills pk_catalog_skills; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_skills
    ADD CONSTRAINT pk_catalog_skills PRIMARY KEY (id);

--
-- Name: channel_members pk_channel_members; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channel_members
    ADD CONSTRAINT pk_channel_members PRIMARY KEY (channel_id, member_id);

--
-- Name: channels pk_channels; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT pk_channels PRIMARY KEY (id);

--
-- Name: chunks pk_chunks; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT pk_chunks PRIMARY KEY (id);

--
-- Name: collections pk_collections; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collections
    ADD CONSTRAINT pk_collections PRIMARY KEY (id);

--
-- Name: comments pk_comments; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT pk_comments PRIMARY KEY (id);

--
-- Name: decision_conflicts pk_decision_conflicts; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_conflicts
    ADD CONSTRAINT pk_decision_conflicts PRIMARY KEY (id);

--
-- Name: decisions pk_decisions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT pk_decisions PRIMARY KEY (memory_id);

--
-- Name: doc_blocks pk_doc_blocks; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_blocks
    ADD CONSTRAINT pk_doc_blocks PRIMARY KEY (id);

--
-- Name: doc_versions pk_doc_versions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_versions
    ADD CONSTRAINT pk_doc_versions PRIMARY KEY (id);

--
-- Name: docs pk_docs; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.docs
    ADD CONSTRAINT pk_docs PRIMARY KEY (id);

--
-- Name: events pk_events; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT pk_events PRIMARY KEY (id);

--
-- Name: favorites pk_favorites; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.favorites
    ADD CONSTRAINT pk_favorites PRIMARY KEY (id);

--
-- Name: file_derivatives pk_file_derivatives; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_derivatives
    ADD CONSTRAINT pk_file_derivatives PRIMARY KEY (id);

--
-- Name: files pk_files; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT pk_files PRIMARY KEY (id);

--
-- Name: folders pk_folders; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT pk_folders PRIMARY KEY (id);

--
-- Name: invitations pk_invitations; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT pk_invitations PRIMARY KEY (id);

--
-- Name: labels pk_labels; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.labels
    ADD CONSTRAINT pk_labels PRIMARY KEY (id);

--
-- Name: links pk_links; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.links
    ADD CONSTRAINT pk_links PRIMARY KEY (id);

--
-- Name: llm_usage pk_llm_usage; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_usage
    ADD CONSTRAINT pk_llm_usage PRIMARY KEY (id);

--
-- Name: meeting_participants pk_meeting_participants; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_participants
    ADD CONSTRAINT pk_meeting_participants PRIMARY KEY (meeting_id, member_id);

--
-- Name: meetings pk_meetings; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT pk_meetings PRIMARY KEY (id);

--
-- Name: members pk_members; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.members
    ADD CONSTRAINT pk_members PRIMARY KEY (id);

--
-- Name: memories pk_memories; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memories
    ADD CONSTRAINT pk_memories PRIMARY KEY (id);

--
-- Name: memory_links pk_memory_links; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_links
    ADD CONSTRAINT pk_memory_links PRIMARY KEY (from_memory_id, to_memory_id, type);

--
-- Name: messages pk_messages; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT pk_messages PRIMARY KEY (id);

--
-- Name: milestones pk_milestones; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.milestones
    ADD CONSTRAINT pk_milestones PRIMARY KEY (id);

--
-- Name: notifications pk_notifications; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT pk_notifications PRIMARY KEY (id);

--
-- Name: project_updates pk_project_updates; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_updates
    ADD CONSTRAINT pk_project_updates PRIMARY KEY (id);

--
-- Name: projects pk_projects; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT pk_projects PRIMARY KEY (id);

--
-- Name: reactions pk_reactions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reactions
    ADD CONSTRAINT pk_reactions PRIMARY KEY (id);

--
-- Name: recordings pk_recordings; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT pk_recordings PRIMARY KEY (id);

--
-- Name: saved_views pk_saved_views; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_views
    ADD CONSTRAINT pk_saved_views PRIMARY KEY (id);

--
-- Name: skills pk_skills; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT pk_skills PRIMARY KEY (id);

--
-- Name: sprints pk_sprints; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sprints
    ADD CONSTRAINT pk_sprints PRIMARY KEY (id);

--
-- Name: subscriptions pk_subscriptions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT pk_subscriptions PRIMARY KEY (member_id, entity_type, entity_id);

--
-- Name: task_labels pk_task_labels; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_labels
    ADD CONSTRAINT pk_task_labels PRIMARY KEY (task_id, label_id);

--
-- Name: task_relations pk_task_relations; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_relations
    ADD CONSTRAINT pk_task_relations PRIMARY KEY (task_id, related_task_id, type);

--
-- Name: tasks pk_tasks; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT pk_tasks PRIMARY KEY (id);

--
-- Name: team_members pk_team_members; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT pk_team_members PRIMARY KEY (team_id, member_id);

--
-- Name: teams pk_teams; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT pk_teams PRIMARY KEY (id);

--
-- Name: transcript_segments pk_transcript_segments; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcript_segments
    ADD CONSTRAINT pk_transcript_segments PRIMARY KEY (id);

--
-- Name: transcripts pk_transcripts; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT pk_transcripts PRIMARY KEY (id);

--
-- Name: users pk_users; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT pk_users PRIMARY KEY (id);

--
-- Name: work_item_labels pk_work_item_labels; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_item_labels
    ADD CONSTRAINT pk_work_item_labels PRIMARY KEY (work_item_id, label_id);

--
-- Name: work_items pk_work_items; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_items
    ADD CONSTRAINT pk_work_items PRIMARY KEY (id);

--
-- Name: workspaces pk_workspaces; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT pk_workspaces PRIMARY KEY (id);

--
-- Name: agent_reports uq_agent_reports_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT uq_agent_reports_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: agent_runs uq_agent_runs_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT uq_agent_runs_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: agent_runs uq_agent_runs_workspace_id_idempotency_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT uq_agent_runs_workspace_id_idempotency_key UNIQUE (workspace_id, idempotency_key);

--
-- Name: approvals uq_approvals_workspace_id_execution_run_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT uq_approvals_workspace_id_execution_run_id UNIQUE (workspace_id, execution_run_id);

--
-- Name: approvals uq_approvals_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT uq_approvals_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: approvals uq_approvals_workspace_id_run_id_request_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT uq_approvals_workspace_id_run_id_request_key UNIQUE (workspace_id, run_id, request_key);

--
-- Name: atom_connections uq_atom_connections_atom_id_toolkit; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_connections
    ADD CONSTRAINT uq_atom_connections_atom_id_toolkit UNIQUE (atom_id, toolkit);

--
-- Name: atom_connections uq_atom_connections_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_connections
    ADD CONSTRAINT uq_atom_connections_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: atom_grants uq_atom_grants_atom_id_resource_type_resource_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_grants
    ADD CONSTRAINT uq_atom_grants_atom_id_resource_type_resource_id UNIQUE NULLS NOT DISTINCT (atom_id, resource_type, resource_id);

--
-- Name: atom_grants uq_atom_grants_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_grants
    ADD CONSTRAINT uq_atom_grants_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: atom_schedules uq_atom_schedules_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_schedules
    ADD CONSTRAINT uq_atom_schedules_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: atom_skills uq_atom_skills_atom_id_catalog_skill_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_skills
    ADD CONSTRAINT uq_atom_skills_atom_id_catalog_skill_id UNIQUE (atom_id, catalog_skill_id);

--
-- Name: atom_skills uq_atom_skills_atom_id_skill_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_skills
    ADD CONSTRAINT uq_atom_skills_atom_id_skill_id UNIQUE (atom_id, skill_id);

--
-- Name: atom_skills uq_atom_skills_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_skills
    ADD CONSTRAINT uq_atom_skills_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: atom_versions uq_atom_versions_atom_id_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_versions
    ADD CONSTRAINT uq_atom_versions_atom_id_version UNIQUE (atom_id, version);

--
-- Name: atom_versions uq_atom_versions_workspace_id_atom_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_versions
    ADD CONSTRAINT uq_atom_versions_workspace_id_atom_id_id UNIQUE (workspace_id, atom_id, id);

--
-- Name: atom_versions uq_atom_versions_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_versions
    ADD CONSTRAINT uq_atom_versions_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: atoms uq_atoms_member_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atoms
    ADD CONSTRAINT uq_atoms_member_id UNIQUE (member_id);

--
-- Name: atoms uq_atoms_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atoms
    ADD CONSTRAINT uq_atoms_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: backlogs uq_backlogs_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backlogs
    ADD CONSTRAINT uq_backlogs_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: backlogs uq_backlogs_workspace_id_project_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backlogs
    ADD CONSTRAINT uq_backlogs_workspace_id_project_id_id UNIQUE (workspace_id, project_id, id);

--
-- Name: catalog_skills uq_catalog_skills_id_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_skills
    ADD CONSTRAINT uq_catalog_skills_id_version UNIQUE (id, version);

--
-- Name: catalog_skills uq_catalog_skills_name_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.catalog_skills
    ADD CONSTRAINT uq_catalog_skills_name_version UNIQUE (name, version);

--
-- Name: channels uq_channels_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT uq_channels_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: chunks uq_chunks_source_type_source_id_chunk_index; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT uq_chunks_source_type_source_id_chunk_index UNIQUE (source_type, source_id, chunk_index);

--
-- Name: collections uq_collections_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collections
    ADD CONSTRAINT uq_collections_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: comments uq_comments_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT uq_comments_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: decisions uq_decisions_workspace_id_memory_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT uq_decisions_workspace_id_memory_id UNIQUE (workspace_id, memory_id);

--
-- Name: doc_blocks uq_doc_blocks_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_blocks
    ADD CONSTRAINT uq_doc_blocks_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: doc_versions uq_doc_versions_doc_id_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_versions
    ADD CONSTRAINT uq_doc_versions_doc_id_version UNIQUE (doc_id, version);

--
-- Name: docs uq_docs_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.docs
    ADD CONSTRAINT uq_docs_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: favorites uq_favorites_member_id_entity_type_entity_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.favorites
    ADD CONSTRAINT uq_favorites_member_id_entity_type_entity_id UNIQUE (member_id, entity_type, entity_id);

--
-- Name: files uq_files_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT uq_files_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: folders uq_folders_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT uq_folders_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: invitations uq_invitations_token_hash; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT uq_invitations_token_hash UNIQUE (token_hash);

--
-- Name: labels uq_labels_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.labels
    ADD CONSTRAINT uq_labels_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: links uq_links_source_type_source_id_target_type_target_id_kind; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.links
    ADD CONSTRAINT uq_links_source_type_source_id_target_type_target_id_kind UNIQUE (source_type, source_id, target_type, target_id, kind);

--
-- Name: meetings uq_meetings_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT uq_meetings_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: members uq_members_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.members
    ADD CONSTRAINT uq_members_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: members uq_members_workspace_id_user_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.members
    ADD CONSTRAINT uq_members_workspace_id_user_id UNIQUE (workspace_id, user_id);

--
-- Name: memories uq_memories_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memories
    ADD CONSTRAINT uq_memories_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: messages uq_messages_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT uq_messages_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: milestones uq_milestones_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.milestones
    ADD CONSTRAINT uq_milestones_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: milestones uq_milestones_workspace_id_project_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.milestones
    ADD CONSTRAINT uq_milestones_workspace_id_project_id_id UNIQUE (workspace_id, project_id, id);

--
-- Name: project_updates uq_project_updates_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_updates
    ADD CONSTRAINT uq_project_updates_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: projects uq_projects_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT uq_projects_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: reactions uq_reactions_member_id_emoji_comment_id_message_id_task_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reactions
    ADD CONSTRAINT uq_reactions_member_id_emoji_comment_id_message_id_task_id UNIQUE NULLS NOT DISTINCT (member_id, emoji, comment_id, message_id, task_id);

--
-- Name: recordings uq_recordings_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT uq_recordings_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: saved_views uq_saved_views_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_views
    ADD CONSTRAINT uq_saved_views_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: skills uq_skills_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT uq_skills_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: skills uq_skills_workspace_id_id_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT uq_skills_workspace_id_id_version UNIQUE (workspace_id, id, version);

--
-- Name: skills uq_skills_workspace_id_name_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT uq_skills_workspace_id_name_version UNIQUE (workspace_id, name, version);

--
-- Name: sprints uq_sprints_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sprints
    ADD CONSTRAINT uq_sprints_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: sprints uq_sprints_workspace_id_project_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sprints
    ADD CONSTRAINT uq_sprints_workspace_id_project_id_id UNIQUE (workspace_id, project_id, id);

--
-- Name: tasks uq_tasks_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT uq_tasks_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: tasks uq_tasks_workspace_id_project_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT uq_tasks_workspace_id_project_id_id UNIQUE (workspace_id, project_id, id);

--
-- Name: teams uq_teams_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT uq_teams_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: transcripts uq_transcripts_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT uq_transcripts_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: work_items uq_work_items_workspace_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_items
    ADD CONSTRAINT uq_work_items_workspace_id_id UNIQUE (workspace_id, id);

--
-- Name: work_items uq_work_items_workspace_id_project_id_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_items
    ADD CONSTRAINT uq_work_items_workspace_id_project_id_id UNIQUE (workspace_id, project_id, id);

--
-- Name: ix_agent_reports_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_reports_project ON public.agent_reports USING btree (project_id, created_at) WHERE (project_id IS NOT NULL);

--
-- Name: ix_agent_reports_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_reports_task ON public.agent_reports USING btree (task_id) WHERE (task_id IS NOT NULL);

--
-- Name: ix_agent_runs_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_runs_agent ON public.agent_runs USING btree (agent_id, created_at);

--
-- Name: ix_agent_runs_atom_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_runs_atom_created ON public.agent_runs USING btree (workspace_id, atom_id, created_at);

--
-- Name: ix_agent_runs_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agent_runs_task ON public.agent_runs USING btree (task_id) WHERE (task_id IS NOT NULL);

--
-- Name: ix_approvals_atom_ready; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_approvals_atom_ready ON public.approvals USING btree (workspace_id, created_at) WHERE ((atom_id IS NOT NULL) AND ((status)::text = 'approved'::text) AND (execution_run_id IS NULL) AND (claimed_at IS NULL));

--
-- Name: ix_approvals_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_approvals_pending ON public.approvals USING btree (assigned_to) WHERE ((status)::text = 'pending'::text);

--
-- Name: ix_atom_grants_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_atom_grants_lookup ON public.atom_grants USING btree (workspace_id, atom_id, resource_type);

--
-- Name: ix_atom_schedules_atom_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_atom_schedules_atom_id ON public.atom_schedules USING btree (atom_id);

--
-- Name: ix_atom_schedules_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_atom_schedules_due ON public.atom_schedules USING btree (enabled, next_run_at);

--
-- Name: ix_attachments_comment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attachments_comment ON public.attachments USING btree (comment_id) WHERE (comment_id IS NOT NULL);

--
-- Name: ix_attachments_doc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attachments_doc ON public.attachments USING btree (doc_id) WHERE (doc_id IS NOT NULL);

--
-- Name: ix_attachments_file_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attachments_file_id ON public.attachments USING btree (file_id);

--
-- Name: ix_attachments_message; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attachments_message ON public.attachments USING btree (message_id) WHERE (message_id IS NOT NULL);

--
-- Name: ix_attachments_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_attachments_task ON public.attachments USING btree (task_id) WHERE (task_id IS NOT NULL);

--
-- Name: ix_catalog_skills_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_catalog_skills_embedding ON public.catalog_skills USING hnsw (embedding public.vector_cosine_ops);

--
-- Name: ix_catalog_skills_tsv; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_catalog_skills_tsv ON public.catalog_skills USING gin (tsv);

--
-- Name: ix_channel_members_member; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_channel_members_member ON public.channel_members USING btree (workspace_id, member_id);

--
-- Name: ix_chunks_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chunks_embedding ON public.chunks USING hnsw (embedding public.vector_cosine_ops);

--
-- Name: ix_chunks_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chunks_scope ON public.chunks USING btree (workspace_id, source_type, project_id);

--
-- Name: ix_chunks_tsv; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_chunks_tsv ON public.chunks USING gin (tsv);

--
-- Name: ix_comments_doc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_doc ON public.comments USING btree (doc_id, created_at) WHERE (doc_id IS NOT NULL);

--
-- Name: ix_comments_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_parent ON public.comments USING btree (parent_comment_id) WHERE (parent_comment_id IS NOT NULL);

--
-- Name: ix_comments_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_project ON public.comments USING btree (project_id, created_at) WHERE (project_id IS NOT NULL);

--
-- Name: ix_comments_project_update; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_project_update ON public.comments USING btree (project_update_id) WHERE (project_update_id IS NOT NULL);

--
-- Name: ix_comments_sprint; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_sprint ON public.comments USING btree (sprint_id) WHERE (sprint_id IS NOT NULL);

--
-- Name: ix_comments_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_task ON public.comments USING btree (task_id, created_at) WHERE (task_id IS NOT NULL);

--
-- Name: ix_comments_work_item; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_comments_work_item ON public.comments USING btree (work_item_id, created_at) WHERE (work_item_id IS NOT NULL);

--
-- Name: ix_decision_conflicts_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_decision_conflicts_open ON public.decision_conflicts USING btree (workspace_id, status) WHERE ((status)::text = 'open'::text);

--
-- Name: ix_decisions_status_area; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_decisions_status_area ON public.decisions USING btree (workspace_id, status, area);

--
-- Name: ix_doc_blocks_doc_id_parent_block_id_sort_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_doc_blocks_doc_id_parent_block_id_sort_order ON public.doc_blocks USING btree (doc_id, parent_block_id, sort_order);

--
-- Name: ix_docs_collection_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_docs_collection_id ON public.docs USING btree (collection_id) WHERE (collection_id IS NOT NULL);

--
-- Name: ix_docs_parent_doc_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_docs_parent_doc_id ON public.docs USING btree (parent_doc_id) WHERE (parent_doc_id IS NOT NULL);

--
-- Name: ix_docs_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_docs_project ON public.docs USING btree (workspace_id, project_id) WHERE (project_id IS NOT NULL);

--
-- Name: ix_events_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_entity ON public.events USING btree (workspace_id, entity_type, entity_id, created_at);

--
-- Name: ix_events_workspace_id_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_workspace_id_created_at ON public.events USING btree (workspace_id, created_at);

--
-- Name: ix_file_derivatives_file_id_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_file_derivatives_file_id_kind ON public.file_derivatives USING btree (file_id, kind);

--
-- Name: ix_files_folder_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_files_folder_id ON public.files USING btree (folder_id) WHERE (folder_id IS NOT NULL);

--
-- Name: ix_files_index_queue; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_files_index_queue ON public.files USING btree (index_status, index_started_at) WHERE ((index_status)::text = ANY ((ARRAY['pending'::character varying, 'indexing'::character varying])::text[]));

--
-- Name: ix_files_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_files_project_id ON public.files USING btree (project_id) WHERE (project_id IS NOT NULL);

--
-- Name: ix_files_sha256; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_files_sha256 ON public.files USING btree (workspace_id, sha256) WHERE (sha256 IS NOT NULL);

--
-- Name: ix_files_workspace_id_kind_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_files_workspace_id_kind_created_at ON public.files USING btree (workspace_id, kind, created_at);

--
-- Name: ix_links_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_links_target ON public.links USING btree (workspace_id, target_type, target_id);

--
-- Name: ix_llm_usage_workspace_id_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_usage_workspace_id_created_at ON public.llm_usage USING btree (workspace_id, created_at);

--
-- Name: ix_meetings_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_meetings_project ON public.meetings USING btree (project_id, started_at) WHERE (project_id IS NOT NULL);

--
-- Name: ix_memories_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memories_embedding ON public.memories USING hnsw (embedding public.vector_cosine_ops);

--
-- Name: ix_memories_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memories_project ON public.memories USING btree (project_id) WHERE (project_id IS NOT NULL);

--
-- Name: ix_memories_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memories_scope ON public.memories USING btree (workspace_id, kind, status);

--
-- Name: ix_memories_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memories_source ON public.memories USING btree (source_type, source_id) WHERE (source_id IS NOT NULL);

--
-- Name: ix_memories_tsv; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memories_tsv ON public.memories USING gin (tsv);

--
-- Name: ix_memory_links_to_memory_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_links_to_memory_id ON public.memory_links USING btree (to_memory_id);

--
-- Name: ix_messages_channel_id_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_messages_channel_id_created_at ON public.messages USING btree (channel_id, created_at);

--
-- Name: ix_messages_parent_message_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_messages_parent_message_id ON public.messages USING btree (parent_message_id) WHERE (parent_message_id IS NOT NULL);

--
-- Name: ix_notifications_inbox; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_inbox ON public.notifications USING btree (recipient_id, created_at) WHERE (archived_at IS NULL);

--
-- Name: ix_notifications_unread; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notifications_unread ON public.notifications USING btree (recipient_id) WHERE ((read_at IS NULL) AND (archived_at IS NULL));

--
-- Name: ix_project_updates_project_id_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_project_updates_project_id_created_at ON public.project_updates USING btree (project_id, created_at);

--
-- Name: ix_projects_workspace_id_team_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_projects_workspace_id_team_id ON public.projects USING btree (workspace_id, team_id);

--
-- Name: ix_skills_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_skills_embedding ON public.skills USING hnsw (embedding public.vector_cosine_ops);

--
-- Name: ix_skills_tsv; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_skills_tsv ON public.skills USING gin (tsv);

--
-- Name: ix_sprints_project_id_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sprints_project_id_status ON public.sprints USING btree (project_id, status);

--
-- Name: ix_subscriptions_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_subscriptions_entity ON public.subscriptions USING btree (workspace_id, entity_type, entity_id);

--
-- Name: ix_task_labels_label_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_labels_label_id ON public.task_labels USING btree (label_id);

--
-- Name: ix_task_relations_related_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_relations_related_task_id ON public.task_relations USING btree (related_task_id);

--
-- Name: ix_tasks_assignee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_assignee ON public.tasks USING btree (workspace_id, assignee_id) WHERE (assignee_id IS NOT NULL);

--
-- Name: ix_tasks_parent_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_parent_task_id ON public.tasks USING btree (parent_task_id) WHERE (parent_task_id IS NOT NULL);

--
-- Name: ix_tasks_project_id_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_project_id_status ON public.tasks USING btree (project_id, status);

--
-- Name: ix_tasks_sprint_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_sprint_id ON public.tasks USING btree (sprint_id) WHERE (sprint_id IS NOT NULL);

--
-- Name: ix_tasks_work_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tasks_work_item_id ON public.tasks USING btree (work_item_id);

--
-- Name: ix_transcript_segments_transcript_id_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_transcript_segments_transcript_id_sequence ON public.transcript_segments USING btree (transcript_id, sequence);

--
-- Name: ix_work_item_labels_label_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_work_item_labels_label_id ON public.work_item_labels USING btree (label_id);

--
-- Name: ix_work_items_backlog_id_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_work_items_backlog_id_status ON public.work_items USING btree (backlog_id, status);

--
-- Name: uq_docs_published_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_docs_published_slug ON public.docs USING btree (lower((published_slug)::text)) WHERE (published_slug IS NOT NULL);

--
-- Name: uq_files_container_blob_path; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_files_container_blob_path ON public.files USING btree (container, blob_path);

--
-- Name: uq_folders_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_folders_name ON public.folders USING btree (workspace_id, COALESCE(parent_folder_id, '00000000-0000-0000-0000-000000000000'::uuid), lower((name)::text)) WHERE (deleted_at IS NULL);

--
-- Name: uq_labels_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_labels_name ON public.labels USING btree (workspace_id, COALESCE(team_id, '00000000-0000-0000-0000-000000000000'::uuid), lower((name)::text));

--
-- Name: uq_projects_workspace_id_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_projects_workspace_id_key ON public.projects USING btree (workspace_id, lower((key)::text));

--
-- Name: uq_tasks_project_id_number; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_tasks_project_id_number ON public.tasks USING btree (project_id, number);

--
-- Name: uq_teams_workspace_id_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_teams_workspace_id_key ON public.teams USING btree (workspace_id, lower((key)::text));

--
-- Name: uq_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_users_email ON public.users USING btree (lower((email)::text));

--
-- Name: uq_workspaces_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_workspaces_slug ON public.workspaces USING btree (lower((slug)::text));

--
-- Name: agent_reports agent_reports_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER agent_reports_set_updated_at BEFORE UPDATE ON public.agent_reports FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: agent_runs agent_runs_approval_contract; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER agent_runs_approval_contract BEFORE INSERT OR DELETE OR UPDATE ON public.agent_runs FOR EACH ROW EXECUTE FUNCTION public.atom_approval_run_contract();

--
-- Name: agent_runs agent_runs_atom_identity; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER agent_runs_atom_identity BEFORE INSERT OR UPDATE OF atom_id, atom_version_id, schedule_id, agent_id, workspace_id ON public.agent_runs FOR EACH ROW EXECUTE FUNCTION public.atom_run_identity();

--
-- Name: approvals approvals_atom_contract; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER approvals_atom_contract BEFORE INSERT OR DELETE OR UPDATE ON public.approvals FOR EACH ROW EXECUTE FUNCTION public.atom_approval_contract();

--
-- Name: atom_connections atom_connections_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER atom_connections_set_updated_at BEFORE UPDATE ON public.atom_connections FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: atom_grants atom_grants_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER atom_grants_set_updated_at BEFORE UPDATE ON public.atom_grants FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: atom_schedules atom_schedules_identity; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER atom_schedules_identity BEFORE INSERT OR UPDATE OF atom_id, workspace_id, timezone ON public.atom_schedules FOR EACH ROW EXECUTE FUNCTION public.atom_schedule_identity();

--
-- Name: atom_schedules atom_schedules_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER atom_schedules_set_updated_at BEFORE UPDATE ON public.atom_schedules FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: atom_versions atom_versions_immutable; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER atom_versions_immutable BEFORE DELETE OR UPDATE ON public.atom_versions FOR EACH ROW EXECUTE FUNCTION public.atom_version_immutable();

--
-- Name: atoms atoms_member_identity; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER atoms_member_identity BEFORE INSERT OR UPDATE OF member_id, workspace_id ON public.atoms FOR EACH ROW EXECUTE FUNCTION public.atom_member_identity();

--
-- Name: atoms atoms_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER atoms_set_updated_at BEFORE UPDATE ON public.atoms FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: backlogs backlogs_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER backlogs_set_updated_at BEFORE UPDATE ON public.backlogs FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: catalog_skills catalog_skills_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER catalog_skills_set_updated_at BEFORE UPDATE ON public.catalog_skills FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: channels channels_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER channels_set_updated_at BEFORE UPDATE ON public.channels FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: chunks chunks_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER chunks_set_updated_at BEFORE UPDATE ON public.chunks FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: collections collections_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER collections_set_updated_at BEFORE UPDATE ON public.collections FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: comments comments_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER comments_set_updated_at BEFORE UPDATE ON public.comments FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: decisions decisions_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER decisions_set_updated_at BEFORE UPDATE ON public.decisions FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: doc_blocks doc_blocks_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER doc_blocks_set_updated_at BEFORE UPDATE ON public.doc_blocks FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: docs docs_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER docs_set_updated_at BEFORE UPDATE ON public.docs FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: events events_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER events_append_only BEFORE UPDATE ON public.events FOR EACH ROW EXECUTE FUNCTION public.events_no_update();

--
-- Name: files files_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER files_set_updated_at BEFORE UPDATE ON public.files FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: folders folders_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER folders_set_updated_at BEFORE UPDATE ON public.folders FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: meetings meetings_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER meetings_set_updated_at BEFORE UPDATE ON public.meetings FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: members members_atom_identity; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER members_atom_identity BEFORE UPDATE OF type, agent_kind ON public.members FOR EACH ROW EXECUTE FUNCTION public.preserve_atom_member_identity();

--
-- Name: members members_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER members_set_updated_at BEFORE UPDATE ON public.members FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: memories memories_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER memories_set_updated_at BEFORE UPDATE ON public.memories FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: milestones milestones_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER milestones_set_updated_at BEFORE UPDATE ON public.milestones FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: project_updates project_updates_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER project_updates_set_updated_at BEFORE UPDATE ON public.project_updates FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: projects projects_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER projects_set_updated_at BEFORE UPDATE ON public.projects FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: recordings recordings_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER recordings_set_updated_at BEFORE UPDATE ON public.recordings FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: saved_views saved_views_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER saved_views_set_updated_at BEFORE UPDATE ON public.saved_views FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: skills skills_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER skills_set_updated_at BEFORE UPDATE ON public.skills FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: sprints sprints_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER sprints_set_updated_at BEFORE UPDATE ON public.sprints FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: tasks tasks_assign_number; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER tasks_assign_number BEFORE INSERT ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.assign_task_number();

--
-- Name: tasks tasks_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER tasks_set_updated_at BEFORE UPDATE ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: teams teams_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER teams_set_updated_at BEFORE UPDATE ON public.teams FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: users users_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER users_set_updated_at BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: work_items work_items_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER work_items_set_updated_at BEFORE UPDATE ON public.work_items FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: workspaces workspaces_set_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER workspaces_set_updated_at BEFORE UPDATE ON public.workspaces FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

--
-- Name: agent_reports fk_agent_reports_workspace_id_agent_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT fk_agent_reports_workspace_id_agent_id_members FOREIGN KEY (workspace_id, agent_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (agent_id);

--
-- Name: agent_reports fk_agent_reports_workspace_id_meeting_id_meetings; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT fk_agent_reports_workspace_id_meeting_id_meetings FOREIGN KEY (workspace_id, meeting_id) REFERENCES public.meetings(workspace_id, id) ON DELETE SET NULL (meeting_id);

--
-- Name: agent_reports fk_agent_reports_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT fk_agent_reports_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE SET NULL (project_id);

--
-- Name: agent_reports fk_agent_reports_workspace_id_run_id_agent_runs; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT fk_agent_reports_workspace_id_run_id_agent_runs FOREIGN KEY (workspace_id, run_id) REFERENCES public.agent_runs(workspace_id, id) ON DELETE SET NULL (run_id);

--
-- Name: agent_reports fk_agent_reports_workspace_id_sprint_id_sprints; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT fk_agent_reports_workspace_id_sprint_id_sprints FOREIGN KEY (workspace_id, sprint_id) REFERENCES public.sprints(workspace_id, id) ON DELETE SET NULL (sprint_id);

--
-- Name: agent_reports fk_agent_reports_workspace_id_task_id_tasks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT fk_agent_reports_workspace_id_task_id_tasks FOREIGN KEY (workspace_id, task_id) REFERENCES public.tasks(workspace_id, id) ON DELETE SET NULL (task_id);

--
-- Name: agent_reports fk_agent_reports_workspace_id_work_item_id_work_items; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT fk_agent_reports_workspace_id_work_item_id_work_items FOREIGN KEY (workspace_id, work_item_id) REFERENCES public.work_items(workspace_id, id) ON DELETE SET NULL (work_item_id);

--
-- Name: agent_reports fk_agent_reports_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_reports
    ADD CONSTRAINT fk_agent_reports_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: agent_runs fk_agent_runs_workspace_id_agent_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT fk_agent_runs_workspace_id_agent_id_members FOREIGN KEY (workspace_id, agent_id) REFERENCES public.members(workspace_id, id) ON DELETE CASCADE;

--
-- Name: agent_runs fk_agent_runs_workspace_id_atom_id_atoms; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT fk_agent_runs_workspace_id_atom_id_atoms FOREIGN KEY (workspace_id, atom_id) REFERENCES public.atoms(workspace_id, id) ON DELETE SET NULL (atom_id);

--
-- Name: agent_runs fk_agent_runs_workspace_id_atom_version_id_atom_versions; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT fk_agent_runs_workspace_id_atom_version_id_atom_versions FOREIGN KEY (workspace_id, atom_version_id) REFERENCES public.atom_versions(workspace_id, id) ON DELETE SET NULL (atom_version_id);

--
-- Name: agent_runs fk_agent_runs_workspace_id_channel_id_channels; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT fk_agent_runs_workspace_id_channel_id_channels FOREIGN KEY (workspace_id, channel_id) REFERENCES public.channels(workspace_id, id) ON DELETE SET NULL (channel_id);

--
-- Name: agent_runs fk_agent_runs_workspace_id_meeting_id_meetings; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT fk_agent_runs_workspace_id_meeting_id_meetings FOREIGN KEY (workspace_id, meeting_id) REFERENCES public.meetings(workspace_id, id) ON DELETE SET NULL (meeting_id);

--
-- Name: agent_runs fk_agent_runs_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT fk_agent_runs_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE SET NULL (project_id);

--
-- Name: agent_runs fk_agent_runs_workspace_id_schedule_id_atom_schedules; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT fk_agent_runs_workspace_id_schedule_id_atom_schedules FOREIGN KEY (workspace_id, schedule_id) REFERENCES public.atom_schedules(workspace_id, id) ON DELETE SET NULL (schedule_id);

--
-- Name: agent_runs fk_agent_runs_workspace_id_task_id_tasks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT fk_agent_runs_workspace_id_task_id_tasks FOREIGN KEY (workspace_id, task_id) REFERENCES public.tasks(workspace_id, id) ON DELETE SET NULL (task_id);

--
-- Name: agent_runs fk_agent_runs_workspace_id_triggered_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT fk_agent_runs_workspace_id_triggered_by_members FOREIGN KEY (workspace_id, triggered_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (triggered_by);

--
-- Name: agent_runs fk_agent_runs_workspace_id_work_item_id_work_items; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT fk_agent_runs_workspace_id_work_item_id_work_items FOREIGN KEY (workspace_id, work_item_id) REFERENCES public.work_items(workspace_id, id) ON DELETE SET NULL (work_item_id);

--
-- Name: agent_runs fk_agent_runs_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_runs
    ADD CONSTRAINT fk_agent_runs_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: approvals fk_approvals_workspace_id_assigned_to_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT fk_approvals_workspace_id_assigned_to_members FOREIGN KEY (workspace_id, assigned_to) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (assigned_to);

--
-- Name: approvals fk_approvals_workspace_id_atom_id_atoms; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT fk_approvals_workspace_id_atom_id_atoms FOREIGN KEY (workspace_id, atom_id) REFERENCES public.atoms(workspace_id, id);

--
-- Name: approvals fk_approvals_workspace_id_decided_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT fk_approvals_workspace_id_decided_by_members FOREIGN KEY (workspace_id, decided_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (decided_by);

--
-- Name: approvals fk_approvals_workspace_id_execution_run_id_agent_runs; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT fk_approvals_workspace_id_execution_run_id_agent_runs FOREIGN KEY (workspace_id, execution_run_id) REFERENCES public.agent_runs(workspace_id, id);

--
-- Name: approvals fk_approvals_workspace_id_report_id_agent_reports; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT fk_approvals_workspace_id_report_id_agent_reports FOREIGN KEY (workspace_id, report_id) REFERENCES public.agent_reports(workspace_id, id) ON DELETE SET NULL (report_id);

--
-- Name: approvals fk_approvals_workspace_id_requested_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT fk_approvals_workspace_id_requested_by_members FOREIGN KEY (workspace_id, requested_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (requested_by);

--
-- Name: approvals fk_approvals_workspace_id_run_id_agent_runs; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT fk_approvals_workspace_id_run_id_agent_runs FOREIGN KEY (workspace_id, run_id) REFERENCES public.agent_runs(workspace_id, id) ON DELETE SET NULL (run_id);

--
-- Name: approvals fk_approvals_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approvals
    ADD CONSTRAINT fk_approvals_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: atom_connections fk_atom_connections_workspace_id_atom_id_atoms; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_connections
    ADD CONSTRAINT fk_atom_connections_workspace_id_atom_id_atoms FOREIGN KEY (workspace_id, atom_id) REFERENCES public.atoms(workspace_id, id) ON DELETE CASCADE;

--
-- Name: atom_connections fk_atom_connections_workspace_id_connected_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_connections
    ADD CONSTRAINT fk_atom_connections_workspace_id_connected_by_members FOREIGN KEY (workspace_id, connected_by) REFERENCES public.members(workspace_id, id);

--
-- Name: atom_connections fk_atom_connections_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_connections
    ADD CONSTRAINT fk_atom_connections_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: atom_grants fk_atom_grants_workspace_id_atom_id_atoms; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_grants
    ADD CONSTRAINT fk_atom_grants_workspace_id_atom_id_atoms FOREIGN KEY (workspace_id, atom_id) REFERENCES public.atoms(workspace_id, id) ON DELETE CASCADE;

--
-- Name: atom_grants fk_atom_grants_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_grants
    ADD CONSTRAINT fk_atom_grants_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: atom_schedules fk_atom_schedules_workspace_id_atom_id_atoms; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_schedules
    ADD CONSTRAINT fk_atom_schedules_workspace_id_atom_id_atoms FOREIGN KEY (workspace_id, atom_id) REFERENCES public.atoms(workspace_id, id) ON DELETE CASCADE;

--
-- Name: atom_schedules fk_atom_schedules_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_schedules
    ADD CONSTRAINT fk_atom_schedules_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: atom_skills fk_atom_skills_catalog_skill_id_skill_version_catalog_skills; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_skills
    ADD CONSTRAINT fk_atom_skills_catalog_skill_id_skill_version_catalog_skills FOREIGN KEY (catalog_skill_id, skill_version) REFERENCES public.catalog_skills(id, version);

--
-- Name: atom_skills fk_atom_skills_workspace_id_added_by_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_skills
    ADD CONSTRAINT fk_atom_skills_workspace_id_added_by_member_id_members FOREIGN KEY (workspace_id, added_by_member_id) REFERENCES public.members(workspace_id, id);

--
-- Name: atom_skills fk_atom_skills_workspace_id_atom_id_atoms; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_skills
    ADD CONSTRAINT fk_atom_skills_workspace_id_atom_id_atoms FOREIGN KEY (workspace_id, atom_id) REFERENCES public.atoms(workspace_id, id) ON DELETE CASCADE;

--
-- Name: atom_skills fk_atom_skills_workspace_id_skill_id_skill_version_skills; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_skills
    ADD CONSTRAINT fk_atom_skills_workspace_id_skill_id_skill_version_skills FOREIGN KEY (workspace_id, skill_id, skill_version) REFERENCES public.skills(workspace_id, id, version);

--
-- Name: atom_skills fk_atom_skills_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_skills
    ADD CONSTRAINT fk_atom_skills_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: atom_versions fk_atom_versions_workspace_id_atom_id_atoms; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_versions
    ADD CONSTRAINT fk_atom_versions_workspace_id_atom_id_atoms FOREIGN KEY (workspace_id, atom_id) REFERENCES public.atoms(workspace_id, id) ON DELETE CASCADE;

--
-- Name: atom_versions fk_atom_versions_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_versions
    ADD CONSTRAINT fk_atom_versions_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id);

--
-- Name: atom_versions fk_atom_versions_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atom_versions
    ADD CONSTRAINT fk_atom_versions_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: atoms fk_atoms_active_version; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atoms
    ADD CONSTRAINT fk_atoms_active_version FOREIGN KEY (workspace_id, id, active_version_id) REFERENCES public.atom_versions(workspace_id, atom_id, id);

--
-- Name: atoms fk_atoms_workspace_id_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atoms
    ADD CONSTRAINT fk_atoms_workspace_id_member_id_members FOREIGN KEY (workspace_id, member_id) REFERENCES public.members(workspace_id, id);

--
-- Name: atoms fk_atoms_workspace_id_owner_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atoms
    ADD CONSTRAINT fk_atoms_workspace_id_owner_member_id_members FOREIGN KEY (workspace_id, owner_member_id) REFERENCES public.members(workspace_id, id);

--
-- Name: atoms fk_atoms_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.atoms
    ADD CONSTRAINT fk_atoms_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: attachments fk_attachments_workspace_id_agent_report_id_agent_reports; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_agent_report_id_agent_reports FOREIGN KEY (workspace_id, agent_report_id) REFERENCES public.agent_reports(workspace_id, id) ON DELETE CASCADE;

--
-- Name: attachments fk_attachments_workspace_id_comment_id_comments; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_comment_id_comments FOREIGN KEY (workspace_id, comment_id) REFERENCES public.comments(workspace_id, id) ON DELETE CASCADE;

--
-- Name: attachments fk_attachments_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: attachments fk_attachments_workspace_id_doc_id_docs; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_doc_id_docs FOREIGN KEY (workspace_id, doc_id) REFERENCES public.docs(workspace_id, id) ON DELETE CASCADE;

--
-- Name: attachments fk_attachments_workspace_id_file_id_files; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_file_id_files FOREIGN KEY (workspace_id, file_id) REFERENCES public.files(workspace_id, id) ON DELETE CASCADE;

--
-- Name: attachments fk_attachments_workspace_id_meeting_id_meetings; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_meeting_id_meetings FOREIGN KEY (workspace_id, meeting_id) REFERENCES public.meetings(workspace_id, id) ON DELETE CASCADE;

--
-- Name: attachments fk_attachments_workspace_id_message_id_messages; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_message_id_messages FOREIGN KEY (workspace_id, message_id) REFERENCES public.messages(workspace_id, id) ON DELETE CASCADE;

--
-- Name: attachments fk_attachments_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: attachments fk_attachments_workspace_id_project_update_id_project_updates; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_project_update_id_project_updates FOREIGN KEY (workspace_id, project_update_id) REFERENCES public.project_updates(workspace_id, id) ON DELETE CASCADE;

--
-- Name: attachments fk_attachments_workspace_id_task_id_tasks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_task_id_tasks FOREIGN KEY (workspace_id, task_id) REFERENCES public.tasks(workspace_id, id) ON DELETE CASCADE;

--
-- Name: attachments fk_attachments_workspace_id_work_item_id_work_items; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_work_item_id_work_items FOREIGN KEY (workspace_id, work_item_id) REFERENCES public.work_items(workspace_id, id) ON DELETE CASCADE;

--
-- Name: attachments fk_attachments_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachments
    ADD CONSTRAINT fk_attachments_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: backlogs fk_backlogs_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backlogs
    ADD CONSTRAINT fk_backlogs_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: backlogs fk_backlogs_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backlogs
    ADD CONSTRAINT fk_backlogs_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: channel_members fk_channel_members_workspace_id_channel_id_channels; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channel_members
    ADD CONSTRAINT fk_channel_members_workspace_id_channel_id_channels FOREIGN KEY (workspace_id, channel_id) REFERENCES public.channels(workspace_id, id) ON DELETE CASCADE;

--
-- Name: channel_members fk_channel_members_workspace_id_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channel_members
    ADD CONSTRAINT fk_channel_members_workspace_id_member_id_members FOREIGN KEY (workspace_id, member_id) REFERENCES public.members(workspace_id, id) ON DELETE CASCADE;

--
-- Name: channel_members fk_channel_members_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channel_members
    ADD CONSTRAINT fk_channel_members_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: channels fk_channels_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT fk_channels_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: channels fk_channels_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT fk_channels_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: channels fk_channels_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT fk_channels_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE CASCADE;

--
-- Name: channels fk_channels_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT fk_channels_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: chunks fk_chunks_workspace_id_channel_id_channels; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT fk_chunks_workspace_id_channel_id_channels FOREIGN KEY (workspace_id, channel_id) REFERENCES public.channels(workspace_id, id) ON DELETE CASCADE;

--
-- Name: chunks fk_chunks_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT fk_chunks_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: chunks fk_chunks_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT fk_chunks_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE CASCADE;

--
-- Name: chunks fk_chunks_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT fk_chunks_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: collections fk_collections_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collections
    ADD CONSTRAINT fk_collections_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: collections fk_collections_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collections
    ADD CONSTRAINT fk_collections_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: collections fk_collections_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collections
    ADD CONSTRAINT fk_collections_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE SET NULL (team_id);

--
-- Name: collections fk_collections_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.collections
    ADD CONSTRAINT fk_collections_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: comments fk_comments_workspace_id_author_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT fk_comments_workspace_id_author_id_members FOREIGN KEY (workspace_id, author_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (author_id);

--
-- Name: comments fk_comments_workspace_id_block_id_doc_blocks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT fk_comments_workspace_id_block_id_doc_blocks FOREIGN KEY (workspace_id, block_id) REFERENCES public.doc_blocks(workspace_id, id) ON DELETE SET NULL (block_id);

--
-- Name: comments fk_comments_workspace_id_doc_id_docs; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT fk_comments_workspace_id_doc_id_docs FOREIGN KEY (workspace_id, doc_id) REFERENCES public.docs(workspace_id, id) ON DELETE CASCADE;

--
-- Name: comments fk_comments_workspace_id_parent_comment_id_comments; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT fk_comments_workspace_id_parent_comment_id_comments FOREIGN KEY (workspace_id, parent_comment_id) REFERENCES public.comments(workspace_id, id) ON DELETE CASCADE;

--
-- Name: comments fk_comments_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT fk_comments_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: comments fk_comments_workspace_id_project_update_id_project_updates; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT fk_comments_workspace_id_project_update_id_project_updates FOREIGN KEY (workspace_id, project_update_id) REFERENCES public.project_updates(workspace_id, id) ON DELETE CASCADE;

--
-- Name: comments fk_comments_workspace_id_resolved_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT fk_comments_workspace_id_resolved_by_members FOREIGN KEY (workspace_id, resolved_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (resolved_by);

--
-- Name: comments fk_comments_workspace_id_sprint_id_sprints; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT fk_comments_workspace_id_sprint_id_sprints FOREIGN KEY (workspace_id, sprint_id) REFERENCES public.sprints(workspace_id, id) ON DELETE CASCADE;

--
-- Name: comments fk_comments_workspace_id_task_id_tasks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT fk_comments_workspace_id_task_id_tasks FOREIGN KEY (workspace_id, task_id) REFERENCES public.tasks(workspace_id, id) ON DELETE CASCADE;

--
-- Name: comments fk_comments_workspace_id_work_item_id_work_items; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT fk_comments_workspace_id_work_item_id_work_items FOREIGN KEY (workspace_id, work_item_id) REFERENCES public.work_items(workspace_id, id) ON DELETE CASCADE;

--
-- Name: comments fk_comments_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comments
    ADD CONSTRAINT fk_comments_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: decision_conflicts fk_decision_conflicts_workspace_id_decision_id_decisions; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_conflicts
    ADD CONSTRAINT fk_decision_conflicts_workspace_id_decision_id_decisions FOREIGN KEY (workspace_id, decision_id) REFERENCES public.decisions(workspace_id, memory_id) ON DELETE CASCADE;

--
-- Name: decision_conflicts fk_decision_conflicts_workspace_id_flagged_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_conflicts
    ADD CONSTRAINT fk_decision_conflicts_workspace_id_flagged_by_members FOREIGN KEY (workspace_id, flagged_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (flagged_by);

--
-- Name: decision_conflicts fk_decision_conflicts_workspace_id_resolved_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_conflicts
    ADD CONSTRAINT fk_decision_conflicts_workspace_id_resolved_by_members FOREIGN KEY (workspace_id, resolved_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (resolved_by);

--
-- Name: decision_conflicts fk_decision_conflicts_workspace_id_run_id_agent_runs; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_conflicts
    ADD CONSTRAINT fk_decision_conflicts_workspace_id_run_id_agent_runs FOREIGN KEY (workspace_id, run_id) REFERENCES public.agent_runs(workspace_id, id) ON DELETE SET NULL (run_id);

--
-- Name: decision_conflicts fk_decision_conflicts_workspace_id_task_id_tasks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_conflicts
    ADD CONSTRAINT fk_decision_conflicts_workspace_id_task_id_tasks FOREIGN KEY (workspace_id, task_id) REFERENCES public.tasks(workspace_id, id) ON DELETE SET NULL (task_id);

--
-- Name: decision_conflicts fk_decision_conflicts_workspace_id_work_item_id_work_items; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_conflicts
    ADD CONSTRAINT fk_decision_conflicts_workspace_id_work_item_id_work_items FOREIGN KEY (workspace_id, work_item_id) REFERENCES public.work_items(workspace_id, id) ON DELETE SET NULL (work_item_id);

--
-- Name: decision_conflicts fk_decision_conflicts_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decision_conflicts
    ADD CONSTRAINT fk_decision_conflicts_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: decisions fk_decisions_workspace_id_memory_id_memories; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT fk_decisions_workspace_id_memory_id_memories FOREIGN KEY (workspace_id, memory_id) REFERENCES public.memories(workspace_id, id) ON DELETE CASCADE;

--
-- Name: decisions fk_decisions_workspace_id_owner_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT fk_decisions_workspace_id_owner_member_id_members FOREIGN KEY (workspace_id, owner_member_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (owner_member_id);

--
-- Name: decisions fk_decisions_workspace_id_task_id_tasks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT fk_decisions_workspace_id_task_id_tasks FOREIGN KEY (workspace_id, task_id) REFERENCES public.tasks(workspace_id, id) ON DELETE SET NULL (task_id);

--
-- Name: decisions fk_decisions_workspace_id_work_item_id_work_items; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT fk_decisions_workspace_id_work_item_id_work_items FOREIGN KEY (workspace_id, work_item_id) REFERENCES public.work_items(workspace_id, id) ON DELETE SET NULL (work_item_id);

--
-- Name: decisions fk_decisions_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.decisions
    ADD CONSTRAINT fk_decisions_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: doc_blocks fk_doc_blocks_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_blocks
    ADD CONSTRAINT fk_doc_blocks_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: doc_blocks fk_doc_blocks_workspace_id_doc_id_docs; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_blocks
    ADD CONSTRAINT fk_doc_blocks_workspace_id_doc_id_docs FOREIGN KEY (workspace_id, doc_id) REFERENCES public.docs(workspace_id, id) ON DELETE CASCADE;

--
-- Name: doc_blocks fk_doc_blocks_workspace_id_last_edited_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_blocks
    ADD CONSTRAINT fk_doc_blocks_workspace_id_last_edited_by_members FOREIGN KEY (workspace_id, last_edited_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (last_edited_by);

--
-- Name: doc_blocks fk_doc_blocks_workspace_id_parent_block_id_doc_blocks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_blocks
    ADD CONSTRAINT fk_doc_blocks_workspace_id_parent_block_id_doc_blocks FOREIGN KEY (workspace_id, parent_block_id) REFERENCES public.doc_blocks(workspace_id, id) ON DELETE CASCADE;

--
-- Name: doc_blocks fk_doc_blocks_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_blocks
    ADD CONSTRAINT fk_doc_blocks_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: doc_versions fk_doc_versions_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_versions
    ADD CONSTRAINT fk_doc_versions_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: doc_versions fk_doc_versions_workspace_id_doc_id_docs; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_versions
    ADD CONSTRAINT fk_doc_versions_workspace_id_doc_id_docs FOREIGN KEY (workspace_id, doc_id) REFERENCES public.docs(workspace_id, id) ON DELETE CASCADE;

--
-- Name: doc_versions fk_doc_versions_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_versions
    ADD CONSTRAINT fk_doc_versions_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: docs fk_docs_workspace_id_collection_id_collections; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.docs
    ADD CONSTRAINT fk_docs_workspace_id_collection_id_collections FOREIGN KEY (workspace_id, collection_id) REFERENCES public.collections(workspace_id, id) ON DELETE CASCADE;

--
-- Name: docs fk_docs_workspace_id_cover_file_id_files; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.docs
    ADD CONSTRAINT fk_docs_workspace_id_cover_file_id_files FOREIGN KEY (workspace_id, cover_file_id) REFERENCES public.files(workspace_id, id) ON DELETE SET NULL (cover_file_id);

--
-- Name: docs fk_docs_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.docs
    ADD CONSTRAINT fk_docs_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: docs fk_docs_workspace_id_last_edited_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.docs
    ADD CONSTRAINT fk_docs_workspace_id_last_edited_by_members FOREIGN KEY (workspace_id, last_edited_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (last_edited_by);

--
-- Name: docs fk_docs_workspace_id_parent_doc_id_docs; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.docs
    ADD CONSTRAINT fk_docs_workspace_id_parent_doc_id_docs FOREIGN KEY (workspace_id, parent_doc_id) REFERENCES public.docs(workspace_id, id) ON DELETE CASCADE;

--
-- Name: docs fk_docs_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.docs
    ADD CONSTRAINT fk_docs_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: docs fk_docs_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.docs
    ADD CONSTRAINT fk_docs_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE SET NULL (team_id);

--
-- Name: docs fk_docs_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.docs
    ADD CONSTRAINT fk_docs_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: events fk_events_workspace_id_actor_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT fk_events_workspace_id_actor_id_members FOREIGN KEY (workspace_id, actor_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (actor_id);

--
-- Name: events fk_events_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT fk_events_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: favorites fk_favorites_workspace_id_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.favorites
    ADD CONSTRAINT fk_favorites_workspace_id_member_id_members FOREIGN KEY (workspace_id, member_id) REFERENCES public.members(workspace_id, id) ON DELETE CASCADE;

--
-- Name: favorites fk_favorites_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.favorites
    ADD CONSTRAINT fk_favorites_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: file_derivatives fk_file_derivatives_workspace_id_file_id_files; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_derivatives
    ADD CONSTRAINT fk_file_derivatives_workspace_id_file_id_files FOREIGN KEY (workspace_id, file_id) REFERENCES public.files(workspace_id, id) ON DELETE CASCADE;

--
-- Name: file_derivatives fk_file_derivatives_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.file_derivatives
    ADD CONSTRAINT fk_file_derivatives_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: files fk_files_workspace_id_folder_id_folders; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT fk_files_workspace_id_folder_id_folders FOREIGN KEY (workspace_id, folder_id) REFERENCES public.folders(workspace_id, id) ON DELETE SET NULL (folder_id);

--
-- Name: files fk_files_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT fk_files_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE SET NULL (project_id);

--
-- Name: files fk_files_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT fk_files_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE SET NULL (team_id);

--
-- Name: files fk_files_workspace_id_uploaded_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT fk_files_workspace_id_uploaded_by_members FOREIGN KEY (workspace_id, uploaded_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (uploaded_by);

--
-- Name: files fk_files_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT fk_files_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: folders fk_folders_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT fk_folders_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: folders fk_folders_workspace_id_parent_folder_id_folders; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT fk_folders_workspace_id_parent_folder_id_folders FOREIGN KEY (workspace_id, parent_folder_id) REFERENCES public.folders(workspace_id, id) ON DELETE CASCADE;

--
-- Name: folders fk_folders_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT fk_folders_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: folders fk_folders_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT fk_folders_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE SET NULL (team_id);

--
-- Name: folders fk_folders_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT fk_folders_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: invitations fk_invitations_workspace_id_invited_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT fk_invitations_workspace_id_invited_by_members FOREIGN KEY (workspace_id, invited_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (invited_by);

--
-- Name: invitations fk_invitations_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invitations
    ADD CONSTRAINT fk_invitations_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: labels fk_labels_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.labels
    ADD CONSTRAINT fk_labels_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE CASCADE;

--
-- Name: labels fk_labels_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.labels
    ADD CONSTRAINT fk_labels_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: links fk_links_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.links
    ADD CONSTRAINT fk_links_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: llm_usage fk_llm_usage_workspace_id_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_usage
    ADD CONSTRAINT fk_llm_usage_workspace_id_member_id_members FOREIGN KEY (workspace_id, member_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (member_id);

--
-- Name: llm_usage fk_llm_usage_workspace_id_run_id_agent_runs; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_usage
    ADD CONSTRAINT fk_llm_usage_workspace_id_run_id_agent_runs FOREIGN KEY (workspace_id, run_id) REFERENCES public.agent_runs(workspace_id, id) ON DELETE SET NULL (run_id);

--
-- Name: llm_usage fk_llm_usage_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_usage
    ADD CONSTRAINT fk_llm_usage_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: meeting_participants fk_meeting_participants_workspace_id_meeting_id_meetings; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_participants
    ADD CONSTRAINT fk_meeting_participants_workspace_id_meeting_id_meetings FOREIGN KEY (workspace_id, meeting_id) REFERENCES public.meetings(workspace_id, id) ON DELETE CASCADE;

--
-- Name: meeting_participants fk_meeting_participants_workspace_id_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_participants
    ADD CONSTRAINT fk_meeting_participants_workspace_id_member_id_members FOREIGN KEY (workspace_id, member_id) REFERENCES public.members(workspace_id, id) ON DELETE CASCADE;

--
-- Name: meeting_participants fk_meeting_participants_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meeting_participants
    ADD CONSTRAINT fk_meeting_participants_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: meetings fk_meetings_workspace_id_channel_id_channels; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT fk_meetings_workspace_id_channel_id_channels FOREIGN KEY (workspace_id, channel_id) REFERENCES public.channels(workspace_id, id) ON DELETE SET NULL (channel_id);

--
-- Name: meetings fk_meetings_workspace_id_host_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT fk_meetings_workspace_id_host_id_members FOREIGN KEY (workspace_id, host_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (host_id);

--
-- Name: meetings fk_meetings_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT fk_meetings_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE SET NULL (project_id);

--
-- Name: meetings fk_meetings_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT fk_meetings_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE SET NULL (team_id);

--
-- Name: meetings fk_meetings_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meetings
    ADD CONSTRAINT fk_meetings_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: members fk_members_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.members
    ADD CONSTRAINT fk_members_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE RESTRICT;

--
-- Name: members fk_members_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.members
    ADD CONSTRAINT fk_members_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: memories fk_memories_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memories
    ADD CONSTRAINT fk_memories_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: memories fk_memories_workspace_id_owner_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memories
    ADD CONSTRAINT fk_memories_workspace_id_owner_member_id_members FOREIGN KEY (workspace_id, owner_member_id) REFERENCES public.members(workspace_id, id) ON DELETE CASCADE;

--
-- Name: memories fk_memories_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memories
    ADD CONSTRAINT fk_memories_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: memories fk_memories_workspace_id_source_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memories
    ADD CONSTRAINT fk_memories_workspace_id_source_member_id_members FOREIGN KEY (workspace_id, source_member_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (source_member_id);

--
-- Name: memories fk_memories_workspace_id_superseded_by_memories; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memories
    ADD CONSTRAINT fk_memories_workspace_id_superseded_by_memories FOREIGN KEY (workspace_id, superseded_by) REFERENCES public.memories(workspace_id, id) ON DELETE SET NULL (superseded_by);

--
-- Name: memories fk_memories_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memories
    ADD CONSTRAINT fk_memories_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE CASCADE;

--
-- Name: memories fk_memories_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memories
    ADD CONSTRAINT fk_memories_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: memory_links fk_memory_links_workspace_id_from_memory_id_memories; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_links
    ADD CONSTRAINT fk_memory_links_workspace_id_from_memory_id_memories FOREIGN KEY (workspace_id, from_memory_id) REFERENCES public.memories(workspace_id, id) ON DELETE CASCADE;

--
-- Name: memory_links fk_memory_links_workspace_id_to_memory_id_memories; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_links
    ADD CONSTRAINT fk_memory_links_workspace_id_to_memory_id_memories FOREIGN KEY (workspace_id, to_memory_id) REFERENCES public.memories(workspace_id, id) ON DELETE CASCADE;

--
-- Name: memory_links fk_memory_links_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_links
    ADD CONSTRAINT fk_memory_links_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: messages fk_messages_workspace_id_author_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT fk_messages_workspace_id_author_id_members FOREIGN KEY (workspace_id, author_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (author_id);

--
-- Name: messages fk_messages_workspace_id_channel_id_channels; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT fk_messages_workspace_id_channel_id_channels FOREIGN KEY (workspace_id, channel_id) REFERENCES public.channels(workspace_id, id) ON DELETE CASCADE;

--
-- Name: messages fk_messages_workspace_id_parent_message_id_messages; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT fk_messages_workspace_id_parent_message_id_messages FOREIGN KEY (workspace_id, parent_message_id) REFERENCES public.messages(workspace_id, id) ON DELETE SET NULL (parent_message_id);

--
-- Name: messages fk_messages_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT fk_messages_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: milestones fk_milestones_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.milestones
    ADD CONSTRAINT fk_milestones_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: milestones fk_milestones_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.milestones
    ADD CONSTRAINT fk_milestones_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: notifications fk_notifications_workspace_id_actor_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT fk_notifications_workspace_id_actor_id_members FOREIGN KEY (workspace_id, actor_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (actor_id);

--
-- Name: notifications fk_notifications_workspace_id_recipient_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT fk_notifications_workspace_id_recipient_id_members FOREIGN KEY (workspace_id, recipient_id) REFERENCES public.members(workspace_id, id) ON DELETE CASCADE;

--
-- Name: notifications fk_notifications_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT fk_notifications_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: project_updates fk_project_updates_workspace_id_author_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_updates
    ADD CONSTRAINT fk_project_updates_workspace_id_author_id_members FOREIGN KEY (workspace_id, author_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (author_id);

--
-- Name: project_updates fk_project_updates_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_updates
    ADD CONSTRAINT fk_project_updates_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: project_updates fk_project_updates_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_updates
    ADD CONSTRAINT fk_project_updates_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: projects fk_projects_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT fk_projects_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: projects fk_projects_workspace_id_lead_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT fk_projects_workspace_id_lead_id_members FOREIGN KEY (workspace_id, lead_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (lead_id);

--
-- Name: projects fk_projects_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT fk_projects_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE SET NULL (team_id);

--
-- Name: projects fk_projects_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT fk_projects_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: reactions fk_reactions_workspace_id_comment_id_comments; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reactions
    ADD CONSTRAINT fk_reactions_workspace_id_comment_id_comments FOREIGN KEY (workspace_id, comment_id) REFERENCES public.comments(workspace_id, id) ON DELETE CASCADE;

--
-- Name: reactions fk_reactions_workspace_id_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reactions
    ADD CONSTRAINT fk_reactions_workspace_id_member_id_members FOREIGN KEY (workspace_id, member_id) REFERENCES public.members(workspace_id, id) ON DELETE CASCADE;

--
-- Name: reactions fk_reactions_workspace_id_message_id_messages; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reactions
    ADD CONSTRAINT fk_reactions_workspace_id_message_id_messages FOREIGN KEY (workspace_id, message_id) REFERENCES public.messages(workspace_id, id) ON DELETE CASCADE;

--
-- Name: reactions fk_reactions_workspace_id_task_id_tasks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reactions
    ADD CONSTRAINT fk_reactions_workspace_id_task_id_tasks FOREIGN KEY (workspace_id, task_id) REFERENCES public.tasks(workspace_id, id) ON DELETE CASCADE;

--
-- Name: reactions fk_reactions_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reactions
    ADD CONSTRAINT fk_reactions_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: recordings fk_recordings_workspace_id_file_id_files; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT fk_recordings_workspace_id_file_id_files FOREIGN KEY (workspace_id, file_id) REFERENCES public.files(workspace_id, id) ON DELETE SET NULL (file_id);

--
-- Name: recordings fk_recordings_workspace_id_meeting_id_meetings; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT fk_recordings_workspace_id_meeting_id_meetings FOREIGN KEY (workspace_id, meeting_id) REFERENCES public.meetings(workspace_id, id) ON DELETE CASCADE;

--
-- Name: recordings fk_recordings_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recordings
    ADD CONSTRAINT fk_recordings_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: saved_views fk_saved_views_workspace_id_owner_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_views
    ADD CONSTRAINT fk_saved_views_workspace_id_owner_id_members FOREIGN KEY (workspace_id, owner_id) REFERENCES public.members(workspace_id, id) ON DELETE CASCADE;

--
-- Name: saved_views fk_saved_views_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_views
    ADD CONSTRAINT fk_saved_views_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: saved_views fk_saved_views_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_views
    ADD CONSTRAINT fk_saved_views_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE CASCADE;

--
-- Name: saved_views fk_saved_views_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_views
    ADD CONSTRAINT fk_saved_views_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: skills fk_skills_workspace_id_owner_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT fk_skills_workspace_id_owner_member_id_members FOREIGN KEY (workspace_id, owner_member_id) REFERENCES public.members(workspace_id, id);

--
-- Name: skills fk_skills_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.skills
    ADD CONSTRAINT fk_skills_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: sprints fk_sprints_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sprints
    ADD CONSTRAINT fk_sprints_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: sprints fk_sprints_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sprints
    ADD CONSTRAINT fk_sprints_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: subscriptions fk_subscriptions_workspace_id_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT fk_subscriptions_workspace_id_member_id_members FOREIGN KEY (workspace_id, member_id) REFERENCES public.members(workspace_id, id) ON DELETE CASCADE;

--
-- Name: subscriptions fk_subscriptions_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subscriptions
    ADD CONSTRAINT fk_subscriptions_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: task_labels fk_task_labels_workspace_id_label_id_labels; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_labels
    ADD CONSTRAINT fk_task_labels_workspace_id_label_id_labels FOREIGN KEY (workspace_id, label_id) REFERENCES public.labels(workspace_id, id) ON DELETE CASCADE;

--
-- Name: task_labels fk_task_labels_workspace_id_task_id_tasks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_labels
    ADD CONSTRAINT fk_task_labels_workspace_id_task_id_tasks FOREIGN KEY (workspace_id, task_id) REFERENCES public.tasks(workspace_id, id) ON DELETE CASCADE;

--
-- Name: task_labels fk_task_labels_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_labels
    ADD CONSTRAINT fk_task_labels_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: task_relations fk_task_relations_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_relations
    ADD CONSTRAINT fk_task_relations_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: task_relations fk_task_relations_workspace_id_related_task_id_tasks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_relations
    ADD CONSTRAINT fk_task_relations_workspace_id_related_task_id_tasks FOREIGN KEY (workspace_id, related_task_id) REFERENCES public.tasks(workspace_id, id) ON DELETE CASCADE;

--
-- Name: task_relations fk_task_relations_workspace_id_task_id_tasks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_relations
    ADD CONSTRAINT fk_task_relations_workspace_id_task_id_tasks FOREIGN KEY (workspace_id, task_id) REFERENCES public.tasks(workspace_id, id) ON DELETE CASCADE;

--
-- Name: task_relations fk_task_relations_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_relations
    ADD CONSTRAINT fk_task_relations_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: tasks fk_tasks_workspace_id_assignee_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_tasks_workspace_id_assignee_id_members FOREIGN KEY (workspace_id, assignee_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (assignee_id);

--
-- Name: tasks fk_tasks_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_tasks_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: tasks fk_tasks_workspace_id_parent_task_id_tasks; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_tasks_workspace_id_parent_task_id_tasks FOREIGN KEY (workspace_id, parent_task_id) REFERENCES public.tasks(workspace_id, id) ON DELETE SET NULL (parent_task_id);

--
-- Name: tasks fk_tasks_workspace_id_project_id_milestone_id_milestones; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_tasks_workspace_id_project_id_milestone_id_milestones FOREIGN KEY (workspace_id, project_id, milestone_id) REFERENCES public.milestones(workspace_id, project_id, id) ON DELETE SET NULL (milestone_id);

--
-- Name: tasks fk_tasks_workspace_id_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_tasks_workspace_id_project_id_projects FOREIGN KEY (workspace_id, project_id) REFERENCES public.projects(workspace_id, id) ON DELETE CASCADE;

--
-- Name: tasks fk_tasks_workspace_id_project_id_sprint_id_sprints; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_tasks_workspace_id_project_id_sprint_id_sprints FOREIGN KEY (workspace_id, project_id, sprint_id) REFERENCES public.sprints(workspace_id, project_id, id) ON DELETE SET NULL (sprint_id);

--
-- Name: tasks fk_tasks_workspace_id_project_id_work_item_id_work_items; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_tasks_workspace_id_project_id_work_item_id_work_items FOREIGN KEY (workspace_id, project_id, work_item_id) REFERENCES public.work_items(workspace_id, project_id, id) ON DELETE CASCADE;

--
-- Name: tasks fk_tasks_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT fk_tasks_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: team_members fk_team_members_workspace_id_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT fk_team_members_workspace_id_member_id_members FOREIGN KEY (workspace_id, member_id) REFERENCES public.members(workspace_id, id) ON DELETE CASCADE;

--
-- Name: team_members fk_team_members_workspace_id_team_id_teams; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT fk_team_members_workspace_id_team_id_teams FOREIGN KEY (workspace_id, team_id) REFERENCES public.teams(workspace_id, id) ON DELETE CASCADE;

--
-- Name: team_members fk_team_members_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.team_members
    ADD CONSTRAINT fk_team_members_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: teams fk_teams_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams
    ADD CONSTRAINT fk_teams_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: transcript_segments fk_transcript_segments_workspace_id_speaker_member_id_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcript_segments
    ADD CONSTRAINT fk_transcript_segments_workspace_id_speaker_member_id_members FOREIGN KEY (workspace_id, speaker_member_id) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (speaker_member_id);

--
-- Name: transcript_segments fk_transcript_segments_workspace_id_transcript_id_transcripts; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcript_segments
    ADD CONSTRAINT fk_transcript_segments_workspace_id_transcript_id_transcripts FOREIGN KEY (workspace_id, transcript_id) REFERENCES public.transcripts(workspace_id, id) ON DELETE CASCADE;

--
-- Name: transcript_segments fk_transcript_segments_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcript_segments
    ADD CONSTRAINT fk_transcript_segments_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: transcripts fk_transcripts_workspace_id_file_id_files; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT fk_transcripts_workspace_id_file_id_files FOREIGN KEY (workspace_id, file_id) REFERENCES public.files(workspace_id, id) ON DELETE CASCADE;

--
-- Name: transcripts fk_transcripts_workspace_id_meeting_id_meetings; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT fk_transcripts_workspace_id_meeting_id_meetings FOREIGN KEY (workspace_id, meeting_id) REFERENCES public.meetings(workspace_id, id) ON DELETE CASCADE;

--
-- Name: transcripts fk_transcripts_workspace_id_message_id_messages; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT fk_transcripts_workspace_id_message_id_messages FOREIGN KEY (workspace_id, message_id) REFERENCES public.messages(workspace_id, id) ON DELETE CASCADE;

--
-- Name: transcripts fk_transcripts_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcripts
    ADD CONSTRAINT fk_transcripts_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: work_item_labels fk_work_item_labels_workspace_id_label_id_labels; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_item_labels
    ADD CONSTRAINT fk_work_item_labels_workspace_id_label_id_labels FOREIGN KEY (workspace_id, label_id) REFERENCES public.labels(workspace_id, id) ON DELETE CASCADE;

--
-- Name: work_item_labels fk_work_item_labels_workspace_id_work_item_id_work_items; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_item_labels
    ADD CONSTRAINT fk_work_item_labels_workspace_id_work_item_id_work_items FOREIGN KEY (workspace_id, work_item_id) REFERENCES public.work_items(workspace_id, id) ON DELETE CASCADE;

--
-- Name: work_item_labels fk_work_item_labels_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_item_labels
    ADD CONSTRAINT fk_work_item_labels_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: work_items fk_work_items_workspace_id_created_by_members; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_items
    ADD CONSTRAINT fk_work_items_workspace_id_created_by_members FOREIGN KEY (workspace_id, created_by) REFERENCES public.members(workspace_id, id) ON DELETE SET NULL (created_by);

--
-- Name: work_items fk_work_items_workspace_id_project_id_backlog_id_backlogs; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_items
    ADD CONSTRAINT fk_work_items_workspace_id_project_id_backlog_id_backlogs FOREIGN KEY (workspace_id, project_id, backlog_id) REFERENCES public.backlogs(workspace_id, project_id, id) ON DELETE CASCADE;

--
-- Name: work_items fk_work_items_workspace_id_workspaces; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_items
    ADD CONSTRAINT fk_work_items_workspace_id_workspaces FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;

--
-- Name: workspaces fk_workspaces_created_by_users; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT fk_workspaces_created_by_users FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;

--
-- Name: agent_reports; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_reports ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_runs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.agent_runs ENABLE ROW LEVEL SECURITY;

--
-- Name: approvals; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.approvals ENABLE ROW LEVEL SECURITY;

--
-- Name: catalog_skills atom_base; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_base ON public.catalog_skills USING (true) WITH CHECK (true);

--
-- Name: users atom_base; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_base ON public.users USING (true) WITH CHECK (true);

--
-- Name: atom_connections; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.atom_connections ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_reports atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.agent_reports AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('agent_reports'::text, to_jsonb(agent_reports.*), 'write'::text));

--
-- Name: agent_runs atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.agent_runs AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('agent_runs'::text, to_jsonb(agent_runs.*), 'write'::text));

--
-- Name: approvals atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.approvals AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('approvals'::text, to_jsonb(approvals.*), 'write'::text));

--
-- Name: atom_connections atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.atom_connections AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('atom_connections'::text, to_jsonb(atom_connections.*), 'write'::text));

--
-- Name: atom_grants atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.atom_grants AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('atom_grants'::text, to_jsonb(atom_grants.*), 'write'::text));

--
-- Name: atom_schedules atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.atom_schedules AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('atom_schedules'::text, to_jsonb(atom_schedules.*), 'write'::text));

--
-- Name: atom_skills atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.atom_skills AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('atom_skills'::text, to_jsonb(atom_skills.*), 'write'::text));

--
-- Name: atom_versions atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.atom_versions AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('atom_versions'::text, to_jsonb(atom_versions.*), 'write'::text));

--
-- Name: atoms atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.atoms AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('atoms'::text, to_jsonb(atoms.*), 'write'::text));

--
-- Name: attachments atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.attachments AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('attachments'::text, to_jsonb(attachments.*), 'write'::text));

--
-- Name: backlogs atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.backlogs AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('backlogs'::text, to_jsonb(backlogs.*), 'write'::text));

--
-- Name: catalog_skills atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.catalog_skills AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('catalog_skills'::text, to_jsonb(catalog_skills.*), 'write'::text));

--
-- Name: channel_members atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.channel_members AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('channel_members'::text, to_jsonb(channel_members.*), 'write'::text));

--
-- Name: channels atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.channels AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('channels'::text, to_jsonb(channels.*), 'write'::text));

--
-- Name: chunks atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.chunks AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('chunks'::text, to_jsonb(chunks.*), 'write'::text));

--
-- Name: collections atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.collections AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('collections'::text, to_jsonb(collections.*), 'write'::text));

--
-- Name: comments atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.comments AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('comments'::text, to_jsonb(comments.*), 'write'::text));

--
-- Name: decision_conflicts atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.decision_conflicts AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('decision_conflicts'::text, to_jsonb(decision_conflicts.*), 'write'::text));

--
-- Name: decisions atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.decisions AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('decisions'::text, to_jsonb(decisions.*), 'write'::text));

--
-- Name: doc_blocks atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.doc_blocks AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('doc_blocks'::text, to_jsonb(doc_blocks.*), 'write'::text));

--
-- Name: doc_versions atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.doc_versions AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('doc_versions'::text, to_jsonb(doc_versions.*), 'write'::text));

--
-- Name: docs atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.docs AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('docs'::text, to_jsonb(docs.*), 'write'::text));

--
-- Name: events atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.events AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('events'::text, to_jsonb(events.*), 'write'::text));

--
-- Name: favorites atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.favorites AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('favorites'::text, to_jsonb(favorites.*), 'write'::text));

--
-- Name: file_derivatives atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.file_derivatives AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('file_derivatives'::text, to_jsonb(file_derivatives.*), 'write'::text));

--
-- Name: files atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.files AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('files'::text, to_jsonb(files.*), 'write'::text));

--
-- Name: folders atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.folders AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('folders'::text, to_jsonb(folders.*), 'write'::text));

--
-- Name: invitations atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.invitations AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('invitations'::text, to_jsonb(invitations.*), 'write'::text));

--
-- Name: labels atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.labels AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('labels'::text, to_jsonb(labels.*), 'write'::text));

--
-- Name: links atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.links AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('links'::text, to_jsonb(links.*), 'write'::text));

--
-- Name: llm_usage atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.llm_usage AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('llm_usage'::text, to_jsonb(llm_usage.*), 'write'::text));

--
-- Name: meeting_participants atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.meeting_participants AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('meeting_participants'::text, to_jsonb(meeting_participants.*), 'write'::text));

--
-- Name: meetings atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.meetings AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('meetings'::text, to_jsonb(meetings.*), 'write'::text));

--
-- Name: members atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.members AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('members'::text, to_jsonb(members.*), 'write'::text));

--
-- Name: memories atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.memories AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('memories'::text, to_jsonb(memories.*), 'write'::text));

--
-- Name: memory_links atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.memory_links AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('memory_links'::text, to_jsonb(memory_links.*), 'write'::text));

--
-- Name: messages atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.messages AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('messages'::text, to_jsonb(messages.*), 'write'::text));

--
-- Name: milestones atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.milestones AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('milestones'::text, to_jsonb(milestones.*), 'write'::text));

--
-- Name: notifications atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.notifications AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('notifications'::text, to_jsonb(notifications.*), 'write'::text));

--
-- Name: project_updates atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.project_updates AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('project_updates'::text, to_jsonb(project_updates.*), 'write'::text));

--
-- Name: projects atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.projects AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('projects'::text, to_jsonb(projects.*), 'write'::text));

--
-- Name: reactions atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.reactions AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('reactions'::text, to_jsonb(reactions.*), 'write'::text));

--
-- Name: recordings atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.recordings AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('recordings'::text, to_jsonb(recordings.*), 'write'::text));

--
-- Name: saved_views atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.saved_views AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('saved_views'::text, to_jsonb(saved_views.*), 'write'::text));

--
-- Name: skills atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.skills AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('skills'::text, to_jsonb(skills.*), 'write'::text));

--
-- Name: sprints atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.sprints AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('sprints'::text, to_jsonb(sprints.*), 'write'::text));

--
-- Name: subscriptions atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.subscriptions AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('subscriptions'::text, to_jsonb(subscriptions.*), 'write'::text));

--
-- Name: task_labels atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.task_labels AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('task_labels'::text, to_jsonb(task_labels.*), 'write'::text));

--
-- Name: task_relations atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.task_relations AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('task_relations'::text, to_jsonb(task_relations.*), 'write'::text));

--
-- Name: tasks atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.tasks AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('tasks'::text, to_jsonb(tasks.*), 'write'::text));

--
-- Name: team_members atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.team_members AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('team_members'::text, to_jsonb(team_members.*), 'write'::text));

--
-- Name: teams atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.teams AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('teams'::text, to_jsonb(teams.*), 'write'::text));

--
-- Name: transcript_segments atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.transcript_segments AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('transcript_segments'::text, to_jsonb(transcript_segments.*), 'write'::text));

--
-- Name: transcripts atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.transcripts AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('transcripts'::text, to_jsonb(transcripts.*), 'write'::text));

--
-- Name: users atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.users AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('users'::text, to_jsonb(users.*), 'write'::text));

--
-- Name: work_item_labels atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.work_item_labels AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('work_item_labels'::text, to_jsonb(work_item_labels.*), 'write'::text));

--
-- Name: work_items atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.work_items AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('work_items'::text, to_jsonb(work_items.*), 'write'::text));

--
-- Name: workspaces atom_delete; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_delete ON public.workspaces AS RESTRICTIVE FOR DELETE USING (public.atom_row_allowed('workspaces'::text, to_jsonb(workspaces.*), 'write'::text));

--
-- Name: atom_grants; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.atom_grants ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_reports atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.agent_reports AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('agent_reports'::text, to_jsonb(agent_reports.*), 'write'::text));

--
-- Name: agent_runs atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.agent_runs AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('agent_runs'::text, to_jsonb(agent_runs.*), 'write'::text));

--
-- Name: approvals atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.approvals AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('approvals'::text, to_jsonb(approvals.*), 'write'::text));

--
-- Name: atom_connections atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.atom_connections AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('atom_connections'::text, to_jsonb(atom_connections.*), 'write'::text));

--
-- Name: atom_grants atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.atom_grants AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('atom_grants'::text, to_jsonb(atom_grants.*), 'write'::text));

--
-- Name: atom_schedules atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.atom_schedules AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('atom_schedules'::text, to_jsonb(atom_schedules.*), 'write'::text));

--
-- Name: atom_skills atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.atom_skills AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('atom_skills'::text, to_jsonb(atom_skills.*), 'write'::text));

--
-- Name: atom_versions atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.atom_versions AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('atom_versions'::text, to_jsonb(atom_versions.*), 'write'::text));

--
-- Name: atoms atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.atoms AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('atoms'::text, to_jsonb(atoms.*), 'write'::text));

--
-- Name: attachments atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.attachments AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('attachments'::text, to_jsonb(attachments.*), 'write'::text));

--
-- Name: backlogs atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.backlogs AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('backlogs'::text, to_jsonb(backlogs.*), 'write'::text));

--
-- Name: catalog_skills atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.catalog_skills AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('catalog_skills'::text, to_jsonb(catalog_skills.*), 'write'::text));

--
-- Name: channel_members atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.channel_members AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('channel_members'::text, to_jsonb(channel_members.*), 'write'::text));

--
-- Name: channels atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.channels AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('channels'::text, to_jsonb(channels.*), 'write'::text));

--
-- Name: chunks atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.chunks AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('chunks'::text, to_jsonb(chunks.*), 'write'::text));

--
-- Name: collections atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.collections AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('collections'::text, to_jsonb(collections.*), 'write'::text));

--
-- Name: comments atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.comments AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('comments'::text, to_jsonb(comments.*), 'write'::text));

--
-- Name: decision_conflicts atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.decision_conflicts AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('decision_conflicts'::text, to_jsonb(decision_conflicts.*), 'write'::text));

--
-- Name: decisions atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.decisions AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('decisions'::text, to_jsonb(decisions.*), 'write'::text));

--
-- Name: doc_blocks atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.doc_blocks AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('doc_blocks'::text, to_jsonb(doc_blocks.*), 'write'::text));

--
-- Name: doc_versions atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.doc_versions AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('doc_versions'::text, to_jsonb(doc_versions.*), 'write'::text));

--
-- Name: docs atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.docs AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('docs'::text, to_jsonb(docs.*), 'write'::text));

--
-- Name: events atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.events AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('events'::text, to_jsonb(events.*), 'write'::text));

--
-- Name: favorites atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.favorites AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('favorites'::text, to_jsonb(favorites.*), 'write'::text));

--
-- Name: file_derivatives atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.file_derivatives AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('file_derivatives'::text, to_jsonb(file_derivatives.*), 'write'::text));

--
-- Name: files atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.files AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('files'::text, to_jsonb(files.*), 'write'::text));

--
-- Name: folders atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.folders AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('folders'::text, to_jsonb(folders.*), 'write'::text));

--
-- Name: invitations atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.invitations AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('invitations'::text, to_jsonb(invitations.*), 'write'::text));

--
-- Name: labels atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.labels AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('labels'::text, to_jsonb(labels.*), 'write'::text));

--
-- Name: links atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.links AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('links'::text, to_jsonb(links.*), 'write'::text));

--
-- Name: llm_usage atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.llm_usage AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('llm_usage'::text, to_jsonb(llm_usage.*), 'write'::text));

--
-- Name: meeting_participants atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.meeting_participants AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('meeting_participants'::text, to_jsonb(meeting_participants.*), 'write'::text));

--
-- Name: meetings atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.meetings AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('meetings'::text, to_jsonb(meetings.*), 'write'::text));

--
-- Name: members atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.members AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('members'::text, to_jsonb(members.*), 'write'::text));

--
-- Name: memories atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.memories AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('memories'::text, to_jsonb(memories.*), 'write'::text));

--
-- Name: memory_links atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.memory_links AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('memory_links'::text, to_jsonb(memory_links.*), 'write'::text));

--
-- Name: messages atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.messages AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('messages'::text, to_jsonb(messages.*), 'write'::text));

--
-- Name: milestones atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.milestones AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('milestones'::text, to_jsonb(milestones.*), 'write'::text));

--
-- Name: notifications atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.notifications AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('notifications'::text, to_jsonb(notifications.*), 'write'::text));

--
-- Name: project_updates atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.project_updates AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('project_updates'::text, to_jsonb(project_updates.*), 'write'::text));

--
-- Name: projects atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.projects AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('projects'::text, to_jsonb(projects.*), 'write'::text));

--
-- Name: reactions atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.reactions AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('reactions'::text, to_jsonb(reactions.*), 'write'::text));

--
-- Name: recordings atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.recordings AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('recordings'::text, to_jsonb(recordings.*), 'write'::text));

--
-- Name: saved_views atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.saved_views AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('saved_views'::text, to_jsonb(saved_views.*), 'write'::text));

--
-- Name: skills atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.skills AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('skills'::text, to_jsonb(skills.*), 'write'::text));

--
-- Name: sprints atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.sprints AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('sprints'::text, to_jsonb(sprints.*), 'write'::text));

--
-- Name: subscriptions atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.subscriptions AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('subscriptions'::text, to_jsonb(subscriptions.*), 'write'::text));

--
-- Name: task_labels atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.task_labels AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('task_labels'::text, to_jsonb(task_labels.*), 'write'::text));

--
-- Name: task_relations atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.task_relations AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('task_relations'::text, to_jsonb(task_relations.*), 'write'::text));

--
-- Name: tasks atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.tasks AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('tasks'::text, to_jsonb(tasks.*), 'write'::text));

--
-- Name: team_members atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.team_members AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('team_members'::text, to_jsonb(team_members.*), 'write'::text));

--
-- Name: teams atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.teams AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('teams'::text, to_jsonb(teams.*), 'write'::text));

--
-- Name: transcript_segments atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.transcript_segments AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('transcript_segments'::text, to_jsonb(transcript_segments.*), 'write'::text));

--
-- Name: transcripts atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.transcripts AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('transcripts'::text, to_jsonb(transcripts.*), 'write'::text));

--
-- Name: users atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.users AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('users'::text, to_jsonb(users.*), 'write'::text));

--
-- Name: work_item_labels atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.work_item_labels AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('work_item_labels'::text, to_jsonb(work_item_labels.*), 'write'::text));

--
-- Name: work_items atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.work_items AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('work_items'::text, to_jsonb(work_items.*), 'write'::text));

--
-- Name: workspaces atom_insert; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_insert ON public.workspaces AS RESTRICTIVE FOR INSERT WITH CHECK (public.atom_row_allowed('workspaces'::text, to_jsonb(workspaces.*), 'write'::text));

--
-- Name: atom_schedules; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.atom_schedules ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_reports atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.agent_reports AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('agent_reports'::text, to_jsonb(agent_reports.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: agent_runs atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.agent_runs AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('agent_runs'::text, to_jsonb(agent_runs.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: approvals atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.approvals AS RESTRICTIVE FOR SELECT USING (public.atom_approval_visible(to_jsonb(approvals.*)));

--
-- Name: atom_connections atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.atom_connections AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('atom_connections'::text, to_jsonb(atom_connections.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: atom_grants atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.atom_grants AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('atom_grants'::text, to_jsonb(atom_grants.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: atom_schedules atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.atom_schedules AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('atom_schedules'::text, to_jsonb(atom_schedules.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: atom_skills atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.atom_skills AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('atom_skills'::text, to_jsonb(atom_skills.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: atom_versions atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.atom_versions AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('atom_versions'::text, to_jsonb(atom_versions.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: atoms atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.atoms AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('atoms'::text, to_jsonb(atoms.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: attachments atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.attachments AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('attachments'::text, to_jsonb(attachments.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: backlogs atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.backlogs AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('backlogs'::text, to_jsonb(backlogs.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: catalog_skills atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.catalog_skills AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('catalog_skills'::text, to_jsonb(catalog_skills.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: channel_members atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.channel_members AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('channel_members'::text, to_jsonb(channel_members.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: channels atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.channels AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('channels'::text, to_jsonb(channels.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: chunks atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.chunks AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('chunks'::text, to_jsonb(chunks.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: collections atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.collections AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('collections'::text, to_jsonb(collections.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: comments atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.comments AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('comments'::text, to_jsonb(comments.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: decision_conflicts atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.decision_conflicts AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('decision_conflicts'::text, to_jsonb(decision_conflicts.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: decisions atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.decisions AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('decisions'::text, to_jsonb(decisions.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: doc_blocks atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.doc_blocks AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('doc_blocks'::text, to_jsonb(doc_blocks.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: doc_versions atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.doc_versions AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('doc_versions'::text, to_jsonb(doc_versions.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: docs atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.docs AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('docs'::text, to_jsonb(docs.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: events atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.events AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('events'::text, to_jsonb(events.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: favorites atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.favorites AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('favorites'::text, to_jsonb(favorites.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: file_derivatives atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.file_derivatives AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('file_derivatives'::text, to_jsonb(file_derivatives.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: files atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.files AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('files'::text, to_jsonb(files.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: folders atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.folders AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('folders'::text, to_jsonb(folders.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: invitations atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.invitations AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('invitations'::text, to_jsonb(invitations.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: labels atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.labels AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('labels'::text, to_jsonb(labels.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: links atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.links AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('links'::text, to_jsonb(links.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: llm_usage atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.llm_usage AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('llm_usage'::text, to_jsonb(llm_usage.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: meeting_participants atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.meeting_participants AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('meeting_participants'::text, to_jsonb(meeting_participants.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: meetings atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.meetings AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('meetings'::text, to_jsonb(meetings.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: members atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.members AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('members'::text, to_jsonb(members.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: memories atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.memories AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('memories'::text, to_jsonb(memories.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: memory_links atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.memory_links AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('memory_links'::text, to_jsonb(memory_links.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: messages atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.messages AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('messages'::text, to_jsonb(messages.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: milestones atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.milestones AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('milestones'::text, to_jsonb(milestones.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: notifications atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.notifications AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('notifications'::text, to_jsonb(notifications.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: project_updates atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.project_updates AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('project_updates'::text, to_jsonb(project_updates.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: projects atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.projects AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('projects'::text, to_jsonb(projects.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: reactions atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.reactions AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('reactions'::text, to_jsonb(reactions.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: recordings atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.recordings AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('recordings'::text, to_jsonb(recordings.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: saved_views atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.saved_views AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('saved_views'::text, to_jsonb(saved_views.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: skills atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.skills AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('skills'::text, to_jsonb(skills.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: sprints atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.sprints AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('sprints'::text, to_jsonb(sprints.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: subscriptions atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.subscriptions AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('subscriptions'::text, to_jsonb(subscriptions.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: task_labels atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.task_labels AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('task_labels'::text, to_jsonb(task_labels.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: task_relations atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.task_relations AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('task_relations'::text, to_jsonb(task_relations.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: tasks atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.tasks AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('tasks'::text, to_jsonb(tasks.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: team_members atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.team_members AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('team_members'::text, to_jsonb(team_members.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: teams atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.teams AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('teams'::text, to_jsonb(teams.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: transcript_segments atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.transcript_segments AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('transcript_segments'::text, to_jsonb(transcript_segments.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: transcripts atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.transcripts AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('transcripts'::text, to_jsonb(transcripts.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: users atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.users AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('users'::text, to_jsonb(users.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: work_item_labels atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.work_item_labels AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('work_item_labels'::text, to_jsonb(work_item_labels.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: work_items atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.work_items AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('work_items'::text, to_jsonb(work_items.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: workspaces atom_select; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_select ON public.workspaces AS RESTRICTIVE FOR SELECT USING (public.atom_row_allowed('workspaces'::text, to_jsonb(workspaces.*),
CASE
    WHEN (current_setting('app.atom_aggregate'::text, true) = 'on'::text) THEN 'summary'::text
    ELSE 'read'::text
END));

--
-- Name: atom_skills; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.atom_skills ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_reports atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.agent_reports AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('agent_reports'::text, to_jsonb(agent_reports.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('agent_reports'::text, to_jsonb(agent_reports.*), 'write'::text));

--
-- Name: agent_runs atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.agent_runs AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('agent_runs'::text, to_jsonb(agent_runs.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('agent_runs'::text, to_jsonb(agent_runs.*), 'write'::text));

--
-- Name: approvals atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.approvals AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('approvals'::text, to_jsonb(approvals.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('approvals'::text, to_jsonb(approvals.*), 'write'::text));

--
-- Name: atom_connections atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.atom_connections AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('atom_connections'::text, to_jsonb(atom_connections.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('atom_connections'::text, to_jsonb(atom_connections.*), 'write'::text));

--
-- Name: atom_grants atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.atom_grants AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('atom_grants'::text, to_jsonb(atom_grants.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('atom_grants'::text, to_jsonb(atom_grants.*), 'write'::text));

--
-- Name: atom_schedules atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.atom_schedules AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('atom_schedules'::text, to_jsonb(atom_schedules.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('atom_schedules'::text, to_jsonb(atom_schedules.*), 'write'::text));

--
-- Name: atom_skills atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.atom_skills AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('atom_skills'::text, to_jsonb(atom_skills.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('atom_skills'::text, to_jsonb(atom_skills.*), 'write'::text));

--
-- Name: atom_versions atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.atom_versions AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('atom_versions'::text, to_jsonb(atom_versions.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('atom_versions'::text, to_jsonb(atom_versions.*), 'write'::text));

--
-- Name: atoms atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.atoms AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('atoms'::text, to_jsonb(atoms.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('atoms'::text, to_jsonb(atoms.*), 'write'::text));

--
-- Name: attachments atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.attachments AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('attachments'::text, to_jsonb(attachments.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('attachments'::text, to_jsonb(attachments.*), 'write'::text));

--
-- Name: backlogs atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.backlogs AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('backlogs'::text, to_jsonb(backlogs.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('backlogs'::text, to_jsonb(backlogs.*), 'write'::text));

--
-- Name: catalog_skills atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.catalog_skills AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('catalog_skills'::text, to_jsonb(catalog_skills.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('catalog_skills'::text, to_jsonb(catalog_skills.*), 'write'::text));

--
-- Name: channel_members atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.channel_members AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('channel_members'::text, to_jsonb(channel_members.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('channel_members'::text, to_jsonb(channel_members.*), 'write'::text));

--
-- Name: channels atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.channels AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('channels'::text, to_jsonb(channels.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('channels'::text, to_jsonb(channels.*), 'write'::text));

--
-- Name: chunks atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.chunks AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('chunks'::text, to_jsonb(chunks.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('chunks'::text, to_jsonb(chunks.*), 'write'::text));

--
-- Name: collections atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.collections AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('collections'::text, to_jsonb(collections.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('collections'::text, to_jsonb(collections.*), 'write'::text));

--
-- Name: comments atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.comments AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('comments'::text, to_jsonb(comments.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('comments'::text, to_jsonb(comments.*), 'write'::text));

--
-- Name: decision_conflicts atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.decision_conflicts AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('decision_conflicts'::text, to_jsonb(decision_conflicts.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('decision_conflicts'::text, to_jsonb(decision_conflicts.*), 'write'::text));

--
-- Name: decisions atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.decisions AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('decisions'::text, to_jsonb(decisions.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('decisions'::text, to_jsonb(decisions.*), 'write'::text));

--
-- Name: doc_blocks atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.doc_blocks AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('doc_blocks'::text, to_jsonb(doc_blocks.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('doc_blocks'::text, to_jsonb(doc_blocks.*), 'write'::text));

--
-- Name: doc_versions atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.doc_versions AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('doc_versions'::text, to_jsonb(doc_versions.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('doc_versions'::text, to_jsonb(doc_versions.*), 'write'::text));

--
-- Name: docs atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.docs AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('docs'::text, to_jsonb(docs.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('docs'::text, to_jsonb(docs.*), 'write'::text));

--
-- Name: events atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.events AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('events'::text, to_jsonb(events.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('events'::text, to_jsonb(events.*), 'write'::text));

--
-- Name: favorites atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.favorites AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('favorites'::text, to_jsonb(favorites.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('favorites'::text, to_jsonb(favorites.*), 'write'::text));

--
-- Name: file_derivatives atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.file_derivatives AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('file_derivatives'::text, to_jsonb(file_derivatives.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('file_derivatives'::text, to_jsonb(file_derivatives.*), 'write'::text));

--
-- Name: files atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.files AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('files'::text, to_jsonb(files.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('files'::text, to_jsonb(files.*), 'write'::text));

--
-- Name: folders atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.folders AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('folders'::text, to_jsonb(folders.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('folders'::text, to_jsonb(folders.*), 'write'::text));

--
-- Name: invitations atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.invitations AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('invitations'::text, to_jsonb(invitations.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('invitations'::text, to_jsonb(invitations.*), 'write'::text));

--
-- Name: labels atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.labels AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('labels'::text, to_jsonb(labels.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('labels'::text, to_jsonb(labels.*), 'write'::text));

--
-- Name: links atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.links AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('links'::text, to_jsonb(links.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('links'::text, to_jsonb(links.*), 'write'::text));

--
-- Name: llm_usage atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.llm_usage AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('llm_usage'::text, to_jsonb(llm_usage.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('llm_usage'::text, to_jsonb(llm_usage.*), 'write'::text));

--
-- Name: meeting_participants atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.meeting_participants AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('meeting_participants'::text, to_jsonb(meeting_participants.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('meeting_participants'::text, to_jsonb(meeting_participants.*), 'write'::text));

--
-- Name: meetings atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.meetings AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('meetings'::text, to_jsonb(meetings.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('meetings'::text, to_jsonb(meetings.*), 'write'::text));

--
-- Name: members atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.members AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('members'::text, to_jsonb(members.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('members'::text, to_jsonb(members.*), 'write'::text));

--
-- Name: memories atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.memories AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('memories'::text, to_jsonb(memories.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('memories'::text, to_jsonb(memories.*), 'write'::text));

--
-- Name: memory_links atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.memory_links AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('memory_links'::text, to_jsonb(memory_links.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('memory_links'::text, to_jsonb(memory_links.*), 'write'::text));

--
-- Name: messages atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.messages AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('messages'::text, to_jsonb(messages.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('messages'::text, to_jsonb(messages.*), 'write'::text));

--
-- Name: milestones atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.milestones AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('milestones'::text, to_jsonb(milestones.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('milestones'::text, to_jsonb(milestones.*), 'write'::text));

--
-- Name: notifications atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.notifications AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('notifications'::text, to_jsonb(notifications.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('notifications'::text, to_jsonb(notifications.*), 'write'::text));

--
-- Name: project_updates atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.project_updates AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('project_updates'::text, to_jsonb(project_updates.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('project_updates'::text, to_jsonb(project_updates.*), 'write'::text));

--
-- Name: projects atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.projects AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('projects'::text, to_jsonb(projects.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('projects'::text, to_jsonb(projects.*), 'write'::text));

--
-- Name: reactions atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.reactions AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('reactions'::text, to_jsonb(reactions.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('reactions'::text, to_jsonb(reactions.*), 'write'::text));

--
-- Name: recordings atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.recordings AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('recordings'::text, to_jsonb(recordings.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('recordings'::text, to_jsonb(recordings.*), 'write'::text));

--
-- Name: saved_views atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.saved_views AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('saved_views'::text, to_jsonb(saved_views.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('saved_views'::text, to_jsonb(saved_views.*), 'write'::text));

--
-- Name: skills atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.skills AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('skills'::text, to_jsonb(skills.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('skills'::text, to_jsonb(skills.*), 'write'::text));

--
-- Name: sprints atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.sprints AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('sprints'::text, to_jsonb(sprints.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('sprints'::text, to_jsonb(sprints.*), 'write'::text));

--
-- Name: subscriptions atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.subscriptions AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('subscriptions'::text, to_jsonb(subscriptions.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('subscriptions'::text, to_jsonb(subscriptions.*), 'write'::text));

--
-- Name: task_labels atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.task_labels AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('task_labels'::text, to_jsonb(task_labels.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('task_labels'::text, to_jsonb(task_labels.*), 'write'::text));

--
-- Name: task_relations atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.task_relations AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('task_relations'::text, to_jsonb(task_relations.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('task_relations'::text, to_jsonb(task_relations.*), 'write'::text));

--
-- Name: tasks atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.tasks AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('tasks'::text, to_jsonb(tasks.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('tasks'::text, to_jsonb(tasks.*), 'write'::text));

--
-- Name: team_members atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.team_members AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('team_members'::text, to_jsonb(team_members.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('team_members'::text, to_jsonb(team_members.*), 'write'::text));

--
-- Name: teams atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.teams AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('teams'::text, to_jsonb(teams.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('teams'::text, to_jsonb(teams.*), 'write'::text));

--
-- Name: transcript_segments atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.transcript_segments AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('transcript_segments'::text, to_jsonb(transcript_segments.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('transcript_segments'::text, to_jsonb(transcript_segments.*), 'write'::text));

--
-- Name: transcripts atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.transcripts AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('transcripts'::text, to_jsonb(transcripts.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('transcripts'::text, to_jsonb(transcripts.*), 'write'::text));

--
-- Name: users atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.users AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('users'::text, to_jsonb(users.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('users'::text, to_jsonb(users.*), 'write'::text));

--
-- Name: work_item_labels atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.work_item_labels AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('work_item_labels'::text, to_jsonb(work_item_labels.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('work_item_labels'::text, to_jsonb(work_item_labels.*), 'write'::text));

--
-- Name: work_items atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.work_items AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('work_items'::text, to_jsonb(work_items.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('work_items'::text, to_jsonb(work_items.*), 'write'::text));

--
-- Name: workspaces atom_update; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY atom_update ON public.workspaces AS RESTRICTIVE FOR UPDATE USING (public.atom_row_allowed('workspaces'::text, to_jsonb(workspaces.*), 'write'::text)) WITH CHECK (public.atom_row_allowed('workspaces'::text, to_jsonb(workspaces.*), 'write'::text));

--
-- Name: atom_versions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.atom_versions ENABLE ROW LEVEL SECURITY;

--
-- Name: atoms; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.atoms ENABLE ROW LEVEL SECURITY;

--
-- Name: attachments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.attachments ENABLE ROW LEVEL SECURITY;

--
-- Name: backlogs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.backlogs ENABLE ROW LEVEL SECURITY;

--
-- Name: catalog_skills; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.catalog_skills ENABLE ROW LEVEL SECURITY;

--
-- Name: channel_members; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.channel_members ENABLE ROW LEVEL SECURITY;

--
-- Name: channels; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.channels ENABLE ROW LEVEL SECURITY;

--
-- Name: chunks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.chunks ENABLE ROW LEVEL SECURITY;

--
-- Name: collections; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.collections ENABLE ROW LEVEL SECURITY;

--
-- Name: comments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.comments ENABLE ROW LEVEL SECURITY;

--
-- Name: decision_conflicts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.decision_conflicts ENABLE ROW LEVEL SECURITY;

--
-- Name: decisions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.decisions ENABLE ROW LEVEL SECURITY;

--
-- Name: doc_blocks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.doc_blocks ENABLE ROW LEVEL SECURITY;

--
-- Name: doc_versions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.doc_versions ENABLE ROW LEVEL SECURITY;

--
-- Name: docs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.docs ENABLE ROW LEVEL SECURITY;

--
-- Name: events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;

--
-- Name: favorites; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.favorites ENABLE ROW LEVEL SECURITY;

--
-- Name: file_derivatives; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.file_derivatives ENABLE ROW LEVEL SECURITY;

--
-- Name: files; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.files ENABLE ROW LEVEL SECURITY;

--
-- Name: folders; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.folders ENABLE ROW LEVEL SECURITY;

--
-- Name: invitations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.invitations ENABLE ROW LEVEL SECURITY;

--
-- Name: labels; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.labels ENABLE ROW LEVEL SECURITY;

--
-- Name: links; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.links ENABLE ROW LEVEL SECURITY;

--
-- Name: llm_usage; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.llm_usage ENABLE ROW LEVEL SECURITY;

--
-- Name: meeting_participants; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.meeting_participants ENABLE ROW LEVEL SECURITY;

--
-- Name: meetings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.meetings ENABLE ROW LEVEL SECURITY;

--
-- Name: members; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.members ENABLE ROW LEVEL SECURITY;

--
-- Name: memories; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.memories ENABLE ROW LEVEL SECURITY;

--
-- Name: memory_links; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.memory_links ENABLE ROW LEVEL SECURITY;

--
-- Name: messages; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;

--
-- Name: milestones; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.milestones ENABLE ROW LEVEL SECURITY;

--
-- Name: notifications; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;

--
-- Name: project_updates; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.project_updates ENABLE ROW LEVEL SECURITY;

--
-- Name: projects; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.projects ENABLE ROW LEVEL SECURITY;

--
-- Name: reactions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reactions ENABLE ROW LEVEL SECURITY;

--
-- Name: recordings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.recordings ENABLE ROW LEVEL SECURITY;

--
-- Name: saved_views; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.saved_views ENABLE ROW LEVEL SECURITY;

--
-- Name: skills; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.skills ENABLE ROW LEVEL SECURITY;

--
-- Name: sprints; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.sprints ENABLE ROW LEVEL SECURITY;

--
-- Name: subscriptions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;

--
-- Name: task_labels; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.task_labels ENABLE ROW LEVEL SECURITY;

--
-- Name: task_relations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.task_relations ENABLE ROW LEVEL SECURITY;

--
-- Name: tasks; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;

--
-- Name: team_members; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.team_members ENABLE ROW LEVEL SECURITY;

--
-- Name: teams; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_reports tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.agent_reports USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: agent_runs tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.agent_runs USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: approvals tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.approvals USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: atom_connections tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.atom_connections USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: atom_grants tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.atom_grants USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: atom_schedules tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.atom_schedules USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: atom_skills tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.atom_skills USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: atom_versions tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.atom_versions USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: atoms tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.atoms USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: attachments tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.attachments USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: backlogs tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.backlogs USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: channel_members tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.channel_members USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: channels tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.channels USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: chunks tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.chunks USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: collections tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.collections USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: comments tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.comments USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: decision_conflicts tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.decision_conflicts USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: decisions tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.decisions USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: doc_blocks tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.doc_blocks USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: doc_versions tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.doc_versions USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: docs tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.docs USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: events tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.events USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: favorites tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.favorites USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: file_derivatives tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.file_derivatives USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: files tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.files USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: folders tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.folders USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: invitations tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.invitations USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: labels tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.labels USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: links tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.links USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: llm_usage tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.llm_usage USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: meeting_participants tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.meeting_participants USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: meetings tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.meetings USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: members tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.members USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: memories tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.memories USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: memory_links tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.memory_links USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: messages tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.messages USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: milestones tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.milestones USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: notifications tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.notifications USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: project_updates tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.project_updates USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: projects tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.projects USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: reactions tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.reactions USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: recordings tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.recordings USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: saved_views tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.saved_views USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: skills tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.skills USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: sprints tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.sprints USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: subscriptions tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.subscriptions USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: task_labels tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.task_labels USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: task_relations tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.task_relations USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: tasks tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.tasks USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: team_members tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.team_members USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: teams tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.teams USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: transcript_segments tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.transcript_segments USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: transcripts tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.transcripts USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: work_item_labels tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.work_item_labels USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: work_items tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.work_items USING (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (workspace_id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: workspaces tenant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY tenant_isolation ON public.workspaces USING (((current_setting('app.service'::text, true) = 'on'::text) OR (id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid))) WITH CHECK (((current_setting('app.service'::text, true) = 'on'::text) OR (id = (NULLIF(current_setting('app.workspace_id'::text, true), ''::text))::uuid)));

--
-- Name: transcript_segments; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.transcript_segments ENABLE ROW LEVEL SECURITY;

--
-- Name: transcripts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.transcripts ENABLE ROW LEVEL SECURITY;

--
-- Name: users; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

--
-- Name: work_item_labels; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.work_item_labels ENABLE ROW LEVEL SECURITY;

--
-- Name: work_items; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.work_items ENABLE ROW LEVEL SECURITY;

--
-- Name: workspaces; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.workspaces ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--
