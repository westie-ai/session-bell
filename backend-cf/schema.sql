-- One namespaced KV table models every mailbox/registry the Vercel blob
-- layout had, minus the never-overwrite workarounds (D1 is consistent).
CREATE TABLE IF NOT EXISTS kv (
  ns TEXT NOT NULL,
  k  TEXT NOT NULL,
  v  TEXT NOT NULL,
  ts INTEGER NOT NULL,
  PRIMARY KEY (ns, k)
);
CREATE INDEX IF NOT EXISTS kv_prefix ON kv (ns, k, ts);
