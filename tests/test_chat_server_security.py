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
