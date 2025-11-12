"""Minimal plugin setup helper using only standard library facilities."""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
import gzip
import zlib
from typing import Any, Dict, Optional


CONFIG_QUERY = """
query Configuration($pluginIds: [ID!]) {
  configuration {
    general {
      databasePath
      apiKey
    }
    # Keep plugins in the payload so callers can still inspect plugin entries if needed
    plugins(include: $pluginIds)
  }
}
"""


def _normalize_backend_base(raw: Any) -> Optional[str]:
    if isinstance(raw, str):
        trimmed = raw.strip()
        if not trimmed:
            return ""
        return trimmed.rstrip("/")
    return None


def _coerce_bool(raw: Any) -> Optional[bool]:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return None


def _build_logger():
    try:
        import stashapi.log as stash_log  # type: ignore

        return stash_log
    except Exception:  # pragma: no cover - fallback when stashapi isn't available
        class _FallbackLog:
            def info(self, msg: Any) -> None:
                sys.stderr.write(f"[INFO] {msg}\n")

            def warning(self, msg: Any) -> None:
                sys.stderr.write(f"[WARN] {msg}\n")

            def error(self, msg: Any) -> None:
                sys.stderr.write(f"[ERROR] {msg}\n")

        return _FallbackLog()


log = _build_logger()


def main() -> None:
    raw_input = sys.stdin.read()
    result = {"output": "ok", "error": None}

    try:
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except json.JSONDecodeError as exc:
        log.error(f"Failed to decode input JSON: {exc}")
        result = {"output": None, "error": f"invalid JSON input: {exc}"}
        _emit_result(result)
        return

    try:
        result = run(payload)
    except Exception as exc:  # pragma: no cover - surfaced to caller
        log.error(f"Plugin setup failed: {exc}")
        result = {"output": None, "error": str(exc)}

    # If run returned a dict-style result, use it; otherwise fall back to ok
    if isinstance(result, dict):
        _emit_result(result)
    else:
        _emit_result({"output": "ok", "error": None})


