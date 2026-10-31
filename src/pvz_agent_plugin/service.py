from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from pathlib import Path
import re
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import DeepSeekAgent
from .capture import GameCaptureHub
from .plugin import PvZPlugin
from .pvz_native.offsets import PLANT_NAMES, PLANT_SUN_COST, PLANT_UPGRADE_MAP, ZOMBIE_NAMES
from .rules import ARMOR_TYPES, CONSUMABLE_OVERLAY_TYPES, MUSHROOM_TYPES, SUPPORT_TYPES
from .settings import Settings

settings = Settings()
plugin = PvZPlugin()
agent = DeepSeekAgent(plugin, settings.deepseek_base_url, settings.deepseek_api_key, settings.deepseek_model)
capture = GameCaptureHub(settings.capture_window_title, settings.capture_fps, settings.capture_quality)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        agent.close()
        capture.stop()
        if plugin.connected:
            plugin.disconnect()


app = FastAPI(title="PvZ Agent Plugin", version="1.0.0", lifespan=lifespan)


class ActionRequest(BaseModel):
    action: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentRequest(BaseModel):
    goal: str = ""


class SettingsRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(default="", max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=500)


class ModelsRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(default="", max_length=1000)


class ProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    avatar_data_url: str = Field(default="", max_length=8_000_000)


ENV_PATH = Path(".env")
PROFILE_DIR = Path("profile")
_AVATAR_DATA_URL = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=\r\n]+)$")


def _public_settings() -> dict[str, Any]:
    return {
        "base_url": settings.deepseek_base_url,
        "model": settings.deepseek_model,
        "goal": settings.agent_goal,
        "api_key_configured": bool(settings.deepseek_api_key.strip()),
    }


def _update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Update known values without exposing or discarding unrelated local settings."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    if remaining and output and output[-1] != "":
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


def _validated_settings(request: SettingsRequest) -> tuple[str, str, str, str]:
    base_url = request.base_url.strip().rstrip("/")
    model = request.model.strip()
    goal = request.goal.strip() or "自主完成当前关卡"
    api_key = request.api_key.strip() or settings.deepseek_api_key.strip()
    if not base_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="API 地址必须以 https:// 或 http:// 开头")
    if any(char in value for value in (base_url, model, goal, api_key) for char in "\r\n"):
        raise HTTPException(status_code=400, detail="设置值不能包含换行符")
    if not api_key:
        raise HTTPException(status_code=400, detail="请先填写 API Key")
    return base_url, api_key, model, goal


def _api_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
    except ValueError:
        message = None
    return str(message or f"HTTP {response.status_code}")[:300]


def _save_settings(base_url: str, api_key: str, model: str, goal: str) -> dict[str, Any]:
    agent.configure(base_url=base_url, api_key=api_key, model=model)
    settings.deepseek_base_url = base_url
    settings.deepseek_api_key = api_key
    settings.deepseek_model = model
    settings.agent_goal = goal
    _update_env_file(ENV_PATH, {
        "DEEPSEEK_BASE_URL": base_url,
        "DEEPSEEK_API_KEY": api_key,
        "DEEPSEEK_MODEL": model,
        "AGENT_GOAL": goal,
    })
    return _public_settings()


def _profile_avatar_file() -> Path | None:
    if not settings.channel_avatar_path:
        return None
    root = Path.cwd().resolve()
    candidate = (root / settings.channel_avatar_path).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def _public_profile() -> dict[str, Any]:
    avatar = _profile_avatar_file()
    version = avatar.stat().st_mtime_ns if avatar else 1
    return {
        "name": settings.channel_name,
        "avatar_url": f"/api/profile/avatar?v={version}" if avatar else "/static/avatar.jpg",
        "custom_avatar": bool(avatar),
    }


def _save_profile(request: ProfileRequest) -> dict[str, Any]:
    name = request.name.strip()
    if not name:
        raise ValueError("频道名称不能为空")
    avatar_path = settings.channel_avatar_path
    if request.avatar_data_url:
        match = _AVATAR_DATA_URL.fullmatch(request.avatar_data_url)
        if not match:
            raise ValueError("头像仅支持 PNG、JPEG 或 WebP 图片")
        image_type, encoded = match.groups()
        try:
            image = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError("头像文件编码无效") from exc
        if not image or len(image) > 5 * 1024 * 1024:
            raise ValueError("头像文件必须小于 5MB")
        valid = (
            image_type == "png" and image.startswith(b"\x89PNG\r\n\x1a\n")
            or image_type == "jpeg" and image.startswith(b"\xff\xd8\xff")
            or image_type == "webp" and image.startswith(b"RIFF") and image[8:12] == b"WEBP"
        )
        if not valid:
            raise ValueError("头像文件内容与图片格式不匹配")
        extension = "jpg" if image_type == "jpeg" else image_type
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        destination = PROFILE_DIR / f"avatar.{extension}"
        temporary = destination.with_suffix(f".{extension}.tmp")
        temporary.write_bytes(image)
        temporary.replace(destination)
        avatar_path = destination.as_posix()
    settings.channel_name = name
    settings.channel_avatar_path = avatar_path
    updates = {"CHANNEL_NAME": name}
    if avatar_path:
        updates["CHANNEL_AVATAR_PATH"] = avatar_path
    _update_env_file(ENV_PATH, updates)
    return _public_profile()


def safe(call):
    try:
        return call()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/status")
