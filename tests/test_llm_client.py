"""The OpenAI compatible client against a local stub server: success, retry on 429, empty answer, key handling."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from stepchaser.llm import LLMError, OpenAICompatLLM


class Stub:
    def __init__(self, script):
        self.script, self.requests = list(script), []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append((self.headers.get("Authorization"), body))
                code, payload, headers = stub.script.pop(0) if stub.script else (200, {"choices": [{"message": {"content": "{}"}}]}, {})
                data = json.dumps(payload).encode()
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"

    def close(self):
        self.srv.shutdown()


def ok(text):
    return (200, {"choices": [{"message": {"content": text}}]}, {})


@pytest.fixture
def stub():
    made = []

    def make(script):
        s = Stub(script)
        made.append(s)
        return s
    yield make
    [s.close() for s in made]


def test_success_sends_the_key_and_a_json_mode_request(stub):
    s = stub([ok('{"a": 1}')])
    out = OpenAICompatLLM(s.url, "secret-key-123", "some/model").complete("sys", "user")
    auth, body = s.requests[0]
    assert out == '{"a": 1}' and auth == "Bearer secret-key-123"
    assert body["response_format"] == {"type": "json_object"} and body["temperature"] == 0 and body["messages"][0]["role"] == "system"


def test_a_429_is_retried_after_the_wait_it_names(stub):
    s = stub([(429, {"error": "slow down"}, {"retry-after": "0"}), ok("fine")])
    assert OpenAICompatLLM(s.url, "k", "m").complete("s", "u") == "fine" and len(s.requests) == 2


def test_an_empty_answer_is_an_error_not_a_blank_reply(stub):
    s = stub([ok("   ")])
    with pytest.raises(LLMError, match="empty"):
        OpenAICompatLLM(s.url, "k", "m", retries=0).complete("s", "u")


def test_a_client_error_is_named_and_never_carries_the_key(stub):
    s = stub([(401, {"error": "bad key"}, {})])
    with pytest.raises(LLMError) as exc:
        OpenAICompatLLM(s.url, "top-secret-value", "m").complete("s", "u")
    assert "401" in str(exc.value) and "top-secret-value" not in str(exc.value)


def test_gives_up_after_the_retries(stub):
    s = stub([(503, {}, {"retry-after": "0"})] * 5)
    with pytest.raises(LLMError, match="503"):
        OpenAICompatLLM(s.url, "k", "m", retries=2).complete("s", "u")
    assert len(s.requests) == 3


def test_no_key_is_refused_up_front_and_env_selection():
    with pytest.raises(LLMError):
        OpenAICompatLLM.from_env(env={})
    llm = OpenAICompatLLM.from_env(env={"LLM_API_KEY": "k", "LLM_BASE_URL": "http://x/v1/", "LLM_MODEL": "m"})
    assert llm.base_url == "http://x/v1" and llm.model == "m"
    assert OpenAICompatLLM.from_env(env={"GROQ_API_KEY": "g"}).base_url.startswith("https://api.groq.com")
