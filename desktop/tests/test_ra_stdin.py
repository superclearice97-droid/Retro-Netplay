"""RetroArch commands: by default the app uses the UDP command port (as before); with the experimental
"ra_stdin_cmds" setting it commands RetroArch through stdin instead, and opens no network port at all."""
import asyncio, json, os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
home = tempfile.mkdtemp(); os.environ["HOME"] = home
import app

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)

def cfg_with(**kw):
    c = json.loads(json.dumps(app.DEFAULT_CFG)); c.update(kw); return c

# 1. default: nothing changes (UDP command port), the experimental switch is off
check("stdin commands are off by default", app.DEFAULT_CFG["ra_stdin_cmds"] is False)
text = Path(app.write_ra_config(cfg_with())).read_text()
check("default config uses the network command port", 'network_cmd_enable = "true"' in text and "stdin_cmd_enable" not in text, text)

# 2. experimental: stdin only, no network port in the config
on = cfg_with(ra_stdin_cmds=True)
text = Path(app.write_ra_config(on)).read_text()
if sys.platform == "win32":
    check("Windows never uses stdin commands", not app.ra_stdin_on(on))
else:
    check("stdin config turns on stdin commands", 'stdin_cmd_enable = "true"' in text, text)
    check("stdin config opens no network command port", "network_cmd" not in text, text)

    # 3. commands really reach the game's stdin, one per line, newlines in text can't smuggle a second command
    out = Path(home) / "stdin.txt"
    fake = Path(home) / "ra.sh"; fake.write_text(f'#!/bin/sh\ncat > "{out}"\n'); fake.chmod(0o755)

    async def go():
        loop = asyncio.get_running_loop()
        proc = await asyncio.create_subprocess_exec(str(fake), stdin=asyncio.subprocess.PIPE)
        app.ra_cmd("NETPLAY_HOST_TOGGLE", proc=proc, loop=loop)
        app.ra_cmd("SHOW_MSG hi\nQUIT", proc=proc, loop=loop)
        await asyncio.sleep(0.2)          # let the loop write
        proc.stdin.close()
        await proc.wait()
    asyncio.run(go())
    lines = out.read_text().splitlines()
    check("each command arrives as its own line", lines[:1] == ["NETPLAY_HOST_TOGGLE"] and len(lines) == 2, lines)
    check("a newline inside a message can't add a second command", lines[1] == "SHOW_MSG hi QUIT", lines)

sys.exit(0 if ok else 1)
