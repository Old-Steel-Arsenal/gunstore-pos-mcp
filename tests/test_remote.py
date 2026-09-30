"""Remote connector (GUNSTORE_MCP_TRANSPORT=http): OAuth resource server whose
authorization server is the POS. No key on the server; the user's own bearer
token is verified against the POS and forwarded on every call."""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from gunstore_mcp import auth, config, frappe_client
from gunstore_mcp.tools import curated

HTTP_ENV = {
	"GUNSTORE_MCP_TRANSPORT": "http",
	"FRAPPE_BASE_URL": "https://pos.example.com",
	"GUNSTORE_MCP_PUBLIC_URL": "https://mcp.example.com/osa/cpa/mcp",
	"GUNSTORE_MCP_PORT": "8781",
}


def _env(extra=None, drop=()):
	env = {k: v for k, v in os.environ.items()
		if not k.startswith(("GUNSTORE_MCP_", "FRAPPE_")) and k not in drop}
	env.update(extra or {})
	return patch.dict(os.environ, env, clear=True)


class _Fresh:
	"""Config is cached per process; each test resolves it anew, with the
	developer's own .env out of the picture."""

	def setUp(self):
		self._p = [patch.object(config, "_config", None),
			patch.object(config, "_load_env", lambda: None)]
		for p in self._p:
			p.start()
			self.addCleanup(p.stop)


def _resp(status, body):
	r = MagicMock(status_code=status)
	r.json.return_value = body
	return r


def _me(user="cpa@x.com", connector=True):
	return _resp(200, {"message": {"user": user, "connector": connector, "client_name": "Claude"}})


class Verifier(unittest.TestCase):
	def _verify(self, v, token):
		return asyncio.run(v.verify_token(token))

	def test_connector_user_is_accepted_and_cached(self):
		v = auth.FrappeTokenVerifier("http://127.0.0.1:8080", "pos.example.com")
		with patch.object(auth.requests, "get", return_value=_me()) as get:
			tok = self._verify(v, "T1")
			again = self._verify(v, "T1")
		self.assertEqual((tok.client_id, tok.token), ("cpa@x.com", "T1"))
		self.assertEqual(again.client_id, "cpa@x.com")
		get.assert_called_once()
		headers = get.call_args.kwargs["headers"]
		self.assertEqual((headers["Authorization"], headers["Host"]), ("Bearer T1", "pos.example.com"))
		self.assertIn("connector_identity", get.call_args.args[0])
		self.assertNotIn("T1", repr(v._cache), "the cache must not hold the token")

	def test_tokens_of_non_connector_apps_and_guests_are_refused(self):
		# #9: a token the POS issued to an OAuth app created in Desk never drives the MCP.
		v = auth.FrappeTokenVerifier("https://pos.example.com")
		for resp in (_me(connector=False), _me(user="Guest"), _resp(403, {}), _resp(401, {})):
			with patch.object(auth.requests, "get", return_value=resp):
				self.assertIsNone(self._verify(v, f"T-{id(resp)}"))
		self.assertIsNone(self._verify(v, ""))

	def test_pos_without_a_verdict_is_not_a_rejection(self):
		"""A POS restart (5xx), rate limit, non-JSON page or network error raises —
		nothing cached, no invalid_token that would make the client drop its token."""
		v = auth.FrappeTokenVerifier("https://pos.example.com")
		bad_json = _resp(200, None)
		bad_json.json.side_effect = ValueError()
		for kw in ({"return_value": _resp(502, {})}, {"return_value": _resp(429, {})},
				{"return_value": bad_json}, {"side_effect": auth.requests.ConnectionError()}):
			with patch.object(auth.requests, "get", **kw):
				with self.assertRaises(auth.AuthUnavailable):
					self._verify(v, "VALID")
		with patch.object(auth.requests, "get", return_value=_me()):
			self.assertEqual(self._verify(v, "VALID").client_id, "cpa@x.com")

	def test_rejections_are_cached_briefly(self):
		v = auth.FrappeTokenVerifier("https://pos.example.com")
		with patch.object(auth.requests, "get", return_value=_resp(401, {})) as get:
			self.assertIsNone(self._verify(v, "BOGUS"))
			self.assertIsNone(self._verify(v, "BOGUS"))
		get.assert_called_once()

	def test_cache_is_bounded(self):
		v = auth.FrappeTokenVerifier("https://pos.example.com")
		with patch.object(auth, "CACHE_MAX", 3), \
				patch.object(auth.requests, "get", return_value=_resp(401, {})):
			for i in range(10):
				self._verify(v, f"FLOOD-{i}")
		self.assertEqual(len(v._cache), 3)


