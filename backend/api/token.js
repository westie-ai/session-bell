import { put } from '@vercel/blob';
import { authed, listAll } from '../lib/util.js';

const HEX = /^[0-9a-f]{16,400}$/;

export default async function handler(req, res) {
  const P = authed(req, res);
  if (!P) return;

  if (req.method === 'POST') {
    const { pts_token: pts, update_token: update, ended_token: ended } = req.body || {};
    if (pts && HEX.test(pts)) {
      await put(`${P}/pts/${pts}`, '1', { access: 'public', addRandomSuffix: false, allowOverwrite: true });
    }
    if (update && HEX.test(update)) {
      await put(`${P}/dash/${Date.now()}-${update}`, '1', { access: 'public', addRandomSuffix: false });
    }
    if (ended && HEX.test(ended)) {
      await put(`${P}/dashended/${ended}`, '1', { access: 'public', addRandomSuffix: false, allowOverwrite: true });
    }
    const { device_token: device } = req.body || {};
    if (device && HEX.test(device)) {
      await put(`${P}/devices/${device}`, '1', { access: 'public', addRandomSuffix: false, allowOverwrite: true });
    }
    if (req.body?.reset_dashboard) {
      // App was reinstalled — every previous activity is dead; retire all tokens.
      const dash = await listAll(`${P}/dash/`);
      await Promise.all(dash.map((b) => {
        const m = b.pathname.match(/\/dash\/\d+-([0-9a-f]+)$/);
        return m
          ? put(`${P}/dashended/${m[1]}`, '1', { access: 'public', addRandomSuffix: false, allowOverwrite: true })
          : null;
      }));
    }
    return res.json({ ok: true });
  }

  const ptsBlobs = await listAll(`${P}/pts/`);
  const dashBlobs = await listAll(`${P}/dash/`);
  const endedSet = new Set(
    (await listAll(`${P}/dashended/`)).map((b) => b.pathname.split('/dashended/')[1]));
  let dashboard = null;
  for (const b of dashBlobs) {
    const m = b.pathname.match(/\/dash\/(\d+)-([0-9a-f]+)$/);
    if (m && !endedSet.has(m[2]) && (!dashboard || Number(m[1]) > dashboard.ts)) {
      dashboard = { ts: Number(m[1]), token: m[2] };
    }
  }
  const deviceBlobs = await listAll(`${P}/devices/`);
  res.json({
    pts: ptsBlobs.map((b) => b.pathname.split('/pts/')[1]).filter((t) => HEX.test(t)),
    devices: deviceBlobs.map((b) => b.pathname.split('/devices/')[1]).filter((t) => HEX.test(t)),
    dashboard,
  });
}
