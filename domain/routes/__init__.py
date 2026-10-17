from fastapi import APIRouter

from domain.routes import (
    members,
    organizations,
    projects,
    sprints,
    tasks,
    teams,
    users,
    work_items,
    workspaces,
)

domain_router = APIRouter()
for module in (
    users,
    organizations,
    members,
    workspaces,
    teams,
    projects,
    work_items,
    sprints,
    tasks,
):
    domain_router.include_router(module.router)