class Config(_Fresh, unittest.TestCase):
	def test_http_ignores_any_api_key(self):
		with _env({**HTTP_ENV, "FRAPPE_API_KEY": "k", "FRAPPE_API_SECRET": "s"}):
			cfg = config.get_config()
		self.assertEqual((cfg.transport, cfg.api_key, cfg.api_secret, cfg.port),
			("http", "", "", 8781))

	def test_http_requires_an_https_mcp_url_and_a_port(self):
		for bad in ({"GUNSTORE_MCP_PUBLIC_URL": "http://mcp.example.com/osa/cpa/mcp"},
				{"GUNSTORE_MCP_PUBLIC_URL": "https://mcp.example.com/osa/cpa"},
				{"GUNSTORE_MCP_PORT": ""}):
			with _env({**HTTP_ENV, **bad}), patch.object(config, "_config", None):
				with self.assertRaises(RuntimeError, msg=bad):
					config.get_config()

	def test_the_pos_url_must_be_https_too(self):
		# The user's token is sent to FRAPPE_BASE_URL on every call.
		with _env({**HTTP_ENV, "FRAPPE_BASE_URL": "http://pos.example.com"}):
			with self.assertRaises(RuntimeError):
				config.get_config()

	def test_plain_http_only_for_localhost(self):
		with _env({**HTTP_ENV, "GUNSTORE_MCP_PUBLIC_URL": "http://localhost:8781/mcp"}):
			self.assertEqual(config.get_config().public_url, "http://localhost:8781/mcp")

	def test_unknown_transport_refuses_to_start(self):
		with _env({"GUNSTORE_MCP_TRANSPORT": "sse"}):
			with self.assertRaises(RuntimeError):
				config.get_transport()


class Client(_Fresh, unittest.TestCase):
	def test_no_token_never_falls_back(self):
		with _env(HTTP_ENV), patch("mcp.server.auth.middleware.auth_context.get_access_token",
				return_value=None):
			with self.assertRaises(frappe_client.RemoteAuthMissing):
				frappe_client.get_client()

	def test_user_token_is_forwarded(self):
		tok = MagicMock(token="USER-TOKEN")
		with _env(HTTP_ENV), patch("mcp.server.auth.middleware.auth_context.get_access_token",
				return_value=tok):
			client = frappe_client.get_client()
		self.assertEqual(client._headers["Authorization"], "Bearer USER-TOKEN")
		self.assertNotIn("Authorization", client.session.headers,
			"the shared pool must never carry a user's token")
		with self.assertRaises(frappe_client.RemoteAuthMissing):
			client.upload_file("/etc/passwd")


class _FakeMCP:
	def __init__(self):
		self.tools = {}

	def tool(self, *a, **k):
		def deco(fn):
			self.tools[fn.__name__] = fn
			return fn
		return deco


class Surface(_Fresh, unittest.TestCase):
	def _names(self, env):
		mcp = _FakeMCP()
		with _env(env), patch.object(curated, "_load_env", lambda: None):
			curated.register(mcp)
		return set(mcp.tools)

	def test_upload_attachment_is_never_registered_remotely(self):
		self.assertIn("upload_attachment", self._names({}))
		self.assertNotIn("upload_attachment", self._names(HTTP_ENV))


