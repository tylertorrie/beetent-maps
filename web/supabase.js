/* Thin Supabase data layer for the web planner.
 *
 * Reads config from window.SUPABASE_CONFIG (config.js). If it's absent or still
 * on the placeholder values, the app runs in DEMO MODE: no network, and
 * listFields()/getField() serve the bundled sample_field.json so the shell is
 * usable with no backend.
 *
 * The `fields` table shape (written by the desktop's supabase_sync.py):
 *   company text, year text, name text, data jsonb, updated_at timestamptz
 *   unique(company, year, name)
 */
"use strict";

window.beeData = (function () {
  const cfg = window.SUPABASE_CONFIG || {};
  const configured =
    !!cfg.url && !!cfg.anonKey &&
    !cfg.url.includes("YOUR-PROJECT") && !cfg.anonKey.includes("YOUR-ANON");

  let sb = null;
  if (configured && window.supabase && typeof window.supabase.createClient === "function") {
    try {
      sb = window.supabase.createClient(cfg.url, cfg.anonKey);
    } catch (e) {
      console.warn("Supabase client init failed — falling back to demo mode:", e);
      sb = null;
    }
  }

  const isLive = () => sb !== null;

  // ---- demo fallback (bundled sample) ---------------------------------------
  let _demo = null;
  async function demoRows() {
    if (_demo) return _demo;
    try {
      const r = await fetch("sample_field.json", { cache: "no-store" });
      const data = await r.json();
      _demo = [{
        company: data.company || "Demo", year: String(data.year || ""),
        name: data.Name || "Sample field", data,
        updated_at: new Date().toISOString(),
      }];
    } catch (e) {
      _demo = [];
    }
    return _demo;
  }

  // ---- public API -----------------------------------------------------------
  return {
    live: isLive,
    mode() { return isLive() ? "live" : "demo"; },

    /** All fields (metadata only — no heavy `data` blob) for the list. */
    async listFields() {
      if (!isLive()) {
        return (await demoRows()).map((r) => ({
          company: r.company, year: r.year, name: r.name, updated_at: r.updated_at,
        }));
      }
      const { data, error } = await sb
        .from("fields")
        .select("company,year,name,updated_at")
        .order("company").order("year", { ascending: false }).order("name");
      if (error) throw error;
      return data || [];
    },

    /** The full field dict for one field. */
    async getField(company, year, name) {
      if (!isLive()) {
        const rows = await demoRows();
        const hit = rows.find((r) =>
          r.company === company && String(r.year) === String(year) && r.name === name);
        return hit ? hit.data : (rows[0] && rows[0].data) || null;
      }
      const { data, error } = await sb
        .from("fields").select("data")
        .eq("company", company).eq("year", String(year)).eq("name", name)
        .maybeSingle();
      if (error) throw error;
      return data ? data.data : null;
    },

    // ---- auth (no-op in demo mode) ------------------------------------------
    async currentUser() {
      if (!isLive()) return null;
      const { data } = await sb.auth.getUser();
      return data ? data.user : null;
    },
    async signIn(email, password) {
      if (!isLive()) throw new Error("No backend configured — running in demo mode.");
      const { data, error } = await sb.auth.signInWithPassword({ email, password });
      if (error) throw error;
      return data.user;
    },
    async signOut() {
      if (isLive()) { try { await sb.auth.signOut(); } catch (e) {} }
    },
    onAuth(cb) {
      if (!isLive()) return;
      sb.auth.onAuthStateChange((_evt, session) => cb(session ? session.user : null));
    },
  };
})();
