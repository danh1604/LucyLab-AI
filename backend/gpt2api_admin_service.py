import base64
import hashlib
import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests


CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_SCOPE = "openid profile email offline_access"
CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
CALLBACK_HOST = "localhost"
CALLBACK_PORT = 1455
SESSION_TTL_SECONDS = 10 * 60

_token_cache: dict[tuple[str, str, str], str] = {}
_login_lock = threading.Lock()
_login_sessions: dict[str, dict] = {}
_sessions_by_state: dict[str, str] = {}
_callback_server: ThreadingHTTPServer | None = None
_callback_thread: threading.Thread | None = None


def account_config(cfg: dict) -> dict:
    return {
        "admin_base_url": _trim_right(cfg.get("gpt2api_admin_base_url") or "http://127.0.0.1:17188", "/"),
        "admin_user": cfg.get("gpt2api_admin_user") or "admin",
        "admin_password": cfg.get("gpt2api_admin_password") or "admin123",
        "codex_auth_path": cfg.get("gpt2api_codex_auth_path")
        or str(Path.home() / ".codex" / "auth.json"),
        "codex_client_id": cfg.get("gpt2api_codex_client_id") or CODEX_OAUTH_CLIENT_ID,
        "account_name_prefix": cfg.get("gpt2api_account_name_prefix") or "lucylab-codex",
    }


def list_gpt_accounts(cfg: dict) -> dict:
    data = _admin_json(cfg, "GET", "/admin/api/v1/accounts?provider=gpt&page=1&page_size=100")
    accounts = sorted(data.get("list") or [], key=lambda item: item.get("id", 0), reverse=True)
    return _accounts_response(accounts)


def select_gpt_account(cfg: dict, account_id: int) -> dict:
    return enable_gpt_account(cfg, account_id)


def enable_gpt_account(cfg: dict, account_id: int) -> dict:
    snapshot = list_gpt_accounts(cfg)
    selected = next((a for a in snapshot["accounts"] if int(a.get("id", 0)) == account_id), None)
    if not selected:
        raise RuntimeError(f"GPT account #{account_id} was not found")
    if _has_active_cooldown(selected):
        raise RuntimeError(f"GPT account #{account_id} is in cooldown until {_format_epoch(selected.get('cooldown_until'))}")
    if _has_dead_oauth(selected):
        raise RuntimeError(f"GPT account #{account_id} has an invalid OAuth token. Re-import it first.")

    _admin_json(cfg, "PUT", f"/admin/api/v1/accounts/{account_id}", {"status": 1})
    return list_gpt_accounts(cfg)


def disable_gpt_account(cfg: dict, account_id: int) -> dict:
    _admin_json(cfg, "PUT", f"/admin/api/v1/accounts/{account_id}", {"status": 0})
    return list_gpt_accounts(cfg)


def probe_gpt_account(cfg: dict, account_id: int) -> dict:
    _admin_json(cfg, "POST", f"/admin/api/v1/accounts/{account_id}/test", {})
    return list_gpt_accounts(cfg)


def delete_gpt_account(cfg: dict, account_id: int) -> dict:
    _admin_json(cfg, "DELETE", f"/admin/api/v1/accounts/{account_id}")
    return list_gpt_accounts(cfg)


def import_gpt_accounts(cfg: dict, text: str) -> dict:
    payload = parse_gpt_account_import_text(cfg, text)
    before = list_gpt_accounts(cfg)
    before_ids = {int(account.get("id", 0)) for account in before["accounts"]}

    if payload["kind"] == "create":
        created = _admin_json(cfg, "POST", "/admin/api/v1/accounts", payload["body"])
        result = {"imported": 1 if created.get("id") else 0, "skipped": 0}
    else:
        result = _admin_json(cfg, "POST", "/admin/api/v1/accounts/import", payload["body"])

    after = list_gpt_accounts(cfg)
    imported_accounts = [
        account for account in after["accounts"] if int(account.get("id", 0)) not in before_ids
    ]
    selected = next((account for account in imported_accounts if _account_is_selectable(account)), None)
    if selected:
        _admin_json(cfg, "PUT", f"/admin/api/v1/accounts/{selected['id']}", {"status": 1})
        after = list_gpt_accounts(cfg)

    return {
        **result,
        **after,
        "selectedAccountId": selected.get("id") if selected else None,
    }


