// Retro-Netplay interface. Draws what the engine reports (/api/events) and sends what the player does (/api/do).
// The same page runs in the desktop shell and, later, inside the phone app.
import { ICONS } from "./icons.js";
import { clean } from "./filter.js";

const T = (() => {        // the engine's token comes in the address (#t=...); keep it for this window only
  const m = location.hash.match(/t=([\w-]+)/);
  try { if (m) sessionStorage.setItem("t", m[1]); } catch {}
  if (m) history.replaceState(null, "", location.pathname);
  try { return m ? m[1] : sessionStorage.getItem("t") || ""; } catch { return m ? m[1] : ""; }
})();

let S = null;                       // the engine's state
const ui = { page: "arcade", filter: "all", pick: null, seats: 2, note: "", auto: false, pub: true, stream: false,
             chatTab: "lobby", menu: null, howto: true };
try { ui.howto = localStorage.getItem("howto") !== "hidden"; } catch {}

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (n, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n] || ""}</svg>`;
const art = p => p ? `/art?p=${encodeURIComponent(p)}&t=${T}` : "";
const ago = ts => { const s = Math.max(0, Date.now() / 1000 - ts); return s < 60 ? "now" : s < 3600 ? `${Math.floor(s / 60)} min` : `${Math.floor(s / 3600)} h`; };
const sysLabel = k => S?.systems?.[k]?.label || k;

async function act(action, data = {}) {
  try {
    const r = await fetch("/api/do", { method: "POST", headers: { "Content-Type": "application/json", "X-Token": T },
                                       body: JSON.stringify({ action, ...data }) });
    const j = await r.json();
    if (!j.ok && j.msg) toast(j.msg, "error");
    return j;
  } catch { toast("Retro-Netplay's engine isn't answering. Restart the app.", "error"); return { ok: false }; }
}

function toast(text, kind = "info") {
  const box = document.getElementById("toasts");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = text;
  box.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 9000 : 6000);
}

const sounds = {};
function sound(name) {
  if (!S?.settings?.sounds) return;
  try { (sounds[name] ||= new Audio(`/sounds/${name}.wav?t=${T}`)).play().catch(() => {}); } catch {}
}

// ---------------------------------------------------------------- pages
function welcome() {
  return `<div class="welcome"><div class="box">
    <div class="brand"><div class="mark">${icon("gamepad-2")}</div><div>Retro-Netplay<small>ARCADE</small></div></div>
    <h1>Play your old games with anyone, online.</h1>
    <p class="lead">Couch co-op and versus games, over the internet. Open a room for the game you're playing, or jump into someone else's. Like an arcade, from home.</p>
    <div class="steps" style="margin:26px 0">
      <div><b>Pick a room</b><br><span class="muted small">See what people are playing right now.</span></div>
      <div><b>Get let in</b><br><span class="muted small">The host says yes, or rooms let you straight in.</span></div>
      <div><b>Play</b><br><span class="muted small">Your controller works as Player 2, 3 or 4.</span></div>
    </div>
    <form class="card stack" id="setup">
      <label class="field"><span>Your player name (others see this)</span><input name="name" maxlength="24" autocomplete="nickname" value="${esc(S.me.name)}" required></label>
      <label class="toggle"><input type="checkbox" name="age" ${S.setup.age_ok ? "checked" : ""}>
        <span><b>I'm 13 or older, and I'll be kind</b><small>No hate, no harassment, no spam. Lobbies can mute, remove and ban players. See the <a href="#" data-go="legal">terms and privacy</a>.</small></span></label>
      <button class="btn primary big" type="submit">${icon("log-in")} Let's play</button>
    </form>
  </div></div>`;
}

function rail() {
  const n = S.requests.length;
  const item = (p, ic, label, extra = "") => `<button class="${ui.page === p ? "on" : ""}" data-go="${p}" title="${label}">${icon(ic)}<span class="lbl">${label}</span>${extra}</button>`;
  const inRoom = S.myroom || S.seat;
  return `<nav class="rail" aria-label="Main">
    <div class="brand"><div class="mark">${icon("gamepad-2")}</div><span>Retro-Netplay<small>ARCADE</small></span></div>
    ${item("arcade", "monitor-play", "Arcade")}
    ${inRoom ? item("room", "door-open", S.myroom ? "Your room" : "Room", n ? `<span class="badge">${n}</span>` : "") : item("host", "plus", "Host a game")}
    ${item("library", "library", "My games")}
    ${item("chat", "message-circle", "Chat")}
    ${item("settings", "settings", "Settings")}
    <div class="foot">${S.lobby.connected ? `<span class="dot ok"></span> ${esc(S.lobby.online)} online` : `<span class="dot bad"></span> Not connected`}</div>
  </nav>`;
}

function lobbyPill() {
  const L = S.lobby;
  return `<button class="pill" data-go="settings" title="Change lobby">
    <span class="dot ${L.connected ? "ok" : "bad"}"></span>
    ${L.connected ? `${esc(L.name || "Lobby")} &middot; ${esc(L.online)} online` : L.error ? "Can't reach the lobby" : "Connecting..."}
  </button>`;
}

function roomTile(r, i) {
  const full = r.taken >= r.seats;
  const needGame = r.mode === "netplay" && !S.library.some(g => g.key === r.game && !g.problem);
  const seats = Array.from({ length: r.seats }, (_, k) => `<i class="${k < r.taken ? "f" : ""}"></i>`).join("");
  const mine = r.host.id === S.me.id;
  const mode = r.mode === "stream" ? `${icon("monitor-play")} Just join` : `${icon("users")} Bring your copy`;
  return `<button class="tile sys-${esc(r.system)} ${full || needGame ? "full" : ""} ${i === 0 && S.rooms.length > 3 ? "wide" : ""}"
      style="--d:${Math.min(i, 12) * 45}ms" data-join="${esc(r.id)}" ${full || mine || needGame ? "disabled" : ""} aria-label="${esc(r.title)}, hosted by ${esc(r.host.name)}">
    <div class="top"><span>${esc(sysLabel(r.system).toUpperCase())}</span><span class="mode">${mode}</span></div>
    <div class="title">${esc(r.title)}</div>
    <div class="meta"><span>${icon("crown")} ${esc(r.host.name)}${mine ? " (you)" : ""}</span><span class="seats" title="${r.taken} of ${r.seats} players">${seats}</span></div>
    ${needGame ? `<div class="note-line">${icon("lock")} You need this game in My games to join</div>` : full ? `<div class="note-line">Full</div>` :
      r.note ? `<div class="note-line">${esc(r.note)}</div>` : ""}
  </button>`;
}

function arcade() {
  const systems = [...new Set(S.rooms.map(r => r.system))];
  const shown = S.rooms.filter(r => ui.filter === "all" || r.mode === ui.filter || r.system === ui.filter);
  const chip = (k, label) => `<button class="chip ${ui.filter === k ? "on" : ""}" data-filter="${k}">${label}</button>`;
  return `<div class="page">
    <div class="head"><div><div class="eyebrow">Arcade</div><h1>Open rooms</h1></div>
      <div class="row"><form class="row" id="codeform"><input id="code" maxlength="6" placeholder="Invite code" style="width:140px;text-transform:uppercase" aria-label="Invite code">
      <button class="btn">Join</button></form>${lobbyPill()}</div></div>
    ${ui.howto ? `<div class="note row spread" style="margin-bottom:18px"><span><b>New here?</b> Rooms marked <b>Just join</b> stream the host's screen: you need nothing but a controller.
      <b>Bring your copy</b> rooms use the emulator's own netplay (smoothest), so you need the same game in My games.</span>
      <button class="btn ghost" data-act="hide-howto">Got it</button></div>` : ""}
    <div class="row" style="margin-bottom:18px">${chip("all", "Everything")}${chip("stream", "Just join")}${chip("netplay", "Bring your copy")}
      ${systems.map(s => chip(s, esc(sysLabel(s)))).join("")}</div>
    ${!S.lobby.connected ? `<div class="warnbox">${icon("wifi-off")} <b>Not connected to a lobby.</b> ${esc(S.lobby.error || "Trying...")}
       <div class="row" style="margin-top:10px"><button class="btn" data-go="settings">Choose a lobby</button></div></div>` :
      shown.length ? `<div class="tiles">${shown.map(roomTile).join("")}</div>` :
      `<div class="empty card"><div class="icon-big">${icon("monitor-play")}</div><h2 style="margin:12px 0 6px">No open rooms ${ui.filter === "all" ? "right now" : "like that"}</h2>
       <p class="muted">Be the first: open a room for a game you'd like to play, and people in this lobby can join you.</p>
       <div class="row" style="justify-content:center"><button class="btn primary big" data-go="host">${icon("plus")} Host a game</button>
       <button class="btn big" data-go="settings">${icon("globe")} Try another lobby</button></div></div>`}
  </div>`;
}

function gameCard(g, picked, open = false) {
  const bad = g.problem;
  return `<button class="game sys-${esc(g.system)} ${picked ? "on" : ""}" ${open ? `data-game="${g.i}"` : `data-pick="${g.i}" aria-pressed="${picked}"`}>
    <div class="art">${g.box ? `<img src="${art(g.box)}" alt="" loading="lazy">` : `<div class="fallback">${esc(g.name)}</div>`}
      ${bad ? `<div class="warn">${icon("triangle-alert")} Needs setup</div>` : ""}</div>
    <div class="txt"><b>${esc(g.name)}</b><small>${esc(sysLabel(g.system))}${g.year ? " &middot; " + esc(g.year) : ""}</small></div>
  </button>`;
}

function host() {
  const lib = S.library;
  if (S.device?.can_host === false) return `<div class="page"><div class="head"><div><div class="eyebrow">Host</div><h1>Host a game</h1></div></div>
    <div class="empty card"><div class="icon-big">${icon("monitor-play")}</div><h2 style="margin:12px 0 6px">Hosting from a phone is coming later</h2>
    <p class="muted">For now, open rooms from a computer with Retro-Netplay. On this phone you can join any room in the arcade.</p>
    <button class="btn primary big" data-go="arcade">${icon("monitor-play")} See open rooms</button></div></div>`;
  if (!lib.length) return `<div class="page"><div class="head"><div><div class="eyebrow">Host</div><h1>Host a game</h1></div></div>
    <div class="empty card"><div class="icon-big">${icon("folder-plus")}</div><h2 style="margin:12px 0 6px">First, show Retro-Netplay your games</h2>
    <p class="muted">Pick the folder where your game files are. Retro-Netplay finds the games and their emulators.</p>
    <button class="btn primary big" data-act="add-folder">${icon("folder-plus")} Add a game folder</button></div></div>`;
  const g = lib.find(x => x.i === ui.pick);
  const netplayOk = g && S.systems[g.system]?.netplay;
  const seg = [2, 3, 4].map(n => `<button class="${ui.seats === n ? "on" : ""}" data-seats="${n}">${n}</button>`).join("");
  return `<div class="page">
    <div class="head"><div><div class="eyebrow">Host</div><h1>Host a game</h1></div>${lobbyPill()}</div>
    <h2 style="margin-bottom:12px">1. Pick the game</h2>
    <div class="games" style="margin-bottom:26px">${lib.map(x => gameCard(x, x.i === ui.pick)).join("")}</div>
    ${g ? `<h2 style="margin-bottom:12px">2. Set up the room</h2>
    <div class="card stack">
      ${g.problem ? `<div class="warnbox">${icon("triangle-alert")} ${esc(g.problem)}</div>` : ""}
      <label class="field"><span>Room title</span><input id="r-title" maxlength="48" value="${esc(g.name)}"></label>
      <div><span class="muted small">How many players, you included</span><div style="margin-top:6px"><span class="seg">${seg}</span></div></div>
      <label class="field"><span>A note for people browsing (optional)</span><input id="r-note" maxlength="120" placeholder="e.g. Grand Prix, beginners welcome" value="${esc(ui.note)}"></label>
      ${netplayOk ? `<div><span class="muted small">How players join</span><div class="choice" style="margin-top:6px">
        <button class="${!ui.stream ? "on" : ""}" data-stream="0"><b>${icon("users")} Netplay</b><small>Smoothest. Each player runs the game on their own computer, so they need their own copy.</small></button>
        <button class="${ui.stream ? "on" : ""}" data-stream="1"><b>${icon("monitor-play")} Stream</b><small>Anyone can join with just the app. They see your screen and control their player.</small></button>
      </div></div>` : `<div class="note small">${icon("info")} ${esc(sysLabel(g.system))} games are streamed: players see your screen and control their own player. They don't need the game.</div>`}
      <label class="toggle"><input type="checkbox" id="r-auto" ${ui.auto ? "checked" : ""}><span><b>Let people in without asking</b><small>Off: you get a request and choose who joins.</small></span></label>
      <label class="toggle"><input type="checkbox" id="r-pub" ${ui.pub ? "checked" : ""}><span><b>Show the room in the arcade</b><small>Off: only people you give the invite code can join.</small></span></label>
      <button class="btn primary big" data-act="post" ${g.problem || !S.lobby.connected ? "disabled" : ""}>${icon("play")} Open the room and start the game</button>
    </div>` : `<p class="muted">Pick a game above to set up its room.</p>`}
  </div>`;
}

