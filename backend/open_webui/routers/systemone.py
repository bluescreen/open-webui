from __future__ import annotations

import asyncio
import logging

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST
from open_webui.models.config import Config
from open_webui.utils.auth import get_admin_user
from open_webui.utils.headers import get_headers_and_cookies
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.model_ids import strip_provider_model_prefix
from open_webui.utils.session_pool import cleanup_response, get_client_timeout, get_session
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

_MODEL_LIST_TIMEOUT = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)

# TypeSafe refuses a request carrying `assets` (pictures) without this opt-in.
ASSETS_HEADER = 'TypeSafe-Danger-Zone-Images'

SYSTEMONE_CONFIG_KEYS = {
    'ENABLE_SYSTEMONE_API': 'systemone.enable',
    'SYSTEMONE_API_BASE_URLS': 'systemone.api_base_urls',
    'SYSTEMONE_API_KEYS': 'systemone.api_keys',
    'SYSTEMONE_API_CONFIGS': 'systemone.api_configs',
}


async def get_systemone_config() -> dict:
    values = await Config.get_many(*SYSTEMONE_CONFIG_KEYS.values())
    return {field: values.get(storage_key) for field, storage_key in SYSTEMONE_CONFIG_KEYS.items()}


def get_connection(config: dict, idx: int) -> tuple[str, str, dict]:
    urls = config['SYSTEMONE_API_BASE_URLS'] or []
    keys = config['SYSTEMONE_API_KEYS'] or []
    api_configs = config['SYSTEMONE_API_CONFIGS'] or {}
    return urls[idx], keys[idx] if idx < len(keys) else '', api_configs.get(str(idx), {})


def model_ids_of(payload) -> list[str]:
    """Model ids from a /models response: OpenAI's `data` list or a bare `models` list."""
    if not isinstance(payload, dict):
        return []
    ids = []
    for entry in payload.get('data') or payload.get('models') or []:
        model_id = entry.get('id') or entry.get('name') if isinstance(entry, dict) else entry
        if isinstance(model_id, str) and model_id:
            ids.append(model_id)
    return ids


async def fetch_model_ids(request: Request, url: str, key: str, api_config: dict, user) -> list[str]:
    try:
        headers, cookies = await get_headers_and_cookies(request, url, key, api_config, user=user)
        async with aiohttp.ClientSession(timeout=_MODEL_LIST_TIMEOUT, trust_env=True) as session:
            async with session.get(
                f'{url}/models', headers=headers, cookies=cookies, ssl=AIOHTTP_CLIENT_SESSION_SSL
            ) as r:
                return model_ids_of(await r.json(loads=JSONCodec.loads))
    except Exception as e:
        log.error(f'System One model list failed for {url}: {e}')
        return []


async def get_all_models(request: Request, user) -> dict[str, dict]:
    config = await get_systemone_config()
    models: dict[str, dict] = {}

    if config['ENABLE_SYSTEMONE_API']:

        async def connection_models(idx: int) -> list[dict]:
            url, key, api_config = get_connection(config, idx)
            if not api_config.get('enable', True):
                return []
            # A clone need not serve /models at all, so listed ids win over asking.
            ids = api_config.get('model_ids') or await fetch_model_ids(request, url, key, api_config, user)
            prefix_id = api_config.get('prefix_id')
            return [
                {
                    'id': f'{prefix_id}.{model_id}' if prefix_id else model_id,
                    'name': model_id,
                    'object': 'model',
                    'owned_by': 'systemone',
                    'urlIdx': idx,
                }
                for model_id in ids
            ]

        urls = config['SYSTEMONE_API_BASE_URLS'] or []
        for connection in await asyncio.gather(*(connection_models(idx) for idx in range(len(urls)))):
            for model in connection:
                models.setdefault(model['id'], model)

    request.app.state.SYSTEMONE_MODELS = models
    return models


@router.get('/config')
async def get_config(user=Depends(get_admin_user)):
    return await get_systemone_config()


class SystemOneConfigForm(BaseModel):
    ENABLE_SYSTEMONE_API: bool | None = None
    SYSTEMONE_API_BASE_URLS: list[str]
    SYSTEMONE_API_KEYS: list[str]
    SYSTEMONE_API_CONFIGS: dict


@router.post('/config/update')
async def update_config(request: Request, form_data: SystemOneConfigForm, user=Depends(get_admin_user)):
    urls = form_data.SYSTEMONE_API_BASE_URLS
    keys = [*form_data.SYSTEMONE_API_KEYS, *([''] * len(urls))][: len(urls)]
    valid_keys = set(map(str, range(len(urls))))
    api_configs = {key: value for key, value in form_data.SYSTEMONE_API_CONFIGS.items() if key in valid_keys}

    await Config.upsert(
        {
            'systemone.enable': form_data.ENABLE_SYSTEMONE_API,
            'systemone.api_base_urls': urls,
            'systemone.api_keys': keys,
            'systemone.api_configs': api_configs,
        }
    )
    request.app.state.SYSTEMONE_MODELS = {}

    return await get_systemone_config()


@router.get('/models')
@router.get('/v1/models')
async def get_models(request: Request, user=Depends(get_admin_user)):
    return {'object': 'list', 'data': list((await get_all_models(request, user)).values())}


@router.post('/v1/systemone')
async def systemone(request: Request, form_data: dict, user=Depends(get_admin_user)):
    """Answers one System One request (`state`, `model`, `questions`) through the
    connection that serves the model, in TypeSafe's wire format both ways."""
    model_id = form_data.get('model')
    if not isinstance(model_id, str) or not model_id:
        raise HTTPException(status_code=400, detail='model is required')
    if not isinstance(form_data.get('questions'), dict) or not form_data['questions']:
        raise HTTPException(status_code=400, detail='questions must be a non-empty object')

    models = getattr(request.app.state, 'SYSTEMONE_MODELS', None) or {}
    if model_id not in models:
        models = await get_all_models(request, user)
    if model_id not in models:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.MODEL_NOT_FOUND(model_id))

    url, key, api_config = get_connection(await get_systemone_config(), models[model_id]['urlIdx'])
    form_data['model'] = strip_provider_model_prefix(model_id, api_config.get('prefix_id'))

    headers, cookies = await get_headers_and_cookies(request, url, key, api_config, user=user)
    if form_data.get('assets'):
        headers[ASSETS_HEADER] = 'true'

    r = None
    try:
        session = await get_session()
        r = await session.request(
            method='POST',
            url=f'{url}/systemone',
            data=JSONCodec.dumps(form_data),
            headers=headers,
            cookies=cookies,
            timeout=get_client_timeout(),
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        )
        try:
            response_data = await r.json(loads=JSONCodec.loads)
        except Exception:
            response_data = await r.text()

        if r.status >= 400:
            if isinstance(response_data, (dict, list)):
                return JSONResponse(status_code=r.status, content=response_data)
            return PlainTextResponse(status_code=r.status, content=response_data)
        return response_data
    except Exception as e:
        log.exception(e)
        raise HTTPException(status_code=r.status if r else 500, detail=ERROR_MESSAGES.SERVER_CONNECTION_ERROR)
    finally:
        await cleanup_response(r)