class HttpApp(_Fresh, unittest.TestCase):
	"""The real ASGI app the remote server serves (server.http_app)."""

	def _mcp(self, extra=None):
		from gunstore_mcp import server
		with _env({**HTTP_ENV, "GUNSTORE_MCP_MODE": "cpa", **(extra or {})}):
			return server.build()

	def _client(self, mcp=None, extra=None):
		from starlette.testclient import TestClient

		from gunstore_mcp import server
		mcp = mcp or self._mcp(extra)
		with _env({**HTTP_ENV, "GUNSTORE_MCP_MODE": "cpa", **(extra or {})}):
			app = server.http_app(mcp)
		return TestClient(app, base_url="https://mcp.example.com")

	def test_unauthenticated_call_points_at_the_resource_metadata(self):
		r = self._client().post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
		self.assertEqual(r.status_code, 401)
		self.assertIn("https://mcp.example.com/.well-known/oauth-protected-resource/osa/cpa/mcp",
			r.headers.get("www-authenticate", ""))

	def test_resource_metadata_names_the_issuer_exactly(self):
		"""No trailing slash: it must equal the issuer the POS itself reports."""
		r = self._client().get("/.well-known/oauth-protected-resource/osa/cpa/mcp")
		self.assertEqual(r.status_code, 200)
		self.assertEqual(r.json(), {"resource": "https://mcp.example.com/osa/cpa/mcp",
			"authorization_servers": ["https://pos.example.com"],
			"bearer_methods_supported": ["header"]})

	def test_proxied_host_header_is_accepted(self):
		with patch.object(auth.requests, "get", return_value=_resp(401, {})):
			r = self._client().post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
		# 401 from auth, not 421 from DNS-rebinding protection: the proxy's Host passes.
		self.assertEqual(r.status_code, 401)

	def _call(self, tokens, audit_fail=False):
		"""tools/call once per token; returns (POS tool calls, audit posts, responses)."""
		from starlette.testclient import TestClient

		from gunstore_mcp import server
		mcp = self._mcp()
		tool_calls, audit_posts = [], []

		def fake_request(self_, method, url, **kw):
			tool_calls.append(kw["headers"]["Authorization"])
			return _resp(200, {"data": [{"name": "C1"}]})

		def fake_post(self_, url, data=None, headers=None, **kw):
			body = json.loads(data)
			audit_posts.append((headers["Authorization"], body["phase"], body["tool"]))
			if audit_fail:
				return _resp(500, {})
			return _resp(200, {"message": {"log": "ACT-1"}})

		users = {"TOKEN-A": "a@x.com", "TOKEN-B": "b@x.com"}
		out = []
		with patch.object(auth.FrappeTokenVerifier, "_identity", lambda s, t: users.get(t, "")), \
				patch.object(frappe_client.requests.Session, "request", fake_request), \
				patch.object(frappe_client.requests.Session, "post", fake_post), \
				_env({**HTTP_ENV, "GUNSTORE_MCP_MODE": "cpa"}):
			with TestClient(server.http_app(mcp), base_url="https://mcp.example.com") as c:
				for tok in tokens:
					r = c.post("/mcp", headers={"Authorization": f"Bearer {tok}",
						"Accept": "application/json, text/event-stream"},
						json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
							"name": "frappe_list_documents", "arguments": {"doctype": "Company"}}})
					out.append(r)
		return tool_calls, audit_posts, out

	def test_each_request_forwards_its_own_token_and_is_audited(self):
		tool_calls, audit_posts, out = self._call(["TOKEN-A", "TOKEN-B"])
		for r in out:
			self.assertEqual(r.status_code, 200, r.text)
		self.assertEqual(tool_calls, ["Bearer TOKEN-A", "Bearer TOKEN-B"])
		self.assertEqual(audit_posts, [
			("Bearer TOKEN-A", "started", "frappe_list_documents"),
			("Bearer TOKEN-A", "ok", "frappe_list_documents"),
			("Bearer TOKEN-B", "started", "frappe_list_documents"),
			("Bearer TOKEN-B", "ok", "frappe_list_documents")])

	def test_a_call_that_cannot_be_recorded_does_not_run(self):
		tool_calls, audit_posts, out = self._call(["TOKEN-A"], audit_fail=True)
		self.assertEqual(tool_calls, [], "the tool must not touch the POS unrecorded")
		self.assertIn("audit record", out[0].text)

	def test_threaded_tools_keep_their_schemas_on_both_surfaces(self):
		import asyncio as aio

		from gunstore_mcp import server
		from mcp.server.fastmcp import FastMCP
		for mode in ("cpa", "full"):
			plain, threaded = FastMCP("p"), FastMCP("t")
			with _env({"GUNSTORE_MCP_MODE": mode}):
				server.register_tools(plain, mode)
				server.register_tools(server.ThreadedMCP(threaded, mode), mode)
			a = {t.name: t.inputSchema for t in aio.run(plain.list_tools())}
			b = {t.name: t.inputSchema for t in aio.run(threaded.list_tools())}
			self.assertEqual(a, b, mode)

	def test_action_gates_refuse_to_start_remotely(self):
		for gate in ("GUNSTORE_MCP_DISTRIBUTOR_ACTIONS", "GUNSTORE_MCP_GUNBROKER_ACTIONS"):
			with patch("gunstore_mcp.tools.distributor._load_env", lambda: None), \
					patch("gunstore_mcp.tools.curated._load_env", lambda: None):
				with self.assertRaises(RuntimeError, msg=gate):
					self._mcp({gate: "1"})


class Listen(_Fresh, unittest.TestCase):
	def test_listen_host_and_internal_backend(self):
		with _env({**HTTP_ENV, "GUNSTORE_MCP_HOST": "0.0.0.0",
				"FRAPPE_INTERNAL_URL": "http://127.0.0.1:8080"}):
			cfg = config.get_config()
		self.assertEqual((cfg.host, cfg.backend_url, cfg.backend_host, cfg.base_url),
			("0.0.0.0", "http://127.0.0.1:8080", "pos.example.com", "https://pos.example.com"))

	def test_internal_backend_must_be_loopback(self):
		with _env({**HTTP_ENV, "FRAPPE_INTERNAL_URL": "http://10.0.0.5:8080"}):
			with self.assertRaises(RuntimeError):
				config.get_config()


if __name__ == "__main__":
	unittest.main()
