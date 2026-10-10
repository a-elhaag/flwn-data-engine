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