def api_status():
    return {"plugin": plugin.status(), "agent": agent.status()}


@app.get("/api/settings")
def api_settings():
    return _public_settings()


@app.get("/api/profile")
def api_profile():
    return _public_profile()


@app.get("/api/profile/avatar")
def api_profile_avatar():
    avatar = _profile_avatar_file()
    if not avatar:
        return FileResponse(STATIC / "avatar.jpg")
    return FileResponse(avatar, headers={"Cache-Control": "no-store"})


@app.post("/api/profile")
def api_profile_update(request: ProfileRequest):
    return safe(lambda: _save_profile(request))


@app.post("/api/settings")
def api_settings_update(request: SettingsRequest):
    base_url, api_key, model, goal = _validated_settings(request)
    return safe(lambda: _save_settings(base_url, api_key, model, goal))


@app.post("/api/settings/models")
def api_settings_models(request: ModelsRequest):
    base_url = request.base_url.strip().rstrip("/")
    api_key = request.api_key.strip() or settings.deepseek_api_key.strip()
    if not base_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="API 地址格式不正确")
    if not api_key:
        raise HTTPException(status_code=400, detail="请先填写 API Key")
    try:
        response = httpx.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"获取模型失败：{exc}") from exc
    if not response.is_success:
        raise HTTPException(status_code=400, detail=f"获取模型失败：{_api_error(response)}")
    try:
        models = response.json().get("data", [])
        model_ids = sorted({item["id"] for item in models if isinstance(item, dict) and item.get("id")})
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="API 返回的模型列表格式不正确") from exc
    if not model_ids:
        raise HTTPException(status_code=400, detail="API 没有返回可用模型")
    return {"models": model_ids}


@app.post("/api/settings/test-and-save")
def api_settings_test_and_save(request: SettingsRequest):
    base_url, api_key, model, goal = _validated_settings(request)
    if agent.running:
        raise HTTPException(status_code=400, detail="请先停止 Agent，再测试 API 设置")
    test_tool = {
        "type": "function",
        "function": {
            "name": "connection_test",
            "description": "Return a successful connection test.",
            "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        },
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Call connection_test with ok=true."}],
        "tools": [test_tool],
        "tool_choice": "required",
        "thinking": {"type": "disabled"},
        "max_tokens": 64,
        "temperature": 0,
    }
    started = time.perf_counter()
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"模型测试失败：{exc}") from exc
    if not response.is_success:
        raise HTTPException(status_code=400, detail=f"模型测试失败：{_api_error(response)}")
    try:
        calls = response.json()["choices"][0]["message"].get("tool_calls") or []
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="模型测试失败：返回格式不兼容") from exc
    if not calls or calls[0].get("function", {}).get("name") != "connection_test":
        raise HTTPException(status_code=400, detail="模型测试失败：该模型没有按要求返回工具调用")
    result = safe(lambda: _save_settings(base_url, api_key, model, goal))
    return {**result, "test": {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1)}}


@app.post("/api/connect")
def api_connect():
    return safe(plugin.connect)


@app.post("/api/disconnect")
def api_disconnect():
    agent.stop()
    plugin.disconnect()
    return {"status": "ok"}


@app.get("/api/state")
def api_state():
    return safe(plugin.read_state)


@app.get("/api/events")
def api_events(limit: int = 80, category: str | None = None):
    return plugin.events(limit, category)


@app.get("/api/capture/status")
def api_capture_status():
    capture.ensure_started()
    return capture.status()


@app.get("/api/capture/stream")
def api_capture_stream():
    return StreamingResponse(
        capture.stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/api/capabilities")
def api_capabilities():
    return {
        "schema_version": plugin.schema_version,
        "actions": {
            "place_plant": ["card_index", "row", "col"],
            "shovel": ["row", "col"],
            "fire_cob": ["row", "col", "target_row", "target_col"],
            "select_seeds": ["seeds"],
            "collect_items": [],
            "set_auto_collect": ["enabled"],
            "wait": ["time"],
            "terminate": ["status"],
        },
        "plants": [{"type": key, "name": name, "cost": PLANT_SUN_COST.get(key, 0)} for key, name in PLANT_NAMES.items()],
        "zombies": [{"type": key, "name": name} for key, name in ZOMBIE_NAMES.items()],
        "rules": {
            "support_types": sorted(SUPPORT_TYPES),
            "armor_types": sorted(ARMOR_TYPES),
            "consumable_overlay_types": sorted(CONSUMABLE_OVERLAY_TYPES),
            "mushroom_types": sorted(MUSHROOM_TYPES),
            "upgrade_map": PLANT_UPGRADE_MAP,
            "interaction": {
                "planting": "background_window_drag_with_native_click_fallback",
                "auto_collect": "delayed_humanized",
                "minimum_collect_delay_seconds": 1.0,
            },
        },
        "logs": ["logs/events.jsonl", "logs/action.jsonl", "logs/agent.jsonl", "logs/model.jsonl", "logs/states.jsonl"],
    }


@app.post("/api/actions")
def api_action(request: ActionRequest):
    return safe(lambda: plugin.execute(request.action, **request.arguments))


@app.post("/api/agent/start")
def api_agent_start(request: AgentRequest):
    safe(lambda: agent.start(request.goal or settings.agent_goal))
    return agent.status()


@app.post("/api/agent/stop")
def api_agent_stop():
    agent.stop()
    return agent.status()


STATIC = Path(__file__).resolve().parent / "static"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")

