"""initial_domain_schema

Revision ID: ba7a2c34aa18
Revises: 
Create Date: 2026-10-17 18:29:55.076373

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'ba7a2c34aa18'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ENUMS = {
    "member_type": ("HUMAN", "AI"),
    "member_role": ("OWNER", "MEMBER"),
    "member_status": ("ACTIVE", "INACTIVE"),
    "team_member_role": ("LEAD", "MEMBER"),
    "project_status": ("ACTIVE", "COMPLETED"),
    "work_item_type": ("FEATURE", "STORY", "BUG"),
    "priority": ("LOW", "MEDIUM", "HIGH", "URGENT"),
    "work_item_status": ("OPEN", "IN_PROGRESS", "DONE", "CANCELLED"),
    "sprint_status": ("PLANNED", "ACTIVE", "COMPLETED", "CANCELLED"),
    "task_status": ("TODO", "IN_PROGRESS", "REVIEW", "DONE", "BLOCKED"),
}


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind)
    op.create_table('users',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_users_name_not_blank')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index('uq_users_email', 'users', [sa.literal_column('lower(email)')], unique=True)
    op.create_table('organizations',
    sa.Column('created_by_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=5000), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_organizations_name_not_blank')),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_organizations_created_by_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations'))
    )
    op.create_table('members',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('workspace_id', sa.UUID(), nullable=True),
    sa.Column('created_by_id', sa.UUID(), nullable=True),
    sa.Column('type', postgresql.ENUM('HUMAN', 'AI', name='member_type', create_type=False), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=True),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('role', postgresql.ENUM('OWNER', 'MEMBER', name='member_role', create_type=False), server_default='MEMBER', nullable=False),
    sa.Column('status', postgresql.ENUM('ACTIVE', 'INACTIVE', name='member_status', create_type=False), server_default='ACTIVE', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(type = 'HUMAN' AND user_id IS NOT NULL AND name IS NULL AND email IS NULL) OR (type = 'AI' AND user_id IS NULL AND name IS NOT NULL)", name=op.f('ck_members_human_or_ai')),
    sa.CheckConstraint('name IS NULL OR char_length(btrim(name)) > 0', name=op.f('ck_members_name_not_blank')),
    sa.ForeignKeyConstraint(['created_by_id', 'organization_id'], ['members.id', 'members.organization_id'], name=op.f('fk_members_created_by_id_organization_id_members')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_members_organization_id_organizations')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_members_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_members')),
    sa.UniqueConstraint('id', 'organization_id', name=op.f('uq_members_id_organization_id')),
    sa.UniqueConstraint('organization_id', 'user_id', name=op.f('uq_members_organization_id_user_id'))
    )
    op.create_index('uq_members_organization_id_email_ai', 'members', ['organization_id', sa.literal_column('lower(email)')], unique=True, postgresql_where=sa.text("type = 'AI' AND email IS NOT NULL"))
    op.create_index('uq_members_organization_id_name_ai', 'members', ['organization_id', sa.literal_column('lower(name)')], unique=True, postgresql_where=sa.text("type = 'AI'"))
    op.create_table('workspaces',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('created_by_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=5000), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_workspaces_name_not_blank')),
    sa.ForeignKeyConstraint(['created_by_id', 'organization_id'], ['members.id', 'members.organization_id'], name=op.f('fk_workspaces_created_by_id_organization_id_members')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_workspaces_organization_id_organizations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workspaces')),
    sa.UniqueConstraint('id', 'organization_id', name=op.f('uq_workspaces_id_organization_id'))
    )
    # members <-> workspaces cycle: this FK is added once both tables exist.
    op.create_foreign_key(
        op.f('fk_members_workspace_id_organization_id_workspaces'),
        'members',
        'workspaces',
        ['workspace_id', 'organization_id'],
        ['id', 'organization_id'],
    )
    op.create_index('uq_workspaces_organization_id_name_active', 'workspaces', ['organization_id', sa.literal_column('lower(name)')], unique=True, postgresql_where=sa.text('archived_at IS NULL'))
    op.create_table('projects',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('created_by_id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=5000), nullable=True),
    sa.Column('status', postgresql.ENUM('ACTIVE', 'COMPLETED', name='project_status', create_type=False), server_default='ACTIVE', nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('task_counter', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("key ~ '^[A-Z]{2,10}$'", name=op.f('ck_projects_key_format')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_projects_name_not_blank')),
    sa.CheckConstraint('task_counter >= 0', name=op.f('ck_projects_task_counter_nonneg')),
    sa.ForeignKeyConstraint(['created_by_id', 'organization_id'], ['members.id', 'members.organization_id'], name=op.f('fk_projects_created_by_id_organization_id_members')),
    sa.ForeignKeyConstraint(['workspace_id', 'organization_id'], ['workspaces.id', 'workspaces.organization_id'], name=op.f('fk_projects_workspace_id_organization_id_workspaces')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_projects')),
    sa.UniqueConstraint('id', 'organization_id', name=op.f('uq_projects_id_organization_id')),
    sa.UniqueConstraint('organization_id', 'key', name=op.f('uq_projects_organization_id_key'))
    )
    op.create_index('uq_projects_workspace_id_name_active', 'projects', ['workspace_id', sa.literal_column('lower(name)')], unique=True, postgresql_where=sa.text('archived_at IS NULL'))
    op.create_table('teams',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=False),
    sa.Column('created_by_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=5000), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_teams_name_not_blank')),
    sa.ForeignKeyConstraint(['created_by_id', 'organization_id'], ['members.id', 'members.organization_id'], name=op.f('fk_teams_created_by_id_organization_id_members')),
    sa.ForeignKeyConstraint(['workspace_id', 'organization_id'], ['workspaces.id', 'workspaces.organization_id'], name=op.f('fk_teams_workspace_id_organization_id_workspaces')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teams')),
    sa.UniqueConstraint('id', 'organization_id', name=op.f('uq_teams_id_organization_id'))
    )
    op.create_index('uq_teams_workspace_id_name_active', 'teams', ['workspace_id', sa.literal_column('lower(name)')], unique=True, postgresql_where=sa.text('archived_at IS NULL'))
    op.create_table('project_teams',
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('team_id', sa.UUID(), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['project_id', 'organization_id'], ['projects.id', 'projects.organization_id'], name=op.f('fk_project_teams_project_id_organization_id_projects')),
    sa.ForeignKeyConstraint(['team_id', 'organization_id'], ['teams.id', 'teams.organization_id'], name=op.f('fk_project_teams_team_id_organization_id_teams')),
    sa.PrimaryKeyConstraint('project_id', 'team_id', name=op.f('pk_project_teams'))
    )
    op.create_index('ix_project_teams_team_id', 'project_teams', ['team_id'], unique=False)
    op.create_table('team_members',
    sa.Column('team_id', sa.UUID(), nullable=False),
    sa.Column('member_id', sa.UUID(), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('role', postgresql.ENUM('LEAD', 'MEMBER', name='team_member_role', create_type=False), server_default='MEMBER', nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['member_id', 'organization_id'], ['members.id', 'members.organization_id'], name=op.f('fk_team_members_member_id_organization_id_members')),
    sa.ForeignKeyConstraint(['team_id', 'organization_id'], ['teams.id', 'teams.organization_id'], name=op.f('fk_team_members_team_id_organization_id_teams')),
    sa.PrimaryKeyConstraint('team_id', 'member_id', name=op.f('pk_team_members'))
    )
    op.create_index('ix_team_members_member_id', 'team_members', ['member_id'], unique=False)
    op.create_table('work_items',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('created_by_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.String(length=5000), nullable=True),
    sa.Column('type', postgresql.ENUM('FEATURE', 'STORY', 'BUG', name='work_item_type', create_type=False), nullable=False),
    sa.Column('priority', postgresql.ENUM('LOW', 'MEDIUM', 'HIGH', 'URGENT', name='priority', create_type=False), server_default='MEDIUM', nullable=False),
    sa.Column('status', postgresql.ENUM('OPEN', 'IN_PROGRESS', 'DONE', 'CANCELLED', name='work_item_status', create_type=False), server_default='OPEN', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('char_length(btrim(title)) > 0', name=op.f('ck_work_items_title_not_blank')),
    sa.ForeignKeyConstraint(['created_by_id', 'organization_id'], ['members.id', 'members.organization_id'], name=op.f('fk_work_items_created_by_id_organization_id_members')),
    sa.ForeignKeyConstraint(['project_id', 'organization_id'], ['projects.id', 'projects.organization_id'], name=op.f('fk_work_items_project_id_organization_id_projects')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_work_items')),
    sa.UniqueConstraint('id', 'project_id', name=op.f('uq_work_items_id_project_id'))
    )
    op.create_index('ix_work_items_project_id_status', 'work_items', ['project_id', 'status'], unique=False)
    op.create_table('sprints',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('team_id', sa.UUID(), nullable=False),
    sa.Column('created_by_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('goal', sa.String(length=5000), nullable=True),
    sa.Column('status', postgresql.ENUM('PLANNED', 'ACTIVE', 'COMPLETED', 'CANCELLED', name='sprint_status', create_type=False), server_default='PLANNED', nullable=False),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_sprints_name_not_blank')),
    sa.CheckConstraint('end_date IS NULL OR start_date IS NULL OR end_date >= start_date', name=op.f('ck_sprints_dates')),
    sa.ForeignKeyConstraint(['created_by_id', 'organization_id'], ['members.id', 'members.organization_id'], name=op.f('fk_sprints_created_by_id_organization_id_members')),
    sa.ForeignKeyConstraint(['project_id', 'organization_id'], ['projects.id', 'projects.organization_id'], name=op.f('fk_sprints_project_id_organization_id_projects')),
    sa.ForeignKeyConstraint(['project_id', 'team_id'], ['project_teams.project_id', 'project_teams.team_id'], name=op.f('fk_sprints_project_id_team_id_project_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sprints')),
    sa.UniqueConstraint('id', 'project_id', name=op.f('uq_sprints_id_project_id'))
    )
    op.create_index('uq_sprints_project_id_team_id_active', 'sprints', ['project_id', 'team_id'], unique=True, postgresql_where=sa.text("status = 'ACTIVE'"))
    op.create_index('uq_sprints_project_id_team_id_name', 'sprints', ['project_id', 'team_id', sa.literal_column('lower(name)')], unique=True)
    op.create_table('tasks',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('work_item_id', sa.UUID(), nullable=True),
    sa.Column('sprint_id', sa.UUID(), nullable=True),
    sa.Column('assignee_id', sa.UUID(), nullable=True),
    sa.Column('created_by_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.String(length=5000), nullable=True),
    sa.Column('requirements', sa.String(length=10000), nullable=True),
    sa.Column('acceptance_criteria', sa.String(length=10000), nullable=True),
    sa.Column('priority', postgresql.ENUM('LOW', 'MEDIUM', 'HIGH', 'URGENT', name='priority', create_type=False), server_default='MEDIUM', nullable=False),
    sa.Column('status', postgresql.ENUM('TODO', 'IN_PROGRESS', 'REVIEW', 'DONE', 'BLOCKED', name='task_status', create_type=False), server_default='TODO', nullable=False),
    sa.Column('due_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'DONE') = (completed_at IS NOT NULL)", name=op.f('ck_tasks_done_completed')),
    sa.CheckConstraint('char_length(btrim(title)) > 0', name=op.f('ck_tasks_title_not_blank')),
    sa.CheckConstraint('number >= 1', name=op.f('ck_tasks_number_positive')),
    sa.ForeignKeyConstraint(['assignee_id', 'organization_id'], ['members.id', 'members.organization_id'], name=op.f('fk_tasks_assignee_id_organization_id_members')),
    sa.ForeignKeyConstraint(['created_by_id', 'organization_id'], ['members.id', 'members.organization_id'], name=op.f('fk_tasks_created_by_id_organization_id_members')),
    sa.ForeignKeyConstraint(['project_id', 'organization_id'], ['projects.id', 'projects.organization_id'], name=op.f('fk_tasks_project_id_organization_id_projects')),
    sa.ForeignKeyConstraint(['sprint_id', 'project_id'], ['sprints.id', 'sprints.project_id'], name=op.f('fk_tasks_sprint_id_project_id_sprints'), ondelete='SET NULL (sprint_id)'),
    sa.ForeignKeyConstraint(['work_item_id', 'project_id'], ['work_items.id', 'work_items.project_id'], name=op.f('fk_tasks_work_item_id_project_id_work_items'), ondelete='SET NULL (work_item_id)'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tasks')),
    sa.UniqueConstraint('project_id', 'number', name=op.f('uq_tasks_project_id_number'))
    )
    op.create_index('ix_tasks_assignee_id', 'tasks', ['assignee_id'], unique=False, postgresql_where=sa.text('assignee_id IS NOT NULL'))
    op.create_index('ix_tasks_project_id_status', 'tasks', ['project_id', 'status'], unique=False)
    op.create_index('ix_tasks_sprint_id', 'tasks', ['sprint_id'], unique=False, postgresql_where=sa.text('sprint_id IS NOT NULL'))
    op.create_index('ix_tasks_work_item_id', 'tasks', ['work_item_id'], unique=False, postgresql_where=sa.text('work_item_id IS NOT NULL'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_tasks_work_item_id', table_name='tasks', postgresql_where=sa.text('work_item_id IS NOT NULL'))
    op.drop_index('ix_tasks_sprint_id', table_name='tasks', postgresql_where=sa.text('sprint_id IS NOT NULL'))
    op.drop_index('ix_tasks_project_id_status', table_name='tasks')
    op.drop_index('ix_tasks_assignee_id', table_name='tasks', postgresql_where=sa.text('assignee_id IS NOT NULL'))
    op.drop_table('tasks')
    op.drop_index('uq_sprints_project_id_team_id_name', table_name='sprints')
    op.drop_index('uq_sprints_project_id_team_id_active', table_name='sprints', postgresql_where=sa.text("status = 'ACTIVE'"))
    op.drop_table('sprints')
    op.drop_index('ix_work_items_project_id_status', table_name='work_items')
    op.drop_table('work_items')
    op.drop_index('ix_team_members_member_id', table_name='team_members')
    op.drop_table('team_members')
    op.drop_index('ix_project_teams_team_id', table_name='project_teams')
    op.drop_table('project_teams')
    op.drop_index('uq_teams_workspace_id_name_active', table_name='teams', postgresql_where=sa.text('archived_at IS NULL'))
    op.drop_table('teams')
    op.drop_index('uq_projects_workspace_id_name_active', table_name='projects', postgresql_where=sa.text('archived_at IS NULL'))
    op.drop_table('projects')
    op.drop_index('uq_workspaces_organization_id_name_active', table_name='workspaces', postgresql_where=sa.text('archived_at IS NULL'))
    op.drop_constraint(
        op.f('fk_members_workspace_id_organization_id_workspaces'), 'members', type_='foreignkey'
    )
    op.drop_table('workspaces')
    op.drop_index('uq_members_organization_id_name_ai', table_name='members', postgresql_where=sa.text("type = 'AI'"))
    op.drop_index('uq_members_organization_id_email_ai', table_name='members', postgresql_where=sa.text("type = 'AI' AND email IS NOT NULL"))
    op.drop_table('members')
    op.drop_table('organizations')
    op.drop_index('uq_users_email', table_name='users')
    op.drop_table('users')
    bind = op.get_bind()
    for name in ENUMS:
        postgresql.ENUM(name=name).drop(bind)