function seatCard(p, n, isHost) {
  if (!p) return `<div class="player open">${icon("plus")}<span>Open seat</span><small>Player ${n}</small></div>`;
  const me = p.id === S.me.id;
  return `<div class="player ${me ? "me" : ""}"><span class="num">PLAYER ${n}</span><b>${esc(p.name)}${me ? " (you)" : ""}</b>
    <span class="muted small">${n === 1 ? `${icon("crown")} Host` : "Player"}</span>
    ${!me ? `<button class="btn ghost menu-btn" data-menu="${esc(p.id)}" data-name="${esc(p.name)}" data-seated="${isHost && n > 1 ? 1 : 0}" aria-label="Options for ${esc(p.name)}">&middot;&middot;&middot;</button>` : ""}</div>`;
}

function room() {
  const hosting = !!S.myroom;
  const r = S.myroom || S.seat?.room;
  if (!r) { ui.page = "arcade"; return arcade(); }
  if (S.seat?.waiting) return `<div class="page"><div class="empty card"><div class="icon-big">${icon("clock")}</div>
    <h2 style="margin:12px 0 6px">Asking ${esc(r.host?.name || "the host")} to let you in...</h2><p class="muted">${esc(r.title || "")}</p>
    <button class="btn" data-act="cancel-join">Cancel</button></div></div>`;
  const players = {}; (r.players || []).forEach(p => players[p.seat] = p);
  const cards = Array.from({ length: r.seats }, (_, k) => seatCard(players[k + 1], k + 1, hosting)).join("");
  const dolphin = hosting && r.mode === "netplay" && r.system === "gamecube";
  return `<div class="page">
    <div class="head"><div><div class="eyebrow">${hosting ? "Your room" : "Room"} &middot; ${esc(sysLabel(r.system))} &middot; ${r.mode === "stream" ? "Stream" : "Netplay"}</div><h1>${esc(r.title)}</h1></div>
      <button class="btn danger" data-act="${hosting ? "close-room" : "leave"}">${icon("door-open")} ${hosting ? "Close the room" : "Leave"}</button></div>
    ${S.playing.note ? `<div class="note" style="margin-bottom:18px">${icon("info")} ${esc(S.playing.note)}</div>` : ""}
    ${S.requests.map(q => `<div class="request" style="margin-bottom:12px"><span style="flex:1"><b>${esc(q.player.name)}</b> wants to join</span>
      <button class="btn primary" data-answer="${esc(q.req)}" data-yes="1">${icon("check")} Let in</button>
      <button class="btn" data-answer="${esc(q.req)}" data-yes="0">Not now</button></div>`).join("")}
    <div class="players" style="margin-bottom:18px">${cards}</div>
    ${hosting ? `<div class="card stack">
      <div class="row spread"><div><div class="eyebrow">Invite code</div><div class="code">${esc(r.invite || "")}</div></div>
        <button class="btn" data-copy="${esc(r.invite || "")}">${icon("copy")} Copy</button></div>
      <p class="muted small">Friends in this lobby can type the code under Arcade &gt; Join with a code${r.public ? ", or find the room in the arcade" : ""}.</p>
      ${dolphin ? `<label class="field"><span>Dolphin host code (Dolphin shows it after Tools &gt; NetPlay &gt; Host)</span>
        <div class="row"><input id="dolphin-code" maxlength="16" style="flex:1"><button class="btn" data-act="dolphin-code">Share with players</button></div></label>` : ""}
      <label class="toggle"><input type="checkbox" id="live-auto" ${r.auto ? "checked" : ""}><span><b>Let people in without asking</b></span></label>
    </div>` : ""}
  </div>`;
}

