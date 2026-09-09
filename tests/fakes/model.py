import httpx


class ScriptedModel(httpx.AsyncBaseTransport):
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        output = next(self.outputs)
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": output}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )
