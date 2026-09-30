"""Remote connector (GUNSTORE_MCP_TRANSPORT=http): OAuth resource server whose
authorization server is the POS. No key on the server; the user's own bearer
token is verified against the POS and forwarded on every call."""
from __future__ import annotations

import asyncio
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


class Verifier(unittest.TestCase):
	def _verify(self, v, token):
		return asyncio.run(v.verify_token(token))

	def test_signed_in_user_is_accepted_and_cached(self):
		v = auth.FrappeTokenVerifier("https://pos.example.com")
		with patch.object(auth.requests, "get", return_value=_resp(200, {"message": "cpa@x.com"})) as get:
			tok = self._verify(v, "T1")
			again = self._verify(v, "T1")
		self.assertEqual((tok.client_id, tok.token), ("cpa@x.com", "T1"))
		self.assertEqual(again.client_id, "cpa@x.com")
		get.assert_called_once()
		self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer T1")
		self.assertNotIn("T1", repr(v._cache), "the cache must not hold the token")

	def test_rejections_are_cached_briefly_but_network_errors_are_not(self):
		v = auth.FrappeTokenVerifier("https://pos.example.com")
		with patch.object(auth.requests, "get", return_value=_resp(401, {})) as get:
			self.assertIsNone(self._verify(v, "BOGUS"))
			self.assertIsNone(self._verify(v, "BOGUS"))
		get.assert_called_once()
		with patch.object(auth.requests, "get", side_effect=auth.requests.ConnectionError()) as get:
			self.assertIsNone(self._verify(v, "OK-LATER"))
		with patch.object(auth.requests, "get", return_value=_resp(200, {"message": "u@x.com"})):
			self.assertEqual(self._verify(v, "OK-LATER").client_id, "u@x.com")

	def test_expired_entries_are_pruned(self):
		v = auth.FrappeTokenVerifier("https://pos.example.com")
		from types import SimpleNamespace
		clock = SimpleNamespace(now=0)
		with patch.object(auth.requests, "get", return_value=_resp(200, {"message": "u@x.com"})), \
				patch.object(auth, "time", SimpleNamespace(monotonic=lambda: clock.now)):
			self._verify(v, "OLD")
			clock.now = 1000
			self._verify(v, "NEW")
		self.assertEqual(len(v._cache), 1, "the refreshed-away token must not linger")

	def test_guest_bad_status_and_network_errors_are_rejected(self):
		v = auth.FrappeTokenVerifier("https://pos.example.com")
		for side in (_resp(200, {"message": "Guest"}), _resp(401, {}), _resp(200, {}),
				auth.requests.ConnectionError("down")):
			kw = {"side_effect": side} if isinstance(side, Exception) else {"return_value": side}
			with patch.object(auth.requests, "get", **kw):
				self.assertIsNone(self._verify(v, "T2"), side)
		self.assertIsNone(self._verify(v, ""))


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
		self.assertEqual(client.session.headers["Authorization"], "Bearer USER-TOKEN")
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
	"""The real Starlette app FastMCP builds in http mode."""

	def _mcp(self, extra=None):
		from gunstore_mcp import server
		with _env({**HTTP_ENV, "GUNSTORE_MCP_MODE": "cpa", **(extra or {})}):
			return server.build()

	def _app(self):
		from starlette.testclient import TestClient
		return TestClient(self._mcp().streamable_http_app(), base_url="https://mcp.example.com")

	def test_each_request_forwards_its_own_token(self):
		"""Stateless: a refreshed token is used on the very next call, and a call is
		never served with an earlier request's token."""
		from starlette.testclient import TestClient
		mcp = self._mcp()
		sent = []

		def fake_request(self_, method, url, **kw):
			sent.append(self_.headers["Authorization"])
			return _resp(200, {"data": []})

		users = {"TOKEN-A": "a@x.com", "TOKEN-B": "b@x.com"}
		with patch.object(auth.FrappeTokenVerifier, "_logged_user", lambda s, t: users.get(t, "")), \
				patch.object(frappe_client.requests.Session, "request", fake_request), \
				_env({**HTTP_ENV, "GUNSTORE_MCP_MODE": "cpa"}):
			with TestClient(mcp.streamable_http_app(), base_url="https://mcp.example.com") as c:
				for tok in ("TOKEN-A", "TOKEN-B"):
					r = c.post("/mcp", headers={"Authorization": f"Bearer {tok}",
						"Accept": "application/json, text/event-stream"},
						json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
							"name": "frappe_list_documents", "arguments": {"doctype": "Company"}}})
					self.assertEqual(r.status_code, 200, r.text)
		self.assertEqual(sent, ["Bearer TOKEN-A", "Bearer TOKEN-B"])

	def test_threaded_tools_keep_their_schemas(self):
		import asyncio as aio

		from gunstore_mcp import server
		from mcp.server.fastmcp import FastMCP
		plain, threaded = FastMCP("p"), FastMCP("t")
		with _env({"GUNSTORE_MCP_MODE": "cpa"}):
			server.register_tools(plain, "cpa")
			server.register_tools(server.ThreadedMCP(threaded), "cpa")
		a = {t.name: t.inputSchema for t in aio.run(plain.list_tools())}
		b = {t.name: t.inputSchema for t in aio.run(threaded.list_tools())}
		self.assertEqual(a, b)

	def test_action_gates_refuse_to_start_remotely(self):
		for gate in ("GUNSTORE_MCP_DISTRIBUTOR_ACTIONS", "GUNSTORE_MCP_GUNBROKER_ACTIONS"):
			with patch("gunstore_mcp.tools.distributor._load_env", lambda: None), \
					patch("gunstore_mcp.tools.curated._load_env", lambda: None):
				with self.assertRaises(RuntimeError, msg=gate):
					self._mcp({gate: "1"})

	def test_unauthenticated_call_points_at_the_resource_metadata(self):
		client = self._app()
		r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
		self.assertEqual(r.status_code, 401)
		self.assertIn("https://mcp.example.com/.well-known/oauth-protected-resource/osa/cpa/mcp",
			r.headers.get("www-authenticate", ""))

	def test_resource_metadata_names_the_pos_as_authorization_server(self):
		client = self._app()
		r = client.get("/.well-known/oauth-protected-resource/osa/cpa/mcp")
		self.assertEqual(r.status_code, 200)
		body = r.json()
		self.assertEqual(body["resource"], "https://mcp.example.com/osa/cpa/mcp")
		self.assertEqual([s.rstrip("/") for s in body["authorization_servers"]],
			["https://pos.example.com"])

	def test_proxied_host_header_is_accepted(self):
		client = self._app()
		with patch.object(auth.requests, "get", return_value=_resp(401, {})):
			r = client.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
		# 401 from auth, not 421 from DNS-rebinding protection: the proxy's Host passes.
		self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
	unittest.main()