def import_local_codex_account(cfg: dict) -> dict:
    tokens = read_codex_tokens_from_file(account_config(cfg)["codex_auth_path"])
    return import_codex_tokens(cfg, tokens, "local Codex auth")


def import_codex_tokens(cfg: dict, tokens: dict, source_label: str = "Codex auth") -> dict:
    if not tokens.get("accessToken") and not tokens.get("refreshToken"):
        raise RuntimeError("Codex auth file does not contain OAuth tokens. Sign in with Codex first.")

    acfg = account_config(cfg)
    account_id = tokens.get("accountId") or ""
    name_suffix = account_id[:8] if account_id else _timestamp_slug()
    body = {
        "provider": "gpt",
        "name": f"{acfg['account_name_prefix']}-{name_suffix}",
        "auth_type": "oauth",
        "access_token": tokens.get("accessToken") or "",
        "refresh_token": tokens.get("refreshToken") or "",
        "session_token": tokens.get("idToken") or "",
        "client_id": acfg["codex_client_id"],
        "weight": 10,
        "remark": f"imported from {source_label} in LucyLab",
    }
    created = _admin_json(cfg, "POST", "/admin/api/v1/accounts", body)
    created_id = created.get("id")
    if created_id:
        _admin_json(cfg, "PUT", f"/admin/api/v1/accounts/{created_id}", {"status": 1})
    after = list_gpt_accounts(cfg)
    return {
        "imported": 1 if created_id else 0,
        "skipped": 0,
        **after,
        "selectedAccountId": created_id,
    }