def _emit_result(result: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()


def run(json_input: Dict[str, Any]) -> Dict[str, Any]:
    args = json_input.get("args") or {}
    mode = args.get("mode")
    log.info(f"Plugin setup triggered (mode={mode!r})")

    if mode != "plugin_setup":
        log.info("No setup action requested; exiting early.")
        return {"output": None, "error": "no setup requested"}

    return plugin_setup(json_input)


def plugin_setup(json_input: Dict[str, Any]) -> Dict[str, Any]:
    connection = json_input.get("server_connection") or {}
    plugin_info = json_input.get("plugin") or {}
    plugin_id = plugin_info.get("id") or plugin_info.get("name")

    target = _build_graphql_url(connection)
    headers = _build_headers(connection)
    verify_ssl = connection.get("VerifySSL", True)

    log.info(f"Connecting to GraphQL endpoint: {target}")
    if plugin_id:
        log.info(f"Fetching configuration for plugin: {plugin_id}")
        variables: Optional[Dict[str, Any]] = {"pluginIds": [plugin_id]}
    else:
        log.info("Fetching configuration for all plugins (no plugin id supplied)")
        variables = {"pluginIds": []}
    # Request only the specific configuration fields we need (no introspection):
    # general.databasePath and general.apiKey. Keep plugins in the payload so
    # the caller can still inspect plugin entries if desired.
    try:
        full_query = CONFIG_QUERY
        response = _execute_graphql(target, full_query, variables, headers, verify_ssl)
        config = (response or {}).get("configuration")
        log.info(f"Received configuration: {json.dumps(config, default=str)[:1000]}")
    except Exception as exc:  # pragma: no cover - runtime fallback
        log.warning(f"Configuration query failed: {exc}; falling back to plugins-only query")
        fallback_query = """
        query PluginSetupConfig($pluginIds: [ID!]) {
          configuration { plugins(include: $pluginIds) }
        }
        """
        response = _execute_graphql(target, fallback_query, variables, headers, verify_ssl)
        config = ((response or {}).get("configuration") or {}).get("plugins")
        log.info(f"Current plugin configuration payload (fallback): {json.dumps(config, default=str)}")

    # Resolve database path (absolute or relative to stash base dir) and verify existence
    database_path_raw = None
    api_key = None
    absolute_db_path = None
    db_exists = False

    plugins_section: Optional[Dict[str, Any]] = None
    plugin_entry: Optional[Dict[str, Any]] = None
    if isinstance(config, dict):
        general = config.get("general") or {}
        database_path_raw = general.get("databasePath")
        api_key = general.get("apiKey")
        candidate_plugins = config.get("plugins")
        if isinstance(candidate_plugins, dict):
            plugins_section = candidate_plugins
        elif plugin_id and plugin_id in config and isinstance(config[plugin_id], dict):
            plugins_section = config  # plugins map returned directly
    elif isinstance(config, list):
        general = {}
    else:
        general = {}

    if plugins_section is None and isinstance(config, dict):
        # Fallback when configuration call only returned plugins map.
        plugins_section = {k: v for k, v in config.items() if isinstance(v, dict)}

    if isinstance(plugins_section, dict):
        lookup_keys = []
        if plugin_id:
            lookup_keys.append(plugin_id)
        plugin_name = plugin_info.get("name") if isinstance(plugin_info, dict) else None
        if plugin_name:
            lookup_keys.append(plugin_name)
        for key in lookup_keys:
            entry = plugins_section.get(key)
            if isinstance(entry, dict):
                plugin_entry = entry
                break
        if plugin_entry is None and len(plugins_section) == 1:
            only_value = next(iter(plugins_section.values()))
            if isinstance(only_value, dict):
                plugin_entry = only_value

    if database_path_raw:
        if os.path.isabs(database_path_raw):
            log.info(f"Database path {database_path_raw} is absolute")
            absolute_db_path = os.path.normpath(database_path_raw)
        else:
            stash_dir = connection.get("Dir") or ""
            log.info(f"Database path {database_path_raw} is relative to Stash directory {stash_dir}")
            absolute_db_path = os.path.normpath(os.path.join(stash_dir, database_path_raw))

        db_exists = os.path.isabs(absolute_db_path) and os.path.exists(absolute_db_path)

    backend_base_override = None
    capture_events_enabled = None
    if isinstance(plugin_entry, dict):
        backend_base_override = (
            plugin_entry.get("backend_base_url")
            or plugin_entry.get("backendBaseUrl")
            or plugin_entry.get("backendBaseURL")
        )
        backend_base_override = _normalize_backend_base(backend_base_override)
        capture_events_raw = (
            plugin_entry.get("capture_events")
            or plugin_entry.get("captureEvents")
            or plugin_entry.get("captureEventsEnabled")
        )
        capture_events_enabled = _coerce_bool(capture_events_raw)

    result_payload = {
        "configuration": config,
        "databasePath": absolute_db_path,
        "databaseExists": db_exists,
        "apiKey": api_key,
        "pluginConfiguration": plugin_entry,
        "backendBaseOverride": backend_base_override,
        "captureEventsEnabled": capture_events_enabled,
    }
    log.info(f"Plugin setup completed successfully: {json.dumps(result_payload, default=str)}")
    return {"output": result_payload, "error": None}


def _build_graphql_url(connection: Dict[str, Any]) -> str:
    host = connection.get("Host", "localhost")
    if host == "0.0.0.0":
        host = "127.0.0.1"

    port = connection.get("Port", 9999)
    scheme = connection.get("Scheme", "http")
    base_path = connection.get("Path", "/graphql")
    if not base_path.startswith("/"):
        base_path = f"/{base_path}"

    return f"{scheme}://{host}:{port}{base_path}"


def _build_headers(connection: Dict[str, Any]) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "User-Agent": "AIOverhaulPluginSetup/1.0",
    }

    api_key = connection.get("ApiKey")
    if api_key:
        headers["ApiKey"] = str(api_key)

    cookie_value: Optional[str] = None
    session_cookie = connection.get("SessionCookie")
    if isinstance(session_cookie, dict):
        cookie_value = session_cookie.get("Value") or session_cookie.get("value")
    elif isinstance(session_cookie, str):
        cookie_value = session_cookie

    if cookie_value:
        headers["Cookie"] = f"session={cookie_value}"

    return headers


def _execute_graphql(
    url: str,
    query: str,
    variables: Optional[Dict[str, Any]],
    headers: Dict[str, str],
    verify_ssl: bool = True,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"query": query}
    if variables is not None:
        payload["variables"] = variables

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")

    context = None
    if url.lower().startswith("https") and not verify_ssl:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
            body = _read_response_body(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP error {exc.code} while calling GraphQL: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach GraphQL endpoint: {exc.reason}") from exc

    try:
        preview = body if len(body) < 500 else body[:500] + "…"
        log.info(f"Received GraphQL response: {preview}")
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from GraphQL endpoint: {exc}") from exc

    errors = payload.get("errors")
    if errors:
        raise RuntimeError(f"GraphQL returned errors: {errors}")

    return payload.get("data", {})





def _read_response_body(response: Any, default_charset: str = "utf-8") -> str:
    raw = response.read()
    encoding = (response.headers.get("Content-Encoding") or "").lower()

    if "gzip" in encoding:
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    elif "deflate" in encoding:
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            try:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                pass

    charset = response.headers.get_content_charset() or default_charset
    try:
        return raw.decode(charset)
    except UnicodeDecodeError:
        return raw.decode(charset, errors="replace")


if __name__ == "__main__":
    main()
