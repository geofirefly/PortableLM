import importlib.util
import io
import pathlib


def load_chat_server():
    module_path = pathlib.Path(__file__).resolve().parents[1] / "Shared" / "chat_server.py"
    spec = importlib.util.spec_from_file_location("chat_server_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeHandler:
    def __init__(self, module):
        self.module = module
        self.responses = []
        self.headers = []
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.responses.append(code)

    def send_header(self, key, value):
        self.headers.append((key, value))

    def end_headers(self):
        pass

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")


def test_static_server_rejects_path_traversal(tmp_path, monkeypatch):
    chat_server = load_chat_server()
    shared = tmp_path / "Shared"
    shared.mkdir()
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("do not expose", encoding="utf-8")
    monkeypatch.setattr(chat_server, "SCRIPT_DIR", str(shared))

    handler = FakeHandler(chat_server)
    chat_server.ChatHandler._serve_static(handler, "/../outside-secret.txt")

    assert handler.responses == [403]
    assert b"do not expose" not in handler.wfile.getvalue()


def test_static_server_serves_files_inside_shared(tmp_path, monkeypatch):
    chat_server = load_chat_server()
    shared = tmp_path / "Shared"
    vendor = shared / "vendor"
    vendor.mkdir(parents=True)
    asset = vendor / "asset.txt"
    asset.write_text("safe asset", encoding="utf-8")
    monkeypatch.setattr(chat_server, "SCRIPT_DIR", str(shared))

    handler = FakeHandler(chat_server)
    chat_server.ChatHandler._serve_static(handler, "/vendor/asset.txt")

    assert handler.responses == [200]
    assert handler.wfile.getvalue() == b"safe asset"


def test_default_bind_host_is_loopback():
    chat_server = load_chat_server()

    assert chat_server._server_bind_host([]) == "127.0.0.1"


def test_lan_flag_is_required_for_all_interfaces():
    chat_server = load_chat_server()

    assert chat_server._server_bind_host(["--lan"]) == "0.0.0.0"


def test_authorization_header_is_not_added_to_log_context():
    chat_server = load_chat_server()

    class HeaderCarrier:
        command = "POST"
        path = "/ollama/api/chat"
        client_address = ("127.0.0.1", 12345)
        headers = {
            "User-Agent": "pytest",
            "Authorization": "Bearer should-not-be-logged",
        }

    context = chat_server.ChatHandler._build_request_context(HeaderCarrier(), "test")

    assert context["request_headers"] == {"User-Agent": "pytest"}



def test_sensitive_shared_paths_are_not_served_as_static_files(tmp_path, monkeypatch):
    chat_server = load_chat_server()
    shared = tmp_path / "Shared"
    chat_data = shared / "chat_data"
    logs = shared / "logs"
    models = shared / "models"
    bin_dir = shared / "bin"
    for directory in (chat_data, logs, models, bin_dir):
        directory.mkdir(parents=True)
    (chat_data / "chats.json").write_text("[]", encoding="utf-8")
    (chat_data / "settings.json").write_text("{}", encoding="utf-8")
    (logs / "chat_server.log").write_text("log", encoding="utf-8")
    (models / "model.gguf").write_text("model", encoding="utf-8")
    (bin_dir / "ollama-linux").write_text("binary", encoding="utf-8")
    monkeypatch.setattr(chat_server, "SCRIPT_DIR", str(shared))

    for request_path in (
        "/chat_data/chats.json",
        "/chat_data/settings.json",
        "/logs/chat_server.log",
        "/models/model.gguf",
        "/bin/ollama-linux",
    ):
        handler = FakeHandler(chat_server)
        chat_server.ChatHandler._serve_static(handler, request_path)
        assert handler.responses == [403]
        assert handler.wfile.getvalue() == b""


def test_default_cors_does_not_allow_arbitrary_origins(monkeypatch):
    chat_server = load_chat_server()
    monkeypatch.delenv("PORTABLELM_ALLOW_ORIGIN", raising=False)

    handler = FakeHandler(chat_server)
    chat_server.ChatHandler._cors_headers(handler)

    assert ("Access-Control-Allow-Origin", "*") not in handler.headers
    assert ("Access-Control-Allow-Origin", f"http://127.0.0.1:{chat_server.CHAT_SERVER_PORT}") in handler.headers


def test_sensitive_post_requires_access_token(monkeypatch):
    chat_server = load_chat_server()
    monkeypatch.setattr(chat_server, "ACCESS_TOKEN", "expected-token")

    class UnauthorizedHandler(FakeHandler):
        path = "/api/chats"
        headers = {}

    handler = UnauthorizedHandler(chat_server)
    assert chat_server.ChatHandler._require_access_token(handler) is False
    assert handler.responses == [403]


def test_sensitive_post_accepts_access_token_query_param(monkeypatch):
    chat_server = load_chat_server()
    monkeypatch.setattr(chat_server, "ACCESS_TOKEN", "expected-token")

    class AuthorizedHandler(FakeHandler):
        path = "/api/chats?access_token=expected-token"
        headers = {}

    handler = AuthorizedHandler(chat_server)
    assert chat_server.ChatHandler._require_access_token(handler) is True
    assert handler.responses == []


def test_sensitive_post_accepts_bearer_access_token(monkeypatch):
    chat_server = load_chat_server()
    monkeypatch.setattr(chat_server, "ACCESS_TOKEN", "expected-token")

    class AuthorizedHandler(FakeHandler):
        path = "/api/chats"

    handler = AuthorizedHandler(chat_server)
    handler.headers = {"Authorization": "Bearer expected-token"}
    assert chat_server.ChatHandler._require_access_token(handler) is True
    assert handler.responses == []


def test_browser_url_includes_access_token(monkeypatch):
    chat_server = load_chat_server()
    opened = []
    monkeypatch.setattr(chat_server, "ACCESS_TOKEN", "abc123")
    monkeypatch.setattr(chat_server.time, "sleep", lambda _: None)
    monkeypatch.setattr(chat_server.webbrowser, "open", opened.append)

    chat_server.open_browser_delayed()

    assert opened == [f"http://localhost:{chat_server.CHAT_SERVER_PORT}/?access_token=abc123"]


def test_windows_quotes_python_command():
    script = pathlib.Path(__file__).resolve().parents[1] / "Windows" / "start-fast-chat.bat"
    content = script.read_text(encoding="utf-8")

    assert '"%PYTHON_CMD%" "%~dp0..\\Shared\\chat_server.py" %*' in content


def test_mac_start_script_forwards_arguments():
    script = pathlib.Path(__file__).resolve().parents[1] / "Mac" / "start.command"
    content = script.read_text(encoding="utf-8")

    assert 'python3 "$SHARED_DIR/chat_server.py" "$@"' in content
    assert 'python "$SHARED_DIR/chat_server.py" "$@"' in content



def test_public_api_metadata_endpoints_require_access_token(monkeypatch):
    chat_server = load_chat_server()
    monkeypatch.setattr(chat_server, "ACCESS_TOKEN", "expected-token")

    class UnauthorizedHandler(FakeHandler):
        headers = {}

    for request_path in ("/api/stats", "/api/engine-status", "/api/image-models"):
        handler = UnauthorizedHandler(chat_server)
        handler.path = request_path
        handler._require_access_token = lambda h=handler: chat_server.ChatHandler._require_access_token(h)
        chat_server.ChatHandler.do_GET(handler)
        assert handler.responses == [403]


def test_cors_headers_include_referrer_policy(monkeypatch):
    chat_server = load_chat_server()
    monkeypatch.delenv("PORTABLELM_ALLOW_ORIGIN", raising=False)

    handler = FakeHandler(chat_server)
    chat_server.ChatHandler._cors_headers(handler)

    assert ("Referrer-Policy", "no-referrer") in handler.headers


def test_portablelm_bearer_token_is_not_forwarded_to_ollama(monkeypatch):
    chat_server = load_chat_server()
    monkeypatch.setattr(chat_server, "ACCESS_TOKEN", "expected-token")

    class AuthorizedHandler(FakeHandler):
        path = "/ollama/api/tags"

    handler = AuthorizedHandler(chat_server)
    handler.headers = {"Authorization": "Bearer expected-token"}
    assert chat_server.ChatHandler._require_access_token(handler) is True
    assert getattr(handler, "_portablelm_auth_header_consumed", False) is True


def test_network_access_url_includes_access_token(monkeypatch, capsys):
    chat_server = load_chat_server()
    monkeypatch.setattr(chat_server, "ACCESS_TOKEN", "abc123")
    monkeypatch.setattr(chat_server, "_set_active_log_mode", lambda _: None)
    monkeypatch.setattr(chat_server, "_load_settings_file", lambda: {"logMode": "errors_only"})
    monkeypatch.setattr(chat_server, "ensure_data_dir", lambda: None)
    monkeypatch.setattr(chat_server, "_server_bind_host", lambda argv=None: "0.0.0.0")

    class StopServer(Exception):
        pass

    class FakeServer:
        def __init__(self, *args, **kwargs):
            pass
        def serve_forever(self):
            raise StopServer()
        def shutdown(self):
            pass
        def server_close(self):
            pass

    monkeypatch.setattr(chat_server, "ThreadedHTTPServer", FakeServer)
    monkeypatch.setattr(chat_server.threading, "Thread", lambda *args, **kwargs: type("T", (), {"start": lambda self: None})())

    try:
        chat_server.main()
    except StopServer:
        pass

    assert "/?access_token=abc123" in capsys.readouterr().out
