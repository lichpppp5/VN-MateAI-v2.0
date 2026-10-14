# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""MCP server giả kiểu stdio cho test (tests/test_agent_gateway_mcp.py). Không phải test: không có hàm test_*.

Cố tình: in một dòng log lạc vào stdout, và hỏi ngược client một `ping` trước khi trả tools/list.
"""
import json
import sys


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    sys.stdin.reconfigure(encoding="utf-8")       # MCP stdio: UTF-8 (Windows mặc định cp1252)
    sys.stdout.reconfigure(encoding="utf-8")
    print("khởi động server giả (dòng log không phải JSON)", flush=True)
    for line in sys.stdin:
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                                          "serverInfo": {"name": "stdio-gia", "version": "0.1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": "srv-ping", "method": "ping"})
            reply = json.loads(sys.stdin.readline())
            assert reply.get("id") == "srv-ping" and "result" in reply
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "echo", "description": "Lặp lại", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                 "annotations": {"readOnlyHint": True}}]}})
        elif method == "tools/call":
            text = msg["params"]["arguments"].get("text", "")
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"echo: {text}"}]}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "không hỗ trợ"}})


if __name__ == "__main__":
    main()