function gamePage() {
  const g = S.library.find(x => x.i === ui.game);
  if (!g) { ui.page = "library"; return library(); }
  const shots = g.screens.length ? g.screens : [g.title].filter(Boolean);
  const n = shots.length;
  const facts = [["calendar", g.year], ["building", g.developer || g.publisher], ["tag", g.genre],
                 ["users", g.players && `${g.players} player${g.players === "1" ? "" : "s"}`], ["star", g.rating && `${Math.round(+g.rating * 10)}/10`]]
    .filter(([, v]) => v).map(([ic, v]) => `<span class="pill">${icon(ic)} ${esc(v)}</span>`).join("");
  return `<div class="page"><div class="gamecard sys-${esc(g.system)}">
    <div class="mesh"></div>
    <div class="gc-body">
      <div class="gc-box">${g.box ? `<img src="${art(g.box)}" alt="">` : `<div class="fallback">${esc(g.name)}</div>`}</div>
      <div class="gc-info">
        <button class="btn ghost back" data-go="library">${icon("arrow-left")} My games</button>
        <div class="eyebrow">${esc(sysLabel(g.system))}</div>
        ${g.logo ? `<img class="gc-logo" src="${art(g.logo)}" alt="${esc(g.name)}">` : `<h1>${esc(g.name)}</h1>`}
        <div class="row" style="margin:12px 0">${facts}</div>
        ${g.desc ? `<p class="gc-desc">${esc(g.desc)}</p>` : ""}
        ${g.problem ? `<div class="warnbox">${icon("triangle-alert")} ${esc(g.problem)}</div>` : ""}
        <div class="row" style="margin-top:16px">
          <button class="btn primary big" data-hostgame="${g.i}" ${g.problem ? "disabled" : ""}>${icon("users")} Host this game</button>
          <button class="btn big" data-solo="${g.i}" ${g.problem ? "disabled" : ""}>${icon("play")} Play alone</button></div>
      </div>
      ${n ? `<div class="tv" aria-hidden="true"><div class="tv-screen">${shots.map((p, k) =>
        `<img src="${art(p)}" alt="" class="${k === (ui.slide || 0) % n ? "on" : ""}">`).join("")}</div></div>` : ""}
    </div>
    ${!g.box && !n ? `<p class="small" style="position:relative;opacity:.8;margin:0 28px 22px">${icon("image")} No box art yet. Run Skraper (or EmulationStation's scraper) on your game folder, and the art shows up here.</p>` : ""}
  </div></div>`;
}

function library() {
  return `<div class="page">
    <div class="head"><div><div class="eyebrow">Library</div><h1>My games</h1></div>
      <div class="row"><button class="btn" data-act="rescan">${icon("refresh-cw")} Scan again</button><button class="btn primary" data-act="add-folder">${icon("folder-plus")} Add a folder</button></div></div>
    ${S.library.length ? `<div class="games">${S.library.map(g => gameCard(g, false, true)).join("")}</div>` :
      `<div class="empty card"><div class="icon-big">${icon("library")}</div><h2 style="margin:12px 0 6px">No games yet</h2>
       <p class="muted">Add the folder where your game files are. Box art from Skraper or EmulationStation shows up by itself.</p></div>`}
    <h2 style="margin:28px 0 10px">Folders</h2>
    <div class="card stack">${(S.settings.game_folders || []).map(f => `<div class="row spread"><span class="mono small">${esc(f)}</span>
      <button class="btn ghost" data-unfolder="${esc(f)}">Remove</button></div>`).join("") || `<span class="muted">No folders yet.</span>`}</div>
  </div>`;
}

function chatPanel() {
  const tab = (S.myroom || S.seat?.room) && ui.chatTab === "room" ? "room" : "lobby";
  const msgs = S.chat[tab] || [];
  const blocked = new Set(S.blocked.map(b => b.id));
  const lines = msgs.filter(m => !blocked.has(m.from.id)).map(m => {
    const mine = m.from.id === S.me.id;
    const text = S.settings.chat_filter ? clean(m.text) : m.text;
    return `<div class="msg ${mine ? "mine" : ""}"><button class="who" ${mine ? "" : `data-menu="${esc(m.from.id)}" data-name="${esc(m.from.name)}"`}>${esc(m.from.name)}</button>${esc(text)}<span class="when">${ago(m.ts)}</span></div>`;
  }).join("");
  const inRoom = S.myroom || S.seat?.room;
  return `<aside class="chat" aria-label="Chat">
    <div class="chat-head"><div class="eyebrow">Chat</div>
      <div class="chat-tabs"><button class="chip ${tab === "lobby" ? "on" : ""}" data-chat="lobby">Lobby</button>
      ${inRoom ? `<button class="chip ${tab === "room" ? "on" : ""}" data-chat="room">Room</button>` : ""}</div></div>
    <div class="msgs" id="msgs">${lines || `<p class="muted small">${tab === "lobby" ? "Say hi to the lobby. Be kind: tap a name to report or block someone." : "Only the people in this room see these messages."}</p>`}</div>
    <form class="chat-form" id="chatform"><input id="chatinput" maxlength="300" placeholder="${tab === "lobby" ? "Message the lobby" : "Message the room"}" autocomplete="off" ${S.lobby.connected ? "" : "disabled"}>
      <button class="btn primary" aria-label="Send">${icon("send")}</button></form>
  </aside>`;
}

function settings() {
  const L = S.lobby;
  const emus = Object.entries(S.emulators).map(([k, e]) => `<div class="row spread"><span><span class="dot ${e.ok ? "ok" : "bad"}"></span> <b>${esc(e.label)}</b>
    <span class="muted small mono">${esc(e.path || "not found")}</span></span><button class="btn ghost" data-emu="${k}">Find again</button></div>`).join("");
  return `<div class="page stack">
    <div class="head"><div><div class="eyebrow">Settings</div><h1>Settings</h1></div></div>
    <section class="card stack"><h2>${icon("globe")} Lobby</h2>
      <p class="muted">A lobby is where rooms and chat live. Any Retro-Netplay app can run one; people join it with its address.</p>
      <div class="row"><span class="dot ${L.connected ? "ok" : "bad"}"></span><b>${esc(L.name || L.address)}</b><span class="muted mono small">${esc(L.address)}</span></div>
      <div class="row"><input id="lobby-addr" placeholder="Lobby address, e.g. 203.0.113.5:7777" style="flex:1"><button class="btn primary" data-act="connect">Connect</button></div>
      <div class="row">${S.device?.phone ? "" : `<button class="btn" data-lobby="127.0.0.1:7777">This computer's lobby</button>`}${(L.saved || []).map(a => `<button class="btn ghost mono" data-lobby="${esc(a)}">${esc(a)}</button>`).join("")}</div>
      ${S.device?.phone ? "" : `<label class="toggle"><input type="checkbox" id="run-lobby" ${L.run_own ? "checked" : ""}><span><b>Run a lobby on this computer</b>
        <small>Others join it with this computer's address. For people outside your home, your router must let them in (the app asks it to). You moderate it: tap a name to mute, remove or ban.</small></span></label>`}
      ${L.share ? shareBox(L.share) : ""}
      ${L.admin ? `<div class="note small">${icon("shield")} You run this lobby. Reports arrive here and are kept in its reports file.</div>` : ""}
    </section>
    <section class="card stack"><h2>${icon("message-circle")} Chat and safety</h2>
      <label class="toggle"><input type="checkbox" id="chat-filter" ${S.settings.chat_filter ? "checked" : ""}><span><b>Hide swear words and slurs</b><small>On this device. The lobby may hide more.</small></span></label>
      <div><b>Blocked players</b> <span class="muted small">(you don't see their messages and they can't join your rooms)</span>
        ${S.blocked.length ? S.blocked.map(b => `<div class="row spread"><span>${esc(b.name || b.id)}</span><button class="btn ghost" data-unblock="${esc(b.id)}">Unblock</button></div>`).join("") : `<p class="muted small">Nobody.</p>`}</div>
    </section>
    ${emus ? `<section class="card stack"><h2>${icon("gamepad-2")} Emulators</h2>${emus}
      <p class="muted small">Retro-Netplay starts these for you. Install the ones for your consoles (RetroArch, Dolphin, PCSX2, RPCS3); they're found by themselves.</p></section>` : ""}
    <section class="card stack"><h2>${icon("user-x")} Profile</h2>
      <form class="row" id="rename"><input name="name" maxlength="24" value="${esc(S.me.name)}" style="flex:1"><button class="btn">Save name</button></form>
      <label class="toggle"><input type="checkbox" id="sounds" ${S.settings.sounds ? "checked" : ""}><span><b>Sounds</b><small>A chime when someone wants to join.</small></span></label>
      <p class="muted small"><a href="#" data-go="legal">Terms, privacy and licences</a></p></section>
  </div>`;
}

function shareBox(sh) {
  const line = (label, host) => host ? `<div class="row spread"><span><b>${label}</b> <span class="mono">${esc(host)}:${sh.port}</span></span>
    <button class="btn" data-copy="netplay://lobby?h=${encodeURIComponent(host)}&p=${sh.port}">${icon("link")} Copy invite link</button></div>` : "";
  return `<div class="note stack small"><b>${icon("users")} Invite people to your lobby</b>
    ${line("People on your Wi-Fi:", sh.lan)}${line("Everyone else:", sh.public)}
    ${!sh.public ? `<span class="muted">${esc(sh.router || "Asking your router to let people in...")}</span>` : ""}</div>`;
}

function legal() {
  return `<div class="page stack"><div class="head"><div><div class="eyebrow">About</div><h1>Terms and privacy</h1></div><button class="btn" data-go="${S.setup.done ? "settings" : "welcome"}">${icon("arrow-left")} Back</button></div>
    <div class="card stack"><p><b>No games are included.</b> Use games you own. Retro-Netplay never sends game files to anyone: streamed players only see your screen, and netplay players use their own copy.</p>
    <p><b>Lobbies and chat.</b> 13 and older only. Whoever runs a lobby sets its rules and can mute, remove and ban players; reports go to them. Chat is shown to the people in the lobby or room and isn't saved, except the messages attached to a report.</p>
    <p><b>What others see.</b> Your player name, the rooms you open and your chat messages. Your internet address is only given to the host of a netplay room you join (the emulators connect directly).</p>
    <p class="muted small">The full texts are in TERMS.md, PRIVACY.md and NOTICE.md next to the app.</p></div></div>`;
}

// ---------------------------------------------------------------- drawing
// What the player is typing survives redraws (the page redraws whenever the engine reports a change).
const drafts = {};
const fieldKey = el => (el.form?.id || "") + ":" + (el.id || el.name || "");

function draw() {
  const root = document.getElementById("root");
  if (!S) return;
  const active = document.activeElement;
  const focusKey = active && active.matches?.("input, textarea") ? fieldKey(active) : "";
  const caret = focusKey && typeof active.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null;
  const scroll = document.querySelector("main")?.scrollTop || 0;
  if (!S.setup.done && ui.page !== "legal") root.innerHTML = welcome();
  else {
    if (ui.page === "room" && !(S.myroom || S.seat)) ui.page = "arcade";
    const pages = { arcade, host, room, library, settings, legal, game: gamePage, chat: arcade };
    root.innerHTML = `<div id="app" class="${ui.page === "chat" ? "chatpage" : ""}">${rail()}<main>${(pages[ui.page] || arcade)()}</main>${chatPanel()}</div>`;
  }
  root.querySelectorAll("input:not([type=checkbox]):not([type=radio]), textarea").forEach(el => {
    const k = fieldKey(el);
    if (k in drafts) el.value = drafts[k];
    if (k === focusKey) { el.focus(); if (caret) try { el.setSelectionRange(caret[0], caret[1]); } catch {} }
  });
  const m = document.querySelector("main"); if (m) m.scrollTop = scroll;
  const msgs = document.getElementById("msgs"); if (msgs) msgs.scrollTop = msgs.scrollHeight;
}

function menu(x, y, id, name, seated) {
  closeMenu();
  const admin = S.lobby.admin;
  const el = document.createElement("div");
  el.className = "menu"; el.id = "menu";
  el.innerHTML = `<div class="eyebrow" style="padding:6px 12px">${esc(name)}</div>
    ${seated ? `<button data-m="kick">${icon("door-open")} Remove from the room</button><button class="bad" data-m="kickban">${icon("ban")} Remove and don't let back in</button>` : ""}
    <button data-m="block">${icon("eye-off")} Block</button>
    <button class="bad" data-m="report">${icon("flag")} Report</button>
    ${admin ? `<button data-m="mute">${icon("volume-x")} Mute for 10 minutes (lobby)</button><button class="bad" data-m="ban">${icon("shield")} Ban from the lobby</button>` : ""}`;
  document.body.append(el);
  el.style.left = Math.min(x, innerWidth - el.offsetWidth - 10) + "px";
  el.style.top = Math.min(y, innerHeight - el.offsetHeight - 10) + "px";
  el.addEventListener("click", async e => {
    const b = e.target.closest("[data-m]"); if (!b) return;
    const m = b.dataset.m; closeMenu();
    if (m === "kick" || m === "kickban") act("kick", { player: id, ban: m === "kickban" });
    if (m === "block") { await act("block", { player: id, name }); toast(`${name} is blocked.`); }
    if (m === "report") report(id, name);
    if (m === "mute") act("mod", { what: "mute", player: id, minutes: 10 });
    if (m === "ban") act("mod", { what: "ban", player: id });
  });
}
function closeMenu() { document.getElementById("menu")?.remove(); }

function report(id, name) {
  const d = document.createElement("dialog");
  const reasons = [["abuse", "Harassment or hate"], ["cheating", "Cheating"], ["spam", "Spam"], ["name", "Offensive name"], ["underage", "Seems under 13"], ["other", "Something else"]];
  d.innerHTML = `<form method="dialog" class="stack"><h2>${icon("flag")} Report ${esc(name)}</h2>
    <p class="muted small">The report, with their last few messages, goes to the person running this lobby.</p>
    ${reasons.map(([k, l], i) => `<label class="toggle"><input type="radio" name="why" value="${k}" ${i === 0 ? "checked" : ""}><span><b>${l}</b></span></label>`).join("")}
    <label class="field"><span>What happened? (optional)</span><input name="text" maxlength="300"></label>
    <div class="row"><button class="btn primary" value="send">Send report</button><button class="btn" value="cancel">Cancel</button></div></form>`;
  document.body.append(d);
  d.showModal();
  d.addEventListener("close", () => {
    if (d.returnValue === "send") { const f = d.querySelector("form"); act("report", { player: id, why: f.why.value, text: f.text.value }); }
    d.remove();
  });
}

async function addFolder() {
  let p = "";
  if (window.shell?.pickFolder) p = await window.shell.pickFolder();
  else p = prompt("The folder where your games are (for example /home/you/Games):") || "";
  if (p) { const r = await act("add_folder", { path: p }); if (r.ok) toast("Looking for games in that folder..."); }
}

// ---------------------------------------------------------------- what the player does
document.addEventListener("click", async e => {
  if (!e.target.closest("#menu")) closeMenu();
  const t = e.target.closest("[data-go],[data-act],[data-join],[data-pick],[data-game],[data-hostgame],[data-solo],[data-seats],[data-stream],[data-filter],[data-answer],[data-menu],[data-chat],[data-copy],[data-lobby],[data-emu],[data-unblock],[data-unfolder]");
  if (!t) return;
  const d = t.dataset;
  if (d.go) { e.preventDefault(); ui.page = d.go === "welcome" ? "arcade" : d.go; return draw(); }
  if (d.filter) { ui.filter = d.filter; return draw(); }
  if (d.pick) { ui.pick = +d.pick; ui.stream = false; delete drafts[":r-title"]; return draw(); }
  if (d.game) { ui.game = +d.game; ui.page = "game"; return draw(); }
  if (d.hostgame) { ui.pick = +d.hostgame; ui.stream = false; delete drafts[":r-title"]; ui.page = "host"; return draw(); }
  if (d.solo) { const r = await act("play_solo", { game: +d.solo }); if (r.ok) toast("Starting the game..."); return; }
  if (d.seats) { ui.seats = +d.seats; return draw(); }
  if (d.stream) { ui.stream = d.stream === "1"; return draw(); }
  if (d.chat) { ui.chatTab = d.chat; return draw(); }
  if (d.join) { await act("join", { room: d.join }); ui.page = "room"; return draw(); }
  if (d.answer) return act("answer", { req: d.answer, accept: d.yes === "1" });
  if (d.menu) { e.stopPropagation(); const r = t.getBoundingClientRect(); return menu(r.left, r.bottom + 4, d.menu, d.name, d.seated === "1"); }
  if (d.copy) { try { await navigator.clipboard.writeText(d.copy); toast("Copied."); } catch { toast(d.copy); } return; }
  if (d.lobby) return act("connect", { address: d.lobby });
  if (d.emu) return act("emulator", { emu: d.emu, path: "" });
  if (d.unblock) return act("unblock", { player: d.unblock });
  if (d.unfolder) return act("remove_folder", { path: d.unfolder });
  switch (d.act) {
    case "hide-howto": ui.howto = false; try { localStorage.setItem("howto", "hidden"); } catch {} return draw();
    case "add-folder": return addFolder();
    case "rescan": toast("Scanning your game folders..."); return act("rescan");
    case "post": {
      const r = await act("post", { game: ui.pick, title: document.getElementById("r-title").value, seats: ui.seats,
        note: document.getElementById("r-note").value, auto: document.getElementById("r-auto").checked,
        public: document.getElementById("r-pub").checked, stream: ui.stream });
      if (r.ok) { delete drafts[":r-title"]; delete drafts[":r-note"]; ui.note = ""; ui.page = "room"; draw(); }
      return;
    }
    case "close-room": if (confirm("Close the room? Everyone in it leaves.")) { await act("close_room"); ui.page = "arcade"; draw(); } return;
    case "leave": await act("leave"); ui.page = "arcade"; return draw();
    case "cancel-join": await act("cancel_join"); ui.page = "arcade"; return draw();
    case "connect": return act("connect", { address: document.getElementById("lobby-addr").value });
    case "dolphin-code": return act("update_room", { code: document.getElementById("dolphin-code").value });
  }
});

document.addEventListener("change", e => {
  const id = e.target.id;
  if (id === "r-auto") ui.auto = e.target.checked;
  if (id === "r-pub") ui.pub = e.target.checked;
  if (id === "live-auto") act("update_room", { auto: e.target.checked });
  if (id === "run-lobby") act("settings", { run_lobby: e.target.checked });
  if (id === "chat-filter") act("settings", { chat_filter: e.target.checked });
  if (id === "sounds") act("settings", { sounds: e.target.checked });
});
document.addEventListener("input", e => {
  if (e.target.matches("input:not([type=checkbox]):not([type=radio]), textarea")) drafts[fieldKey(e.target)] = e.target.value;
  if (e.target.id === "r-note") ui.note = e.target.value;
});

document.addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  for (const el of f.querySelectorAll("input, textarea")) delete drafts[fieldKey(el)];
  if (f.id === "setup" || f.id === "rename") {
    const r = await act("setup", { name: f.name.value, age_ok: f.id === "rename" ? true : f.age.checked });
    if (r.ok && f.id === "rename") toast("Name saved.");
  }
  if (f.id === "codeform") {
    const code = f.code.value.trim().toUpperCase();
    if (code) { await act("join", { invite: code }); ui.page = "room"; draw(); }
  }
  if (f.id === "chatform") {
    const i = document.getElementById("chatinput");
    const text = i.value.trim();
    if (!text) return;
    i.value = "";
    const tab = (S.myroom || S.seat?.room) && ui.chatTab === "room" ? "room" : "lobby";
    act("chat", { to: tab, text });
  }
});
document.addEventListener("keydown", e => { if (e.key === "Escape") closeMenu(); });

// the game page's TV: the next screenshot every 6 seconds (classes only, so nothing else redraws)
setInterval(() => {
  const imgs = document.querySelectorAll(".tv-screen img");
  if (imgs.length < 2 || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  ui.slide = ((ui.slide || 0) + 1) % imgs.length;
  imgs.forEach((im, k) => im.classList.toggle("on", k === ui.slide));
}, 6000);

// netplay:// links (from a browser or a message): netplay://lobby?h=<host>&p=<port>[&code=<invite>]
window.addEventListener("netplay-link", async e => {
  let u; try { u = new URL(e.detail); } catch { return; }
  const h = u.searchParams.get("h"), p = u.searchParams.get("p") || "7777", code = u.searchParams.get("code");
  if (h) { await act("connect", { address: `${h}:${p}` }); toast(`Joining the lobby at ${h}...`); }
  if (code) setTimeout(() => act("join", { invite: code.toUpperCase() }), 1500);
});

// ---------------------------------------------------------------- live updates from the engine
function listen() {
  const es = new EventSource(`/api/events?t=${T}`);
  es.addEventListener("state", e => {
    const before = S;
    S = JSON.parse(e.data);
    if (before && !before.myroom && S.myroom) ui.page = "room";
    if (before?.seat?.waiting && S.seat && !S.seat.waiting) ui.page = "room";
    if (ui.pick === null && S.library.length) ui.pick = S.library.find(g => !g.problem)?.i ?? null;
    ui.seats = ui.seats || S.settings.seats || 2;
    draw();
  });
  es.addEventListener("notice", e => {
    const n = JSON.parse(e.data);
    toast(n.text, n.kind === "error" ? "error" : n.kind === "warn" ? "warn" : "info");
    if (n.sound) sound(n.sound);
  });
  es.onerror = () => { /* EventSource reconnects by itself */ };
}
listen();
