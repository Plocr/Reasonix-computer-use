"""Fail-closed routing for screenshot results.

The Reasonix host knows whether the selected model accepts image input, but
that value is not part of the current MCP or hook payload.  This module keeps
the computer-use side honest: a screenshot is forwarded only when an
explicit capability signal is available.  An external vision route (for
example a Mimo MCP server) can be declared without pretending that the
current text model understood the image.

The resolver deliberately accepts several spellings used by Reasonix desktop,
hooks, and test harnesses.  Unknown values never enable vision.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from urllib.parse import urlparse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


TRUE_VALUES = {"1", "true", "yes", "on", "enabled", "allow", "native"}
FALSE_VALUES = {"0", "false", "no", "off", "disabled", "deny", "none", "unavailable"}
MCP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class VisionRoute:
    """Resolved route for a possible image result."""

    mode: str  # native | external | unavailable
    available: bool
    source: str
    reason: str
    model: str = ""
    provider: str = ""
    server: str = ""
    tool: str = ""
    handoff: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in TRUE_VALUES:
            return True
        if lowered in FALSE_VALUES:
            return False
    return None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _capability_from_payload(payload: Mapping[str, Any]) -> bool | None:
    """Read explicit image-input fields from arbitrary host payloads."""

    candidates: list[Any] = []
    for mapping in (payload, payload.get("capabilities", {}), payload.get("model", {}),
                    payload.get("meta", {}), payload.get("session", {})):
        if isinstance(mapping, Mapping):
            candidates.extend(mapping.get(key) for key in (
                "imageInputEnabled", "image_input_enabled", "supportsVision",
                "supports_vision", "vision", "multimodal", "image_input",
            ))
    declared = [value for candidate in candidates
                if (value := _bool_value(candidate)) is not None]
    # Contradictory host metadata must fail closed.  A stale generic
    # ``multimodal=true`` flag must not override the selected model's explicit
    # ``image_input_enabled=false`` declaration.
    if False in declared:
        return False
    if True in declared:
        return True
    return None


def _callable_external_target(server: str, tool: str) -> bool:
    """Return whether Reasonix can construct one unambiguous MCP tool name."""

    return bool(MCP_NAME_RE.fullmatch(server) and MCP_NAME_RE.fullmatch(tool))


def _model_from_payload(payload: Mapping[str, Any]) -> str:
    for mapping in (payload, payload.get("model", {}), payload.get("meta", {}),
                    payload.get("session", {})):
        if not isinstance(mapping, Mapping):
            continue
        value = _first(mapping, "model_ref", "modelRef", "model_name", "modelName",
                       "selected_model", "selectedModel", "model", "ref", "name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _env_value(environ: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = str(environ.get(key, "")).strip()
        if value:
            return value
    return ""


def _capability_from_env(environ: Mapping[str, str]) -> bool | None:
    """Resolve all host capability flags with false taking precedence."""

    declared = [value for key in (
        "REASONIX_IMAGE_INPUT_ENABLED", "REASONIX_SUPPORTS_VISION",
        "REASONIX_MODEL_SUPPORTS_VISION", "REASONIX_VISION_ENABLED",
    ) if key in environ and (value := _bool_value(environ.get(key))) is not None]
    if False in declared:
        return False
    if True in declared:
        return True
    return None


def _parse_external_route(value: Any, source: str) -> VisionRoute | None:
    if isinstance(value, Mapping):
        mode = str(value.get("mode", "external")).strip().casefold()
        if mode in {"none", "unavailable", "disabled"}:
            return None
        server = str(value.get("server", value.get("mcp_server", ""))).strip()
        tool = str(value.get("tool", value.get("mcp_tool", ""))).strip()
        if not tool and server.casefold() in {"mimo", "mimo-mcp"}:
            tool = "understand_image"
        if not _callable_external_target(server, tool):
            return None
        provider = str(value.get("provider", "")).strip()
        model = str(value.get("model", "")).strip()
        handoff = str(value.get("handoff", "agent")).strip() or "agent"
        return VisionRoute("external", True, source, "external_route_configured",
                           model, provider, server, tool, handoff)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.casefold() in FALSE_VALUES:
        return None
    if text.startswith("{"):
        try:
            return _parse_external_route(json.loads(text), source)
        except (json.JSONDecodeError, TypeError):
            return None
    # Accepted compact forms: ``mcp:mimo-mcp/understand_image`` and
    # ``mimo-mcp:understand_image``. Only the known Mimo server may omit its
    # canonical tool name; every other external route must name both parts.
    compact = text.removeprefix("mcp:")
    if ":" in compact and "/" not in compact:
        server, tool = compact.split(":", 1)
    elif "/" in compact:
        server, tool = compact.split("/", 1)
    else:
        server, tool = compact, ""
    if not tool.strip() and server.strip().casefold() in {"mimo", "mimo-mcp"}:
        tool = "understand_image"
    if not _callable_external_target(server.strip(), tool.strip()):
        return None
    return VisionRoute("external", True, source, "external_route_configured",
                       provider="", server=server.strip(), tool=tool.strip(), handoff="agent")


def _route_file(environ: Mapping[str, str], base_dir: Path | None) -> tuple[Any, str] | None:
    candidates: list[Path] = []
    configured = _env_value(environ, "REASONIX_VISION_ROUTE_FILE", "REASONIX_COMPUTER_USE_VISION_ROUTE_FILE")
    if configured:
        candidates.append(Path(configured).expanduser())
    if base_dir is not None:
        candidates.append(base_dir / "memory" / "vision-route.json")
    for path in candidates:
        try:
            return json.loads(path.read_text(encoding="utf-8")), "file"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def _config_paths(environ: Mapping[str, str], base_dir: Path | None,
                  paths: list[Path] | None) -> list[Path]:
    if paths is not None:
        return [Path(path) for path in paths]
    result: list[Path] = []
    explicit = _env_value(environ, "REASONIX_CONFIG", "REASONIX_CONFIG_PATH")
    if explicit:
        result.append(Path(explicit).expanduser())
    workspace = _env_value(environ, "REASONIX_WORKSPACE_ROOT", "REASONIX_WORKSPACE")
    if workspace:
        result.append(Path(workspace) / "reasonix.toml")
    if base_dir is not None:
        result.append(base_dir / "reasonix.toml")
    appdata = _env_value(environ, "APPDATA")
    if appdata:
        result.append(Path(appdata) / "reasonix" / "config.toml")
    # The plugin may run outside a normal shell where APPDATA is absent.
    home = Path.home()
    result.extend((home / "AppData" / "Roaming" / "reasonix" / "config.toml",
                   home / ".config" / "reasonix" / "config.toml"))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in result:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _model_capability_from_toml(model: str, paths: list[Path]) -> tuple[bool | None, str, str]:
    """Return (capability, provider, reason) for a selected model."""

    if not model:
        return None, "", "model_not_declared"
    model_fold = model.casefold().strip()
    short_model = model_fold.split("/", 1)[-1]
    found = False
    for path in paths:
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue
        providers = document.get("providers", [])
        if isinstance(providers, Mapping):
            providers = [providers]
        if not isinstance(providers, list):
            continue
        for provider in providers:
            if not isinstance(provider, Mapping):
                continue
            provider_name = str(provider.get("name", "")).strip()
            models = provider.get("models", [])
            if isinstance(models, str):
                models = [models]
            if not isinstance(models, list):
                models = []
            singular_model = provider.get("model")
            if singular_model:
                models = [singular_model, *models]
            defaults = [provider.get("default")] if provider.get("default") else []
            names = {str(item).casefold().strip() for item in [*models, *defaults] if item}
            # Older Reasonix configs expose a provider alias as the selected
            # model (for example ``deepseek-flash``) and keep the concrete
            # model in the singular ``model`` field.
            if provider_name:
                names.add(provider_name.casefold())
            qualified = {f"{provider_name}/{name}" for name in names if provider_name}
            if model_fold not in names and model_fold not in qualified and short_model not in names:
                continue
            found = True
            explicit_vision = _bool_value(provider.get("vision"))
            vision = explicit_vision
            vision_models = provider.get("vision_models", provider.get("visionModels"))
            if isinstance(vision_models, str):
                vision_models = [vision_models]
            if explicit_vision is None and isinstance(vision_models, list):
                listed = {str(item).casefold().strip() for item in vision_models}
                if short_model in listed or model_fold in listed:
                    vision = True
                elif vision is None:
                    vision = False
            # Mirror Reasonix's conservative official MiMo fallback.  It is
            # limited to the known model IDs and first-party hosts; a random
            # OpenAI-compatible proxy must never be treated as visual merely
            # because its model name contains "mimo".
            provider_host = (urlparse(str(provider.get("base_url", "")).strip()).hostname or "").casefold()
            if (vision is None and short_model in {"mimo-v2.5", "mimo-v2-omni"}
                    and provider_host in {"api.xiaomimimo.com", "token-plan-cn.xiaomimimo.com"}):
                vision = True
            if vision is True:
                return True, provider_name, "model_declared_vision"
            return False, provider_name, "model_declared_text_only"
    return (False if found else None), "", "model_not_found" if not found else "model_declared_text_only"


def _configured_default_model(paths: list[Path]) -> str:
    """Read a static default only when the host did not expose a session model."""

    for path in paths:
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue
        value = document.get("default_model")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _external_route_from_reasonix_config(paths: list[Path]) -> VisionRoute | None:
    """Discover the standard Mimo vision MCP without reading its credentials."""

    for path in paths:
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue
        plugins = document.get("plugins", [])
        if isinstance(plugins, Mapping):
            plugins = [plugins]
        if not isinstance(plugins, list):
            continue
        for plugin in plugins:
            if not isinstance(plugin, Mapping):
                continue
            name = str(plugin.get("name", "")).strip()
            command = str(plugin.get("command", "")).strip()
            args = plugin.get("args", [])
            if isinstance(args, str):
                args = [args]
            elif not isinstance(args, list):
                args = []
            tokens = " ".join([name, command, *(str(item) for item in args)]).casefold()
            if name.casefold() != "mimo-mcp" and "mimo-mcp-server" not in tokens:
                continue
            server = name or "mimo-mcp"
            if not _callable_external_target(server, "understand_image"):
                continue
            return VisionRoute(
                "external", True, "config", "reasonix_mimo_mcp_configured",
                provider="mimo", server=server, tool="understand_image",
                handoff="agent")
    return None


def resolve_vision_route(payload: Mapping[str, Any] | None = None, *,
                         environ: Mapping[str, str] | None = None,
                         base_dir: str | Path | None = None,
                         config_paths: list[Path] | None = None) -> VisionRoute:
    """Resolve a safe image route.

    Priority is explicit external route, explicit native capability, model
    metadata, then fail-closed.  ``payload`` and ``environ`` are injectable so
    hooks and tests can use the same resolver without global state.
    """

    payload = payload if isinstance(payload, Mapping) else {}
    env = environ if environ is not None else os.environ
    root = Path(base_dir) if base_dir else None

    payload_capability = _capability_from_payload(payload)
    env_capability = _capability_from_env(env)
    # A negative declaration from either trusted host channel wins over a
    # stale positive flag from the other. Unknown values do not grant access.
    if False in (payload_capability, env_capability):
        capability = False
    elif True in (payload_capability, env_capability):
        capability = True
    else:
        capability = None

    paths = _config_paths(env, root, config_paths)
    model = _model_from_payload(payload) or _env_value(
        env, "REASONIX_MODEL", "REASONIX_CURRENT_MODEL", "REASONIX_SELECTED_MODEL", "REASONIX_MODEL_REF")
    if not model:
        model = _configured_default_model(paths)
    model_capability, provider, reason = _model_capability_from_toml(model, paths)

    route_value = _env_value(env, "REASONIX_COMPUTER_USE_VISION_ROUTE", "REASONIX_VISION_ROUTE")
    if route_value:
        if route_value.casefold() in {"native", "direct", "image"}:
            if capability is False or model_capability is False:
                return VisionRoute(
                    "unavailable", False,
                    "explicit" if capability is False else "config",
                    "native_route_conflicts_with_text_only_model",
                    model=model, provider=provider,
                )
            return VisionRoute("native", True, "env", "explicit_native_route")
        if route_value.casefold() in FALSE_VALUES:
            return VisionRoute("unavailable", False, "env", "explicit_route_disabled")
        external = _parse_external_route(route_value, "env")
        if external:
            return external

    file_route = _route_file(env, root)
    if file_route:
        external = _parse_external_route(file_route[0], file_route[1])
        if external:
            return external

    external_fields = {
        "server": _env_value(env, "REASONIX_VISION_MCP_SERVER", "REASONIX_COMPUTER_USE_VISION_MCP"),
        "tool": _env_value(env, "REASONIX_VISION_MCP_TOOL", "REASONIX_COMPUTER_USE_VISION_TOOL"),
        "provider": _env_value(env, "REASONIX_VISION_PROVIDER"),
        "model": _env_value(env, "REASONIX_VISION_MODEL"),
    }
    if external_fields["server"]:
        external = _parse_external_route(external_fields, "env")
        if external:
            return external

    if capability is True:
        return VisionRoute("native", True, "explicit", "host_declared_image_input",
                           model=model)
    if model_capability is True and capability is not False:
        return VisionRoute("native", True, "config", reason, model=model, provider=provider)
    configured_external = _external_route_from_reasonix_config(paths)
    if configured_external:
        return configured_external
    if model_capability is False or capability is False:
        return VisionRoute("unavailable", False, "config" if model_capability is False else "explicit",
                           "model_text_only" if model_capability is False else "host_declared_text_only",
                           model=model, provider=provider)
    return VisionRoute("unavailable", False, "default", "vision_capability_not_declared",
                       model=model)


def compact_route(payload: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """Return a short model-facing route summary."""

    route = resolve_vision_route(payload, **kwargs)
    result = {
        "mode": route.mode,
        "available": route.available,
        "source": route.source,
        "reason": route.reason,
    }
    for key in ("model", "provider", "server", "tool", "handoff"):
        value = getattr(route, key)
        if value:
            result[key] = value
    return result


def unavailable_result(route: VisionRoute, *, revision: str = "") -> dict[str, Any]:
    """Build a truthful structured response when a screenshot cannot be read."""

    handoff_ready = (route.mode == "external" and route.available
                     and _callable_external_target(route.server, route.tool))
    result: dict[str, Any] = {
        "status": "error",
        "code": "vision_handoff_required" if handoff_ready else "vision_unavailable",
        "retryable": handoff_ready,
        # An unavailable local model is terminal for this task.  An external
        # route is a strategy handoff: the caller may invoke the configured
        # vision tool with the returned image path.
        "blocked": not handoff_ready,
        "source": "none",
        "vision": route.as_dict(),
        "message": ("当前模型未声明图片理解能力，或外部视觉路由不完整；插件不会假装已理解截图。"
                     if not handoff_ready else
                     "当前模型不能直接理解截图；请把图片交给已配置的视觉路由后再继续。"),
        "next_hint": ("切换到已声明 vision_models 的模型（例如 Mimo 视觉模型），"
                      "或配置 REASONIX_VISION_MCP_SERVER/REASONIX_VISION_ROUTE_FILE；"
                      "不要根据图片占位文本猜测坐标。" if not handoff_ready else
                      "调用 vision.server/tool 处理 image_path 后，把结构化识别结果交回当前任务；"
                      "不要声称当前文本模型已理解截图。"),
    }
    if revision:
        result["revision"] = revision
    return result
