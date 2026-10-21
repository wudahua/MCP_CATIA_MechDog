# Remote access: agent on a Mac, CATIA on Windows

CATIA V5 only runs on Windows, and the default stdio transport needs the
agent on that same machine. The HTTP transport lifts that limit: the server
runs next to CATIA, and Claude Code, Cursor, VS Code or Claude Desktop
connect to it from a Mac, another PC, or while CATIA runs on a cloud
workstation (Vagon, Shadow, a GPU VM).

```
Mac / laptop                      Windows machine (local or cloud)
AI client  --HTTP + token-->      catia-mcp --http  --COM-->  CATIA V5
```

## 1. On the Windows machine

Install the server as usual, then create a token and start it:

```bat
catia-mcp --new-token
set CATIA_MCP_TOKEN=<paste the token>
catia-mcp --http --host 0.0.0.0 --port 8765
```

`python -m catia_mcp.server --http ...` works the same if the `catia-mcp`
command is not on the PATH.

- Without `--host`, the server only listens on `127.0.0.1` (same machine).
- On any other address it refuses to start without a token of at least
  16 characters. Every request must send `Authorization: Bearer <token>`.
- Allow the port in the Windows firewall if Windows asks.
- Keep CATIA open and visible in that Windows session: the server drives
  the running CATIA, it does not launch a hidden one.

## 2. Connect the two machines privately

The transport is plain HTTP. Never expose the port directly on the
internet; anyone holding the URL and the token would drive your CATIA.

- **Recommended: Tailscale** (free for personal use). Install it on both
  machines, sign in with the same account, and use the Windows machine's
  Tailscale name or `100.x.y.z` address. Works the same for a cloud PC.
- **SSH tunnel**: start the server without `--host` (loopback only) and run
  `ssh -L 8765:127.0.0.1:8765 user@windows-host` from the Mac. The URL is
  then `http://127.0.0.1:8765/mcp` on the Mac.
- **Same office network**: the Windows machine's LAN IP works, with the token.

## 3. Configure the client (on the Mac)

Replace `HOST` with the Tailscale name, IP, or `127.0.0.1` for a tunnel.

**Claude Code**

```bash
claude mcp add --transport http catia-v5 http://HOST:8765/mcp \
  --header "Authorization: Bearer YOUR_TOKEN"
```

**Cursor** (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "catia-v5-lite": {
      "url": "http://HOST:8765/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN" }
    }
  }
}
```

**VS Code** (`.vscode/mcp.json`)

```json
{
  "servers": {
    "catia-v5-lite": {
      "type": "http",
      "url": "http://HOST:8765/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN" }
    }
  }
}
```

**Claude Desktop** reads only local (stdio) servers from its config file,
so it goes through the `mcp-remote` bridge (needs Node.js on the Mac):

```json
{
  "mcpServers": {
    "catia-v5-lite": {
      "command": "npx",
      "args": [
        "mcp-remote", "http://HOST:8765/mcp",
        "--allow-http",
        "--header", "Authorization:Bearer ${CATIA_MCP_TOKEN}"
      ],
      "env": { "CATIA_MCP_TOKEN": "YOUR_TOKEN" }
    }
  }
}
```

`--allow-http` is required by `mcp-remote` for a non-HTTPS URL; keep it to a
private network as described above.

## Limits

- One CATIA session serves every connected client; calls are serialized on
  one worker, so two agents never drive CATIA at the same time.
- No TLS in the server itself. Use a private network, or put a TLS reverse
  proxy (Caddy, nginx) in front if you need HTTPS.
- Web chat connectors (claude.ai, ChatGPT) need a public HTTPS URL with
  OAuth, which this transport does not provide; use a desktop or CLI client.
- Screenshots and exports are written on the Windows machine's disk, at the
  paths the tools receive.
