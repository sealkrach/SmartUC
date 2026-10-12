import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, require_role
from ..db import get_session
from ..models import Role, Template, User
from ..schemas import TemplateOut
from ..services.templates import sync_templates

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateOut])
async def list_templates(session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    return (await session.scalars(select(Template).order_by(Template.key, Template.version.desc()))).all()


@router.get("/{template_id}", response_model=TemplateOut)
async def get_template(template_id: str, session: AsyncSession = Depends(get_session),
                       _: User = Depends(get_current_user)):
    t = await session.get(Template, template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    return t


@router.get("/{template_id}/yaml", response_class=PlainTextResponse)
async def template_yaml(template_id: str, session: AsyncSession = Depends(get_session),
                        _: User = Depends(get_current_user)):
    t = await session.get(Template, template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    return yaml.safe_dump(t.definition, sort_keys=False, allow_unicode=True)


@router.post("/sync", response_model=list[TemplateOut])
async def sync(session: AsyncSession = Depends(get_session), _: User = Depends(require_role(Role.admin))):
    """Reload YAML templates from disk (new versions are added, same versions updated)."""
    return await sync_templates(session)


@router.post("", response_model=TemplateOut, status_code=201)
async def upload_template(body: dict, session: AsyncSession = Depends(get_session),
                          _: User = Depends(require_role(Role.admin))):
    """Register a template from a parsed YAML/JSON definition (key, version, sections...)."""
    if not body.get("key") or not body.get("sections"):
        raise HTTPException(400, "Definition needs at least 'key' and 'sections'")
    version = int(body.get("version", 1))
    if await session.scalar(select(Template).where(Template.key == body["key"], Template.version == version)):
        raise HTTPException(409, "This key/version already exists; bump the version")
    make_default = bool(body.get("is_default"))
    if make_default:
        for t in (await session.scalars(select(Template).where(Template.is_default.is_(True)))).all():
            t.is_default = False
    t = Template(key=body["key"], version=version, name=body.get("name", body["key"]),
                 description=body.get("description", ""), is_default=make_default, definition=body)
    session.add(t)
    await session.commit()
    return t


@router.post("/{template_id}/default", response_model=TemplateOut)
async def set_default(template_id: str, session: AsyncSession = Depends(get_session),
                      _: User = Depends(require_role(Role.admin))):
    t = await session.get(Template, template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    for other in (await session.scalars(select(Template).where(Template.is_default.is_(True)))).all():
        other.is_default = False
    t.is_default = True
    await session.commit()
    return t
