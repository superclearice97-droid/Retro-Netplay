// Test-only stand-in for the real host: a Chromium page that streams an animated canvas and speaks the
// same signaling + data-channel protocol as stream_host.py. Used to exercise web/guest.html for real.
(async (key) => {
  const log = window.simLog = { pads: [], ctl: [], state: [], offers: 0 };
  const cfg = await (await fetch('/cfg?t=' + key)).json();
  const pc = new RTCPeerConnection({ iceServers: [] });
  const cv = document.createElement('canvas'); cv.width = 640; cv.height = 360;
  const g = cv.getContext('2d'); let n = 0;
  setInterval(() => { n++; g.fillStyle = n % 2 ? '#123' : '#321'; g.fillRect(0, 0, 640, 360);
    g.fillStyle = '#fff'; g.font = '48px sans-serif'; g.fillText('frame ' + n, 40, 180); }, 16);
  const stream = cv.captureStream(60);
  stream.getTracks().forEach((t) => pc.addTrack(t, stream));
  const pad = pc.createDataChannel('pad', { ordered: false, maxRetransmits: 0 });
  const ctl = pc.createDataChannel('ctl');
  pad.binaryType = 'arraybuffer';
  pad.onmessage = (e) => { const d = new DataView(e.data);
    log.pads.push({ ver: d.getUint8(0), seq: d.getUint8(1), buttons: d.getUint32(2, true),
      ax: [6, 8, 10, 12].map((o) => d.getInt16(o, true)), lt: d.getUint8(14), rt: d.getUint8(15) }); };
  ctl.onopen = () => { ctl.send(JSON.stringify({ t: 'slot', n: 2 }));
    setInterval(() => ctl.send(JSON.stringify({ t: 'host', s: { captureAgeMs: 1.5, convertMs: 0.8, encodeMs: 2.5, encodeP95: 3.1, payMs: 0.2, totalMs: 3.6, frames: 100 } })), 500); };
  ctl.onmessage = (e) => { const m = JSON.parse(e.data); log.ctl.push(m);
    if (m.t === 'ping') ctl.send(JSON.stringify({ t: 'pong', ts: m.ts })); };
  const post = (m) => fetch('/sig?t=' + key, { method: 'POST', body: JSON.stringify(m) });
  pc.onicecandidate = (e) => { if (e.candidate) post({ type: 'ice', candidate: e.candidate.candidate, sdpMLineIndex: e.candidate.sdpMLineIndex }); };
  pc.onconnectionstatechange = () => log.state.push(pc.connectionState);
  (async () => { for (;;) {
    const r = await fetch('/sig?t=' + key + '&wait=20'); if (r.status === 404) return;
    for (const m of (await r.json()).msgs) {
      if (m.type === 'hello') { log.offers++; await pc.setLocalDescription(await pc.createOffer()); await post({ type: 'offer', sdp: pc.localDescription.sdp }); }
      else if (m.type === 'answer') await pc.setRemoteDescription({ type: 'answer', sdp: m.sdp });
      else if (m.type === 'ice') await pc.addIceCandidate({ candidate: m.candidate, sdpMLineIndex: m.sdpMLineIndex }).catch(() => {});
    } } })();
  return cfg.role;
})
