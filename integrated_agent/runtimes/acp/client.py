"""持久化 codex ACP 后端：把 codex 当成一个长期会话，每条消息 = 这个会话里的一轮 prompt。

和第17课的一次性 run_acp_task 不同：这里**只 spawn 一次 codex、只建一次会话**，之后所有
prompt 复用同一个 session_id——非 new_session() 不重建（满足“会话不被动重置”）。session_update
里的文本块（agent_message_chunk）实时推进队列，供上层流式回传给 IM。
"""
from __future__ import annotations

import asyncio
import shutil
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any, AsyncIterator, cast

from acp import PROTOCOL_VERSION, Client, spawn_agent_process, text_block
from acp.schema import (
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    Implementation,
    PermissionOption,
    RequestPermissionResponse,
    ToolCallUpdate,
)

CODEX_ACP_VERSION = "@agentclientprotocol/codex-acp@0.0.46"


def codex_command() -> tuple[str, tuple[str, ...]]:
    """codex 通过 npx 的 ACP 适配器启动（codex CLI 本身不直接讲 ACP）。"""
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("未找到 npx。请先安装 Node.js，并确认 npx 在 PATH 中。")
    return npx, ("-y", CODEX_ACP_VERSION)


class _StreamingClient:
    """ACP Client：把 agent_message_chunk 文本推进当前 sink 队列。

    权限请求默认放行——因为这是“你本地、你自己显式路由进去的 codex 会话”，
    课堂演示默认放行；真实环境可把 auto_approve 设为 False，并接入人工确认。
    """

    def __init__(self, auto_approve: bool = True) -> None:
        self.sink: asyncio.Queue[str | None] | None = None
        self.auto_approve = auto_approve

    def on_connect(self, conn: Any) -> None:
        self._conn = conn

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        content = getattr(update, "content", None)
        text = getattr(content, "text", None)
        if getattr(content, "type", None) == "text" and isinstance(text, str) and self.sink is not None:
            self.sink.put_nowait(text)

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: ToolCallUpdate,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        pool = options
        pick = None
        if self.auto_approve:
            pick = next((o for o in pool if o.kind.startswith("allow")), None)
        if pick is None:
            pick = next((o for o in pool if o.kind.startswith("reject")), None)
        if pick is None:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=pick.option_id)
        )

    async def write_text_file(self, **kwargs: Any) -> None:
        raise RuntimeError("no fs write capability")

    async def read_text_file(self, **kwargs: Any) -> Any:
        raise RuntimeError("no fs read capability")

    async def create_terminal(self, **kwargs: Any) -> Any:
        raise RuntimeError("no terminal capability")

    async def terminal_output(self, **kwargs: Any) -> Any:
        raise RuntimeError("no terminal capability")

    async def release_terminal(self, **kwargs: Any) -> None:
        raise RuntimeError("no terminal capability")

    async def wait_for_terminal_exit(self, **kwargs: Any) -> Any:
        raise RuntimeError("no terminal capability")

    async def kill_terminal(self, **kwargs: Any) -> None:
        raise RuntimeError("no terminal capability")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"unsupported ext method: {method}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None


class CodexAcpClient:
    """长期持有一个 codex ACP 会话；prompt_stream 在同一会话里跑一轮并流式吐文本。"""

    def __init__(self, cwd: str | Path | None = None, *, auto_approve: bool = True) -> None:
        self._command, self._args = codex_command()
        self._cwd = Path(cwd or Path.cwd()).resolve()
        self._client = _StreamingClient(auto_approve=auto_approve)
        self._stack: AsyncExitStack | None = None
        self._conn: Any = None
        self._lock = asyncio.Lock()
        self.session_id: str | None = None
        self.sessions: list[str] = []      # 本次运行建过的所有 codex 会话
        self.agent_info: str = ""

    async def ensure_started(self) -> None:
        if self._conn is not None:
            return
        self._stack = AsyncExitStack()
        connection, _process = await self._stack.enter_async_context(
            spawn_agent_process(cast(Client, self._client), self._command, *self._args, cwd=self._cwd)
        )
        self._conn = connection
        init = await connection.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(
                name="wecom-acp-router", title="WeCom ACP Router", version="0.1.0"
            ),
        )
        self.agent_info = init.agent_info.name if init.agent_info else "codex"
        await self.new_session()

    async def new_session(self) -> str:
        await self.ensure_started()
        session = await self._conn.new_session(cwd=str(self._cwd), mcp_servers=[])
        sid = str(session.session_id)
        self.session_id = sid
        self.sessions.append(sid)
        return sid

    def list_sessions(self) -> list[str]:
        return list(self.sessions)

    def switch(self, session_id: str) -> bool:
        """切到一个已存在的会话；未知 id 返回 False。"""
        if session_id in self.sessions:
            self.session_id = session_id
            return True
        return False

    async def prompt_stream(self, text: str) -> AsyncIterator[str]:
        await self.ensure_started()
        async with self._lock:  # 同一会话一次只跑一轮，串行化
            queue: asyncio.Queue[str | None] = asyncio.Queue()
            self._client.sink = queue
            task = asyncio.create_task(
                self._conn.prompt(prompt=[text_block(text)], session_id=self.session_id)
            )
            task.add_done_callback(lambda _t: queue.put_nowait(None))
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield item
            finally:
                self._client.sink = None
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            if not task.cancelled():
                await task  # 让 prompt 的异常（如有）抛出来

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._conn = None


async def _selftest() -> None:
    """同一会话连发两轮，验证会话持久（第二轮能记得第一轮内容）。"""
    backend = CodexAcpClient()
    try:
        print("启动 codex ACP 会话…")
        await backend.ensure_started()
        print(f"agent={backend.agent_info} session={backend.session_id}")

        print("\n[第1轮] 记住一个数字 73，只回复'好的'：")
        async for delta in backend.prompt_stream("请记住数字 73。只回复两个字：好的。"):
            print(delta, end="", flush=True)
        sid1 = backend.session_id

        print("\n\n[第2轮] 我刚让你记的数字是多少？（同一会话，应记得）：")
        async for delta in backend.prompt_stream("我刚让你记住的数字是多少？只回数字。"):
            print(delta, end="", flush=True)
        print(f"\n\n两轮 session 一致：{sid1 == backend.session_id}（{backend.session_id}）")
    finally:
        await backend.close()


if __name__ == "__main__":
    asyncio.run(_selftest())
