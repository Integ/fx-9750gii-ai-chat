"""Chat with an OpenAI model through the Chat Completions REST API (Python standard library only).

Same interface as aichat_bridge.ClaudeChat: ask(question) -> reply text, reset().
The API key comes from OPENAI_API_KEY; OPENAI_MODEL and OPENAI_BASE_URL override the defaults.
"""

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_EFFORT = "low"  # gpt-6-astra / gpt-6.1-sol accept low..max; gpt-6-luna also accepts none
DEFAULT_BASE_URL = "https://api.openai.com/v1"
TIMEOUT_S = 120


class OpenAIChat:
    def __init__(self, language, system_template, model=None, effort=DEFAULT_EFFORT):
        self.key = os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL
        self.effort = effort
        self.url = (os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"
        self.system = system_template.format(language=language)
        self.history = []

    def reset(self):
        self.history = []

    def _post(self, body):
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.load(response)

    def ask(self, question):
        if not self.key:
            return "Mac error: OPENAI_API_KEY is not set. Set it and restart the bridge."
        messages = self.history + [{"role": "user", "content": question}]
        body = {"model": self.model, "messages": [{"role": "system", "content": self.system}] + messages}
        if self.effort:
            body["reasoning_effort"] = self.effort
        try:
            data = self._post(body)
        except urllib.error.HTTPError as e:
            return self._error_reply(e)
        except (urllib.error.URLError, TimeoutError) as e:
            print("OpenAI connection error: %s" % e, flush=True)
            return "Mac error: could not reach the OpenAI API. Check the internet connection."

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        if message.get("refusal"):
            return "Sorry, I can't help with that question."
        answer = (message.get("content") or "").strip()
        if not answer:
            return "(empty answer, please ask again)"
        self.history = messages + [{"role": "assistant", "content": answer}]
        return answer

    def _error_reply(self, e):
        try:
            err = json.loads(e.read().decode("utf-8", "replace")).get("error") or {}
        except (ValueError, AttributeError):
            err = {}
        detail = err.get("message") or str(e)
        code = err.get("code") or err.get("type") or ""
        print("OpenAI API error %d (%s): %s" % (e.code, code, detail), flush=True)
        if e.code == 401:
            return "Mac error: the OpenAI API key is invalid. Check OPENAI_API_KEY and restart the bridge."
        if code == "insufficient_quota":
            return "Mac error: the OpenAI account has no credits left. Add credits at platform.openai.com (Billing)."
        if e.code == 429:
            return "Mac error: rate limited by OpenAI. Wait a minute and try again."
        if code == "model_not_found" or e.code == 404:
            return "Mac error: model %s is not available to this API key. Try --model." % self.model
        return "Mac error: OpenAI API returned %d: %s" % (e.code, detail[:160])