def start_codex_login_session(cfg: dict) -> dict:
    _cleanup_login_sessions()
    session_id = str(uuid.uuid4())
    verifier = _b64url(os.urandom(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url(os.urandom(32))
    login_url = _build_codex_auth_url(state, challenge)
    now = time.time()
    session = {
        "sessionId": session_id,
        "state": state,
        "codeVerifier": verifier,
        "status": "waiting",
        "createdAt": now,
        "expiresAt": now + SESSION_TTL_SECONDS,
        "loginUrl": login_url,
        "callbackUrl": CODEX_REDIRECT_URI,
        "message": "Waiting for Codex OAuth callback on localhost:1455.",
        "cfg": dict(cfg),
    }
    with _login_lock:
        _login_sessions[session_id] = session
        _sessions_by_state[state] = session_id
    _ensure_callback_server()
    return _session_status(session)


def get_codex_login_session_status(session_id: str) -> dict:
    _cleanup_login_sessions()
    with _login_lock:
        session = _login_sessions.get(session_id)
    if not session:
        raise RuntimeError("Codex login session was not found. Start a new login.")
    if time.time() > session["expiresAt"] and session["status"] not in {"imported", "failed"}:
        session["status"] = "timeout"
        session["message"] = "Codex OAuth login timed out. Start a new flow."
    return _session_status(session)


def parse_gpt_account_import_text(cfg: dict, text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        raise RuntimeError("Paste account data before importing.")

    parsed = _parse_json_loose(raw)
    if parsed is not None:
        if isinstance(parsed, str) and parsed.strip():
            return _line_import_payload(parsed.strip())
        accounts = _extract_sub2api_accounts(parsed)
        if accounts:
            return {
                "kind": "sub2api",
                "body": {"format": "sub2api", "provider": "gpt", "accounts": accounts, "weight": 10},
            }
        oauth = _extract_single_oauth_account(cfg, parsed)
        if oauth:
            return {"kind": "create", "body": oauth}
        raise RuntimeError("Unsupported account JSON. Paste sub2api accounts or OAuth token fields.")

    loose_session = _extract_loose_auth_session(cfg, raw)
    if loose_session:
        return {"kind": "create", "body": loose_session}
    return _line_import_payload(raw)


def read_codex_tokens_from_file(auth_path: str) -> dict:
    path = Path(os.path.expandvars(auth_path)).expanduser()
    if not path.exists():
        raise RuntimeError(f"Codex auth file was not found at {path}. Sign in with Codex first.")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Codex auth file at {path} is not valid JSON.") from exc

    source = parsed.get("tokens") if isinstance(parsed, dict) and isinstance(parsed.get("tokens"), dict) else parsed
    return {
        "accessToken": _string_value(source.get("access_token")) if isinstance(source, dict) else "",
        "refreshToken": _string_value(source.get("refresh_token")) if isinstance(source, dict) else "",
        "idToken": _string_value(source.get("id_token")) if isinstance(source, dict) else "",
        "accountId": _string_value(source.get("account_id")) if isinstance(source, dict) else "",
    }


def _admin_json(cfg: dict, method: str, path: str, body: dict | None = None) -> dict:
    acfg = account_config(cfg)
    token = _admin_token(cfg)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{acfg['admin_base_url']}{path}"
    try:
        res = requests.request(method, url, headers=headers, json=body, timeout=30)
    except requests.Timeout as exc:
        raise RuntimeError(f"gpt2api admin request timed out at {acfg['admin_base_url']}") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Cannot reach gpt2api admin at {acfg['admin_base_url']}: {exc}") from exc

    data = _response_json(res)
    if res.status_code == 401:
        _token_cache.pop((acfg["admin_base_url"], acfg["admin_user"], acfg["admin_password"]), None)
    if not res.ok or data.get("code") != 0:
        raise RuntimeError(str(data.get("detail") or data.get("msg") or f"gpt2api admin request failed: {res.status_code}"))
    return data.get("data") or {}


def _admin_token(cfg: dict) -> str:
    acfg = account_config(cfg)
    key = (acfg["admin_base_url"], acfg["admin_user"], acfg["admin_password"])
    if key in _token_cache:
        return _token_cache[key]
    url = f"{acfg['admin_base_url']}/admin/api/v1/auth/login"
    try:
        res = requests.post(
            url,
            json={"account": acfg["admin_user"], "password": acfg["admin_password"]},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Cannot login to gpt2api admin at {acfg['admin_base_url']}: {exc}") from exc

    data = _response_json(res)
    token = ((data.get("data") or {}).get("token") or {}).get("access_token")
    if not res.ok or data.get("code") != 0 or not token:
        raise RuntimeError(str(data.get("detail") or data.get("msg") or "gpt2api admin login failed"))
    _token_cache[key] = token
    return token


def _response_json(res: requests.Response) -> dict:
    try:
        data = res.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {"msg": res.text}


def _accounts_response(accounts: list[dict]) -> dict:
    active = next((account for account in accounts if _account_is_selectable(account) and account.get("status") == 1), None)
    return {
        "accounts": accounts,
        "activeAccountId": active.get("id") if active else None,
        "pool": _summarize_pool(accounts),
    }


def _summarize_pool(accounts: list[dict]) -> dict:
    enabled = [account for account in accounts if account.get("status") == 1]
    usable = [account for account in enabled if _is_usable(account)]
    return {
        "enabledCount": len(enabled),
        "usableCount": len(usable),
        "codexReadyCount": len([a for a in usable if _is_codex_oauth(a)]),
        "webReadyCount": len([a for a in usable if not _is_codex_oauth(a) and not _is_web_blocked(a)]),
        "cooldownCount": len([a for a in accounts if _has_active_cooldown(a)]),
        "invalidCount": len([a for a in accounts if _has_dead_oauth(a)]),
        "webBlockedCount": len([a for a in accounts if _is_web_blocked(a)]),
    }


def _is_usable(account: dict) -> bool:
    return account.get("status") == 1 and not _has_dead_oauth(account) and not _has_active_cooldown(account)


def _is_codex_oauth(account: dict) -> bool:
    marker = f"{account.get('name') or ''} {account.get('remark') or ''} {account.get('default_model') or ''}".lower()
    return account.get("auth_type") == "oauth" and "codex" in marker


def _account_is_selectable(account: dict) -> bool:
    return not _has_dead_oauth(account) and not _has_active_cooldown(account)


def _has_active_cooldown(account: dict) -> bool:
    cooldown = account.get("cooldown_until")
    return isinstance(cooldown, (int, float)) and cooldown > time.time()


def _has_dead_oauth(account: dict) -> bool:
    combined = f"{account.get('last_error') or ''}\n{account.get('last_test_error') or ''}".lower()
    needles = [
        "refresh token has already been used",
        "refresh_token_invalidated",
        "token_invalidated",
        "authentication token has been invalidated",
        "token has been invalidated",
        "token_revoked",
        "please try signing in again",
    ]
    return any(needle in combined for needle in needles)


def _is_web_blocked(account: dict) -> bool:
    combined = f"{account.get('last_error') or ''}\n{account.get('last_test_error') or ''}".lower()
    return "gpt image2 web bootstrap 403" in combined or "gpt image2 web requirements 403" in combined


def _line_import_payload(text: str) -> dict:
    lines = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return {
        "kind": "lines",
        "body": {"format": "lines", "provider": "gpt", "auth_type": "oauth", "text": lines, "weight": 10},
    }


def _extract_sub2api_accounts(value) -> list | None:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("accounts"), list):
        return value["accounts"]
    return None


def _extract_single_oauth_account(cfg: dict, value) -> dict | None:
    if not isinstance(value, dict):
        return None
    credentials = value.get("credentials") if isinstance(value.get("credentials"), dict) else value
    user = value.get("user") if isinstance(value.get("user"), dict) else {}
    access_token = _string_value(credentials.get("access_token") or credentials.get("accessToken"))
    refresh_token = _string_value(credentials.get("refresh_token") or credentials.get("refreshToken"))
    session_token = _string_value(
        credentials.get("session_token")
        or credentials.get("sessionToken")
        or credentials.get("id_token")
        or credentials.get("idToken")
    )
    client_id = _string_value(credentials.get("client_id") or credentials.get("clientId")) or _loose_jwt_claim(access_token, "client_id")
    if not access_token and not refresh_token:
        return None

    name = (
        _string_value(value.get("name"))
        or _string_value(user.get("email"))
        or _string_value(user.get("name"))
        or _string_value(credentials.get("email"))
        or f"chatgpt-import-{_timestamp_slug()}"
    )
    remark = "imported from Codex OAuth in LucyLab" if client_id == account_config(cfg)["codex_client_id"] else "imported from LucyLab"
    return {
        "provider": "gpt",
        "name": name,
        "auth_type": "oauth",
        "credential": refresh_token or "",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "session_token": session_token,
        "client_id": client_id,
        "weight": 10,
        "remark": remark,
    }


def _extract_loose_auth_session(cfg: dict, value: str) -> dict | None:
    access_token = _loose_json_string(value, "accessToken") or _loose_json_string(value, "access_token")
    session_token = _loose_json_string(value, "sessionToken") or _loose_json_string(value, "session_token")
    refresh_token = _loose_json_string(value, "refreshToken") or _loose_json_string(value, "refresh_token")
    if not access_token and not refresh_token:
        return None
    email = _loose_json_string(value, "email")
    name = _loose_json_string(value, "name") or email or f"chatgpt-session-{_timestamp_slug()}"
    client_id = _loose_json_string(value, "client_id") or _loose_jwt_claim(access_token, "client_id")
    remark = "imported from Codex OAuth in LucyLab" if client_id == account_config(cfg)["codex_client_id"] else "imported from ChatGPT session in LucyLab"
    return {
        "provider": "gpt",
        "name": name,
        "auth_type": "oauth",
        "credential": _compact_token(refresh_token) or "",
        "access_token": _compact_token(access_token),
        "refresh_token": _compact_token(refresh_token),
        "session_token": _compact_token(session_token),
        "client_id": client_id,
        "weight": 10,
        "remark": remark,
    }


def _parse_json_loose(value: str):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        sanitized = _sanitize_json_like_text(value)
        if sanitized == value:
            return None
        try:
            return json.loads(sanitized)
        except json.JSONDecodeError:
            return None


def _sanitize_json_like_text(value: str) -> str:
    without_comments = "\n".join(
        line for line in value.splitlines() if not line.lstrip().startswith("//")
    ).strip()
    start = without_comments.find("{")
    end = without_comments.rfind("}")
    if start < 0 or end <= start:
        return without_comments
    return without_comments[start : end + 1]


def _build_codex_auth_url(state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CODEX_OAUTH_CLIENT_ID,
        "redirect_uri": CODEX_REDIRECT_URI,
        "scope": CODEX_SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "codex_cli_rs",
        "state": state,
    }
    return f"{CODEX_AUTHORIZE_URL}?{urlencode(params)}"


def _ensure_callback_server() -> None:
    global _callback_server, _callback_thread
    if _callback_server:
        return
    try:
        _callback_server = ThreadingHTTPServer((CALLBACK_HOST, CALLBACK_PORT), _CodexCallbackHandler)
    except OSError as exc:
        raise RuntimeError("Port 1455 is already in use. Close another Codex login flow, then try again.") from exc
    _callback_thread = threading.Thread(target=_callback_server.serve_forever, daemon=True)
    _callback_thread.start()


class _CodexCallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003
        return

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in {"/auth/callback", "/callback"}:
            self._write_text(404, "Not found")
            return

        query = parse_qs(parsed.query)
        state = (query.get("state") or [""])[0]
        with _login_lock:
            session_id = _sessions_by_state.get(state)
            session = _login_sessions.get(session_id or "")
        if not session:
            self._write_html(False, "This OAuth callback does not belong to an active LucyLab Codex login flow.")
            return

        error = (query.get("error") or [""])[0]
        if error:
            session["status"] = "failed"
            session["message"] = (query.get("error_description") or [f"Codex OAuth returned {error}."])[0]
            self._write_html(False, session["message"])
            return

        code = (query.get("code") or [""])[0]
        if not code:
            session["status"] = "failed"
            session["message"] = "Codex OAuth callback did not include an authorization code."
            self._write_html(False, session["message"])
            return

        session["status"] = "logged_in"
        session["message"] = "Codex OAuth callback received. Importing account into gpt2api."
        imported = _import_session_from_code(session, code)
        self._write_html(imported, session.get("message") or "Codex OAuth completed.")

    def _write_text(self, status: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_html(self, success: bool, message: str) -> None:
        title = "Codex account connected" if success else "Codex account login failed"
        html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{_escape_html(title)}</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#09090f;color:#f8fafc;font-family:Arial,sans-serif}}main{{width:min(560px,calc(100vw - 32px));border:1px solid #3f2f73;border-radius:12px;padding:24px;background:#171420}}p{{color:#cbd5e1;line-height:1.55}}</style>
<script>setTimeout(function(){{window.close()}},1600)</script></head>
<body><main><h1>{_escape_html(title)}</h1><p>{_escape_html(message)}</p></main></body></html>"""
        body = html.encode("utf-8")
        self.send_response(200 if success else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _import_session_from_code(session: dict, code: str) -> bool:
    try:
        token = _exchange_codex_code(code, session["codeVerifier"])
        if not token.get("access_token") and not token.get("refresh_token"):
            raise RuntimeError("Codex token exchange completed but did not return OAuth tokens.")
        claims = _decode_openai_id_token_claims(token.get("id_token") or "")
        result = import_codex_tokens(
            session["cfg"],
            {
                "accessToken": token.get("access_token") or "",
                "refreshToken": token.get("refresh_token") or "",
                "idToken": token.get("id_token") or "",
                "accountId": claims.get("chatgptAccountId") or "",
            },
            f"Codex OAuth login ({claims['email']})" if claims.get("email") else "Codex OAuth login",
        )
        session["status"] = "imported"
        session["accountId"] = result.get("selectedAccountId")
        session["snapshot"] = {
            "accounts": result.get("accounts") or [],
            "activeAccountId": result.get("activeAccountId"),
            "pool": result.get("pool"),
        }
        session["message"] = (
            f"Imported Codex OAuth account #{session['accountId']}."
            if session.get("accountId")
            else "Imported Codex OAuth account."
        )
        return True
    except Exception as exc:
        session["status"] = "failed"
        session["message"] = str(exc)
        return False


def _exchange_codex_code(code: str, verifier: str) -> dict:
    res = requests.post(
        CODEX_TOKEN_URL,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "client_id": CODEX_OAUTH_CLIENT_ID,
            "code": code,
            "redirect_uri": CODEX_REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    data = _response_json(res)
    if not res.ok:
        raise RuntimeError(f"Codex token exchange failed: {data.get('error_description') or data.get('error') or res.text}")
    return data


def _session_status(session: dict) -> dict:
    snapshot = session.get("snapshot") or {}
    return {
        "sessionId": session.get("sessionId"),
        "status": session.get("status"),
        "loginUrl": session.get("loginUrl"),
        "callbackUrl": session.get("callbackUrl"),
        "message": session.get("message"),
        "accountId": session.get("accountId"),
        "accounts": snapshot.get("accounts"),
        "activeAccountId": snapshot.get("activeAccountId"),
        "pool": snapshot.get("pool"),
    }


def _cleanup_login_sessions() -> None:
    now = time.time()
    with _login_lock:
        for session_id, session in list(_login_sessions.items()):
            if now > session.get("expiresAt", 0) and session.get("status") not in {"imported", "failed"}:
                session["status"] = "timeout"
                session["message"] = "Codex OAuth login timed out. Start a new flow."
            if session.get("status") in {"imported", "failed", "timeout"} and now - session.get("expiresAt", now) > 60:
                _login_sessions.pop(session_id, None)
                _sessions_by_state.pop(session.get("state") or "", None)


def _decode_openai_id_token_claims(id_token: str) -> dict:
    parts = id_token.strip().split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except Exception:
        return {}
    auth = payload.get("https://api.openai.com/auth") if isinstance(payload, dict) else {}
    auth = auth if isinstance(auth, dict) else {}
    return {
        "email": _string_value(payload.get("email")) if isinstance(payload, dict) else "",
        "chatgptAccountId": _string_value(auth.get("chatgpt_account_id")),
        "chatgptPlanType": _string_value(auth.get("chatgpt_plan_type")),
    }


def _loose_json_string(value: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([\s\S]*?)"', value, re.IGNORECASE)
    return match.group(1).replace('\\"', '"').strip() if match else ""


def _loose_jwt_claim(token: str, claim: str) -> str:
    parts = _compact_token(token).split(".")
    if len(parts) < 2:
        return ""
    try:
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except Exception:
        return ""
    return _string_value(payload.get(claim)) if isinstance(payload, dict) else ""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = value + ("=" * ((4 - len(value) % 4) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _compact_token(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _string_value(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _timestamp_slug() -> str:
    return time.strftime("%Y%m%d%H%M")


def _format_epoch(epoch_seconds) -> str:
    try:
        return time.strftime("%d/%m %H:%M", time.localtime(float(epoch_seconds)))
    except Exception:
        return "reset time"


def _trim_right(value: str, char: str) -> str:
    out = str(value or "").strip()
    while out.endswith(char):
        out = out[:-1]
    return out


def _escape_html(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
