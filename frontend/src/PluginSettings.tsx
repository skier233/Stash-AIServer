// =============================================================================
// Plugin Settings & Manager Panel (MVP)
//  - Local UI settings (backend URL override, interaction tracking toggle)
//  - Lists installed backend plugins (name/human/version/status)
//  - Shows update availability by comparing to catalog versions
//  - Source management: list/add/remove sources, refresh catalogs
//  - Browse a selected source's catalog and install/update/remove plugins
//  - Minimal styling via inline styles to avoid new CSS dependencies
//  - Exposed as window.AIPluginSettings and route /plugins/ai-settings (integration file registers it)
// =============================================================================

interface InstalledPlugin { name: string; version: string; status: string; required_backend: string; migration_head?: string|null; last_error?: string|null; human_name?: string|null; server_link?: string|null; }
interface Source { id: number; name: string; url: string; enabled: boolean; last_refreshed_at?: string|null; last_error?: string|null; }
interface CatalogEntry { plugin_name: string; version: string; description?: string; manifest?: any; }

// LocalStorage keys
const LS_BACKEND_URL = 'AI_BACKEND_URL_OVERRIDE';
const LS_INTERACTIONS = 'AI_INTERACTIONS_ENABLED';

// Use shared backend base helper when available. The build outputs each file as
// an IIFE so we also support the global `window.AIDefaultBackendBase` for
// consumers that execute before modules are loaded.
const defaultBackendBase = () => {
  const fn = (window as any).AIDefaultBackendBase;
  if (typeof fn !== 'function') throw new Error('AIDefaultBackendBase not initialized. Ensure backendBase is loaded first.');
  return fn();
};

// Small fetch wrapper adding JSON handling + error capture
async function jfetch(url: string, opts: any = {}): Promise<any> {
  const res = await fetch(url, { headers: { 'content-type': 'application/json', ...(opts.headers||{}) }, ...opts });
  const ct = (res.headers.get('content-type')||'').toLowerCase();
  let body: any = null;
  if (ct.includes('application/json')) { try { body = await res.json(); } catch { body = null; } }
  if (!res.ok) {
    const detail = body?.detail || res.statusText;
    throw new Error(detail || ('HTTP '+res.status));
  }
  return body;
}

const PluginSettings = () => {
  if ((window as any).AIDebug) console.debug('[PluginSettings] component render start');
  const React: any = (window as any).PluginApi?.React || (window as any).React;
  if (!React) { console.error('[PluginSettings] React not found'); return null; }

  // Core state
  const [backendBase, setBackendBase] = React.useState(() => defaultBackendBase());
  const [backendDraft, setBackendDraft] = React.useState(() => backendBase);
  // Using 'any' in generics because React reference might be untyped (window injection)
  const [installed, setInstalled] = React.useState([] as any as InstalledPlugin[]);
  const [sources, setSources] = React.useState([] as any as Source[]);
  const [catalog, setCatalog] = React.useState({} as Record<string, CatalogEntry[]>);
  const [pluginSettings, setPluginSettings] = React.useState({} as Record<string, any[]>);
  const [systemSettings, setSystemSettings] = React.useState([] as any[]);
  const [systemOpen, setSystemOpen] = React.useState(false);
  const [openConfig, setOpenConfig] = React.useState(null as string | null);
  const [selectedSource, setSelectedSource] = React.useState(null as string | null);
  const [loading, setLoading] = React.useState({installed:false, sources:false, catalog:false} as {installed:boolean; sources:boolean; catalog:boolean; action?:string});
  const [error, setError] = React.useState(null as string | null);
  const [addSrcName, setAddSrcName] = React.useState('');
  const [addSrcUrl, setAddSrcUrl] = React.useState('');
  const [interactionsEnabled, setInteractionsEnabled] = React.useState(() => localStorage.getItem(LS_INTERACTIONS) === '1');

  // Derived: update availability map plugin->latestVersionAcrossCatalogs
  const latestVersions = React.useMemo(() => {
    const map: Record<string,string> = {};
    for (const entries of Object.values(catalog) as any) {
      for (const c of entries as any[]) {
        const cur = map[c.plugin_name];
        if (!cur || isVersionNewer(c.version, cur)) map[c.plugin_name] = c.version;
      }
    }
    return map;
  }, [catalog]);

  function isVersionNewer(a: string, b: string) {
    // naive semver-ish compare fall back lexicographic
    try {
      const pa = a.split(/\.|-/).map(x=>parseInt(x,10)||0);
      const pb = b.split(/\.|-/).map(x=>parseInt(x,10)||0);
      for (let i=0;i<Math.max(pa.length,pb.length);i++) { const av=pa[i]||0, bv=pb[i]||0; if (av>bv) return true; if (av<bv) return false; }
      return false;
    } catch { return a > b; }
  }

  // Loaders
  const loadInstalled = React.useCallback(async () => {
    setLoading((l:any) => ({...l, installed:true}));
  try { const data = await jfetch(`${backendBase}/api/v1/plugins/installed?include_removed=false`); setInstalled(Array.isArray(data)?data:data||[]); } catch(e:any){ setError(e.message); }
    finally { setLoading((l:any) => ({...l, installed:false})); }
  }, [backendBase]);

  const loadSources = React.useCallback(async () => {
    setLoading((l:any) => ({...l, sources:true}));
    try { const data = await jfetch(`${backendBase}/api/v1/plugins/sources`); setSources(Array.isArray(data)?data:data||[]); } catch(e:any){ setError(e.message); }
    finally { setLoading((l:any) => ({...l, sources:false})); }
  }, [backendBase]);

  const loadCatalogFor = React.useCallback(async (name: string) => {
    setLoading((l:any) => ({...l, catalog:true}));
    try { const data = await jfetch(`${backendBase}/api/v1/plugins/catalog/${name}`); setCatalog((c:any) => ({...c, [name]: Array.isArray(data)?data:[]})); } catch(e:any){ setError(e.message); }
    finally { setLoading((l:any) => ({...l, catalog:false})); }
  }, [backendBase]);

  const refreshSource = React.useCallback(async (name: string) => {
    try { await jfetch(`${backendBase}/api/v1/plugins/sources/${name}/refresh`, { method:'POST' }); await loadCatalogFor(name); await loadInstalled(); await loadSources(); } catch(e:any){ setError(e.message); }
  }, [backendBase, loadCatalogFor, loadInstalled, loadSources]);

  const addSource = React.useCallback(async () => {
    if (!addSrcName || !addSrcUrl) return;
    try { await jfetch(`${backendBase}/api/v1/plugins/sources`, { method:'POST', body: JSON.stringify({ name: addSrcName, url: addSrcUrl, enabled: true }) }); setAddSrcName(''); setAddSrcUrl(''); await loadSources(); } catch(e:any){ setError(e.message); }
  }, [backendBase, addSrcName, addSrcUrl, loadSources]);

  const removeSource = React.useCallback(async (name: string) => {
    if (!confirm(`Remove source ${name}?`)) return;
    try { await jfetch(`${backendBase}/api/v1/plugins/sources/${name}`, { method:'DELETE' }); setCatalog((c:any) => { const n = {...c}; delete n[name]; return n; }); if (selectedSource === name) setSelectedSource(null); await loadSources(); } catch(e:any){ setError(e.message); }
  }, [backendBase, selectedSource, loadSources]);

  const installPlugin = React.useCallback(async (source: string, plugin: string, overwrite=false, installDependencies=false) => {
    try {
      await jfetch(`${backendBase}/api/v1/plugins/install`, { method:'POST', body: JSON.stringify({ source, plugin, overwrite, install_dependencies: installDependencies }) });
      await loadInstalled();
    } catch(e:any){ setError(e.message); }
  }, [backendBase, loadInstalled]);

  const startInstall = React.useCallback(async (source: string, plugin: string, overwrite=false) => {
    try {
      const plan = await jfetch(`${backendBase}/api/v1/plugins/install/plan`, { method:'POST', body: JSON.stringify({ source, plugin }) });
      const missing = (plan?.missing || []) as string[];
      if (missing.length) {
        alert(`Cannot install ${plan?.human_names?.[plugin] || plugin}. Missing dependencies: ${missing.join(', ')}`);
        return;
      }
      const deps = (plan?.dependencies || []) as string[];
      const already = new Set(plan?.already_installed || []);
      const needed = deps.filter(d => !already.has(d));
      let installDeps = false;
      if (needed.length) {
        const friendly = needed.map(name => plan?.human_names?.[name] || name).join(', ');
        if (!confirm(`Installing ${plan?.human_names?.[plugin] || plugin} will also install: ${friendly}. Continue?`)) return;
        installDeps = true;
      }
      await installPlugin(source, plugin, overwrite, installDeps);
    } catch(e:any){ setError(e.message); }
  }, [backendBase, installPlugin]);

  const updatePlugin = React.useCallback(async (source: string, plugin: string) => {
    try { await jfetch(`${backendBase}/api/v1/plugins/update`, { method:'POST', body: JSON.stringify({ source, plugin }) }); await loadInstalled(); } catch(e:any){ setError(e.message); }
  }, [backendBase, loadInstalled]);

  const removePlugin = React.useCallback(async (plugin: string) => {
    try {
      const plan = await jfetch(`${backendBase}/api/v1/plugins/remove/plan`, { method:'POST', body: JSON.stringify({ plugin }) });
      const human = (plan?.human_names?.[plugin] || installed.find((p: InstalledPlugin) => p.name === plugin)?.human_name || plugin) as string;
      const dependents = ((plan?.dependents || []) as string[]).filter(name => name !== plugin);
      let cascade = false;
      if (dependents.length) {
        const friendly = dependents.map(name => plan?.human_names?.[name] || installed.find((p: InstalledPlugin) => p.name === name)?.human_name || name).join(', ');
        if (!confirm(`Removing ${human} will also remove: ${friendly}. Continue?`)) return;
        cascade = true;
      } else {
        if (!confirm(`Remove plugin ${human}?`)) return;
      }
      await jfetch(`${backendBase}/api/v1/plugins/remove`, { method:'POST', body: JSON.stringify({ plugin, cascade }) });
      await loadInstalled();
    } catch(e:any){ setError(e.message); }
  }, [backendBase, loadInstalled, installed]);

  const loadPluginSettings = React.useCallback(async (pluginName: string) => {
    try {
      const data = await jfetch(`${backendBase}/api/v1/plugins/settings/${pluginName}`);
      setPluginSettings((p:any) => ({...p, [pluginName]: Array.isArray(data)?data:[]}));
    } catch(e:any) { setError(e.message); }
  }, [backendBase]);

  const savePluginSetting = React.useCallback(async (pluginName: string, key: string, value: any) => {
    try {
      await jfetch(`${backendBase}/api/v1/plugins/settings/${pluginName}/${encodeURIComponent(key)}`, { method:'PUT', body: JSON.stringify({ value }) });
      // update local copy
      setPluginSettings((p:any) => {
        const cur = (p[pluginName] || []).map((f:any) => f.key === key ? ({...f, value}) : f);
        return {...p, [pluginName]: cur};
      });
    } catch(e:any) { setError(e.message); }
  }, [backendBase]);

  // Initial loads
  React.useEffect(() => { loadInstalled(); loadSources(); }, [loadInstalled, loadSources]);
  // After sources load first time, auto refresh each source once to populate catalog
  const autoRefreshed = React.useRef(false);
  React.useEffect(() => {
    (async () => {
      if (autoRefreshed.current) return; // only once
      if (!sources.length) return;
      // If any catalog already populated, skip bulk auto-refresh
      const haveAny = Object.values(catalog).some(arr => Array.isArray(arr) && arr.length);
      if (haveAny) { autoRefreshed.current = true; return; }
      autoRefreshed.current = true;
      for (const s of sources) {
        try { await refreshSource(s.name); } catch(e:any){ /* ignore individual errors */ }
      }
    })();
  }, [sources, catalog, refreshSource]);
  // System settings initial load
  React.useEffect(() => { (async ()=> { try { const data = await jfetch(`${backendBase}/api/v1/plugins/system/settings`); setSystemSettings(Array.isArray(data)?data:[]); } catch(e:any){ /* ignore until user opens */ } })(); }, [backendBase]);

  // If selected source changes and we don't have catalog, load it
  React.useEffect(() => { if (selectedSource && !catalog[selectedSource]) loadCatalogFor(selectedSource); }, [selectedSource, catalog, loadCatalogFor]);

  // Auto-select official source or first available when sources arrive
  React.useEffect(() => {
    if (!selectedSource && sources.length) {
  const official = sources.find((s: any) => s.name === 'official');
      setSelectedSource(official ? official.name : sources[0].name);
    }
  }, [sources, selectedSource]);

  // Interaction toggle persistence
  React.useEffect(() => { 
    if (interactionsEnabled) localStorage.setItem(LS_INTERACTIONS,'1'); else localStorage.removeItem(LS_INTERACTIONS);
    // Propagate to tracker runtime if already loaded
    try {
      const tracker = (window as any).stashAIInteractionTracker;
      if (tracker) {
        if (typeof tracker.setEnabled === 'function') tracker.setEnabled(!!interactionsEnabled); else if (typeof tracker.configure === 'function') tracker.configure({ enabled: !!interactionsEnabled });
      }
    } catch {}
  }, [interactionsEnabled]);

  function saveBackendBase() {
    const clean = backendDraft.trim().replace(/\/$/, '');
    setBackendBase(clean);
    localStorage.setItem(LS_BACKEND_URL, clean);
    // Reload data with new base
    setInstalled([]); setSources([]); setCatalog({}); setSelectedSource(null);
    loadInstalled(); loadSources();
  }

  // UI helpers
  const sectionStyle: React.CSSProperties = { border: '1px solid #444', padding: '12px 14px', borderRadius: 6, marginBottom: 16, background: '#1e1e1e' };
  const headingStyle: React.CSSProperties = { margin: '0 0 8px', fontSize: 16 };
  const smallBtn: React.CSSProperties = { fontSize: 12, padding: '2px 6px', cursor: 'pointer' };
  const tableStyle: React.CSSProperties = { width: '100%', borderCollapse: 'collapse' };
  const thtd: React.CSSProperties = { border: '1px solid #333', padding: '4px 6px', fontSize: 12, verticalAlign: 'top' };

  // Compose installed plugin rows
  function renderInstalled() {
    if (!installed.length) return <div style={{fontSize:12, opacity:0.7}}>No plugins installed.</div>;
    return (
      <table style={tableStyle}>
        <thead><tr>
          <th style={thtd}>Plugin</th><th style={thtd}>Current Version</th><th style={thtd}>Latest Version</th><th style={thtd}>Actions</th>
        </tr></thead>
  <tbody>{installed.map((p:any) => {
          const latest = latestVersions[p.name];
            const updateAvailable = latest && isVersionNewer(latest, p.version);
            // Reinstall conditions: status error|removed with matching catalog entry
            const isInactive = p.status === 'removed' || p.status === 'error';
            return <tr key={p.name} style={{background: updateAvailable? '#262214': (p.status==='removed' ? '#201818' : (p.status==='error' ? '#2a1a1a':'transparent'))}}>
              <td style={thtd} title={p.name}>{p.human_name || p.name}</td>
              <td style={thtd}>{p.version}</td>
              <td style={thtd}>{latest || ''}</td>
              <td style={thtd}>
                  {updateAvailable && <button style={smallBtn} onClick={()=> {
                  // need to find which source has that version; naive: iterate catalogs
                  let sourceFor: string | null = null;
                  for (const [srcName, entries] of Object.entries(catalog) as any) { if ((entries as any[]).find((e:any) => e.plugin_name===p.name && e.version===latest)) { sourceFor=srcName; break; } }
                  if (sourceFor) updatePlugin(sourceFor, p.name); else alert('Latest version not found in loaded catalogs');
                }}>Update</button>}{' '}
                {isInactive && <button style={smallBtn} onClick={()=> {
                  // find any catalog entry with same or newer version to reinstall (overwrite)
                  let found: {source:string, version:string}|null = null;
                  for (const [srcName, entries] of Object.entries(catalog) as any) {
                    for (const e of (entries as any[])) {
                      if (e.plugin_name === p.name) { found = {source: srcName, version: e.version}; break; }
                    }
                    if (found) break;
                  }
                  if (found) startInstall(found.source, p.name, true); else alert('No catalog entry found to reinstall');
                }}>{p.status==='removed' ? 'Reinstall' : 'Retry'}</button>}{' '}
                <button style={smallBtn} onClick={()=>removePlugin(p.name)}>Remove</button>
                {' '}
                <button style={smallBtn} onClick={async ()=>{
                  if (openConfig === p.name) { setOpenConfig(null); return; }
                  setOpenConfig(p.name);
                  if (!pluginSettings[p.name]) await loadPluginSettings(p.name);
                }}>{openConfig===p.name ? 'Close' : 'Configure'}</button>
              </td>
            </tr>;
        })}</tbody>
      </table>
    );
  }

  function FieldRenderer({f, pluginName}: {f:any, pluginName:string}) {
    const t = f.type || 'string';
    const label = f.label || f.key;
    const val = f.value === undefined ? f.default : f.value;
    const changed = val !== undefined && val !== null && f.default !== undefined && val !== f.default;
    const inputStyle: React.CSSProperties = { padding:6, background:'#111', color:'#eee', border:'1px solid #333', minWidth:120 };
    const wrap: React.CSSProperties = { position:'relative', padding:'4px 4px 6px', border:'1px solid #2a2a2a', borderRadius:4, background:'#101010' };
    const resetBtn = (changed ? <button style={{position:'absolute', top:2, right:4, fontSize:9, padding:'1px 4px', cursor:'pointer'}} onClick={()=>savePluginSetting(pluginName, f.key, null)}>Reset</button> : null);
    const labelEl = <span>{label} {changed && <span style={{color:'#ffa657', fontSize:10}}>•</span>}</span>;
    if (t === 'boolean') {
      return <div style={wrap}><label style={{fontSize:12, display:'flex', alignItems:'center', gap:8}}>
        <input type="checkbox" checked={!!val} onChange={e=>savePluginSetting(pluginName, f.key, (e.target as any).checked)} /> {labelEl}
      </label>{resetBtn}</div>;
    }
    if (t === 'number') {
      return <div style={wrap}><label style={{fontSize:12}}>{labelEl}<br/>
        <input style={inputStyle} type="number" value={val ?? ''} onChange={e=>savePluginSetting(pluginName, f.key, Number((e.target as any).value))} />
      </label>{resetBtn}</div>;
    }
    if (t === 'select' || (f.options && Array.isArray(f.options))) {
      return <div style={wrap}><label style={{fontSize:12}}>{labelEl}<br/>
        <select style={inputStyle} value={val ?? ''} onChange={e=>savePluginSetting(pluginName, f.key, (e.target as any).value)}>
          <option value="">(unset)</option>
          {(f.options||[]).map((o:any,i:number)=><option key={i} value={o}>{typeof o === 'object' ? (o.value ?? o.key ?? JSON.stringify(o)) : String(o)}</option>)}
        </select>
      </label>{resetBtn}</div>;
    }
    // default string
    return <div style={wrap}><label style={{fontSize:12}}>{labelEl}<br/>
      <input style={inputStyle} value={val ?? ''} onChange={e=>savePluginSetting(pluginName, f.key, (e.target as any).value)} />
    </label>{resetBtn}</div>;
  }

  function SystemFieldRenderer({f}:{f:any}) {
    const t = f.type || 'string';
    const label = f.label || f.key;
    const val = f.value === undefined ? f.default : f.value;
    const changed = val !== undefined && val !== null && f.default !== undefined && val !== f.default;
    const inputStyle: React.CSSProperties = { padding:6, background:'#111', color:'#eee', border:'1px solid #333', minWidth:140 };
    const wrap: React.CSSProperties = { position:'relative', padding:'4px 4px 6px', border:'1px solid #2a2a2a', borderRadius:4, background:'#101010' };
    const resetBtn = (changed ? <button style={{position:'absolute', top:2, right:4, fontSize:9, padding:'1px 4px', cursor:'pointer'}} onClick={()=>saveSystemSetting(f.key, null)}>Reset</button> : null);
    const labelEl = <span>{label} {changed && <span style={{color:'#ffa657', fontSize:10}}>•</span>}</span>;
    if (t === 'boolean') return <div style={wrap}><label style={{fontSize:12, display:'flex', alignItems:'center', gap:8}}><input type="checkbox" checked={!!val} onChange={e=>saveSystemSetting(f.key, (e.target as any).checked)} /> {labelEl}</label>{resetBtn}</div>;
    if (t === 'number') return <div style={wrap}><label style={{fontSize:12}}>{labelEl}<br/><input style={inputStyle} type="number" value={val ?? ''} onChange={e=>saveSystemSetting(f.key, Number((e.target as any).value))} /></label>{resetBtn}</div>;
    if (t === 'select' || (f.options && Array.isArray(f.options))) return <div style={wrap}><label style={{fontSize:12}}>{labelEl}<br/><select style={inputStyle} value={val ?? ''} onChange={e=>saveSystemSetting(f.key, (e.target as any).value)}><option value="">(unset)</option>{(f.options||[]).map((o:any,i:number)=><option key={i} value={o}>{typeof o === 'object' ? (o.value ?? o.key ?? JSON.stringify(o)) : String(o)}</option>)}</select></label>{resetBtn}</div>;
    return <div style={wrap}><label style={{fontSize:12}}>{labelEl}<br/><input style={inputStyle} value={val ?? ''} onChange={e=>saveSystemSetting(f.key, (e.target as any).value)} /></label>{resetBtn}</div>;
  }

  const saveSystemSetting = React.useCallback(async (key: string, value: any) => {
    try { await jfetch(`${backendBase}/api/v1/plugins/system/settings/${encodeURIComponent(key)}`, { method:'PUT', body: JSON.stringify({ value }) }); setSystemSettings((cur:any[]) => cur.map(f=> f.key===key ? ({...f, value}) : f)); }
    catch(e:any){ setError(e.message); }
  }, [backendBase]);

  function renderSources() {
    return (
      <div>
        <div style={{display:'flex', gap:8, flexWrap:'wrap'}}>
          {sources.map((s:any) => {
            const isSel = s.name === selectedSource;
            return <div key={s.id} style={{border:'1px solid #333', padding:'6px 8px', borderRadius:4, background:isSel?'#2d2d2d':'#1a1a1a'}}>
              <div style={{fontSize:12}}><strong>{s.name}</strong></div>
              <div style={{fontSize:10, opacity:0.7, maxWidth:220, wordBreak:'break-all'}}>{s.url}</div>
              <div style={{display:'flex', gap:4, marginTop:4, flexWrap:'wrap'}}>
                <button style={smallBtn} onClick={()=> setSelectedSource(s.name)} disabled={isSel}>{isSel ? 'Selected' : 'Select'}</button>
                <button style={smallBtn} onClick={()=> refreshSource(s.name)}>Refresh</button>
                <button style={{...smallBtn, color:'#e66'}} onClick={()=> removeSource(s.name)}>Remove</button>
              </div>
              {s.last_error && <div style={{color:'#e99', fontSize:10, marginTop:4}}>Err: {s.last_error}</div>}
            </div>;
          })}
        </div>
        <div style={{marginTop:8}}>
          <input placeholder="source name" value={addSrcName} onChange={e=>setAddSrcName((e.target as any).value)} style={{fontSize:12, padding:4, marginRight:4}} />
          <input placeholder="source url" value={addSrcUrl} onChange={e=>setAddSrcUrl((e.target as any).value)} style={{fontSize:12, padding:4, marginRight:4, width:240}} />
          <button style={smallBtn} onClick={addSource}>Add Source</button>
        </div>
      </div>
    );
  }

  function renderCatalog() {
    if (!selectedSource) return <div style={{fontSize:12, opacity:0.7}}>Select a source to view its catalog.</div>;
    const entries = catalog[selectedSource];
    if (!entries) return <div style={{fontSize:12}}>Loading catalog…</div>;
    if (!entries.length) return <div style={{fontSize:12}}>No entries in catalog.</div>;
    return (
      <table style={tableStyle}>
        <thead><tr><th style={thtd}>Plugin</th><th style={thtd}>Version</th><th style={thtd}>Description</th><th style={thtd}>Installation Instructions</th><th style={thtd}>Actions</th></tr></thead>
        <tbody>{entries.map((e:any) => {
          const inst = installed.find((p:any) => p.name === e.plugin_name);
          const newer = inst && isVersionNewer(e.version, inst.version);
          const serverLink = e.manifest?.serverLink || e.manifest?.server_link;
          const docsLink = e.manifest?.installation || e.manifest?.install || e.manifest?.docs;
          return <tr key={e.plugin_name}>
            <td style={thtd}>{e.manifest?.humanName || e.manifest?.human_name || e.plugin_name}</td>
            <td style={thtd}>{e.version}</td>
            <td style={{...thtd, maxWidth:260}}>{e.description || e.manifest?.description || ''}</td>
            <td style={thtd}>
              {serverLink && <a href={serverLink} target="_blank" rel="noopener noreferrer" style={{display:'inline-block', fontSize:10, marginRight:6, color:'#4aa3ff', textDecoration:'underline'}}>Instructions</a>}
              {docsLink && <a href={docsLink} target="_blank" rel="noopener noreferrer" style={{display:'inline-block', fontSize:10, color:'#4aa3ff', textDecoration:'underline'}}>Docs</a>}
              {(!serverLink && !docsLink) && <span style={{fontSize:10, opacity:0.4}}>—</span>}
            </td>
            <td style={thtd}>
              {!inst && <button style={smallBtn} onClick={()=> { if (selectedSource) startInstall(selectedSource, e.plugin_name); }} disabled={!selectedSource}>Install</button>}
              {inst && newer && <button style={smallBtn} onClick={()=>updatePlugin(selectedSource, e.plugin_name)}>Update</button>}
              {inst && !newer && <span style={{fontSize:10, opacity:0.7}}>Installed</span>}
            </td>
          </tr>;
        })}</tbody>
      </table>
    );
  }

  return (
    <div style={{padding:16, color:'#ddd', fontFamily:'sans-serif'}}>
      <h2 style={{marginTop:0}}>AI Plugin Settings</h2>
      {error && <div style={{background:'#402', color:'#fbb', padding:8, marginBottom:12, border:'1px solid #600'}}>
        <strong>Error:</strong> {error} <button style={smallBtn} onClick={()=>setError(null)}>x</button>
      </div>}
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Local UI Settings</h3>
        <div style={{display:'flex', flexDirection:'column', gap:8, maxWidth:500}}>
          <label style={{fontSize:12, display:'flex', flexDirection:'column', gap:4}}>Backend Base URL
            <div style={{display:'flex', gap:4}}>
              <input value={backendDraft} onChange={e=>setBackendDraft((e.target as any).value)} style={{flex:1, padding:6, background:'#111', color:'#eee', border:'1px solid #333'}} />
              <button style={smallBtn} onClick={saveBackendBase} disabled={backendDraft.trim().replace(/\/$/,'')===backendBase}>Save</button>
            </div>
            <span style={{fontSize:10, opacity:0.7}}>Overrides autodetected base. Stored locally only.</span>
          </label>
          <label style={{fontSize:12}}>
            <input type="checkbox" checked={interactionsEnabled} onChange={e=>setInteractionsEnabled((e.target as any).checked)} style={{marginRight:6}} />
            Capture interaction events (local only)
          </label>
          <div style={{fontSize:10, opacity:0.7}}>Task dashboard: <a href="plugins/ai-tasks" style={{color:'#9cf'}}>Open</a></div>
          <div style={{fontSize:10, opacity:0.5}}>Restart backend button not yet implemented (needs backend endpoint).</div>
        </div>
      </div>
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Backend System Settings</h3>
        <div style={{margin:'4px 0 12px'}}>
          <button style={smallBtn} onClick={()=>{ if (!systemSettings.length) { jfetch(`${backendBase}/api/v1/plugins/system/settings`).then(d=> setSystemSettings(Array.isArray(d)?d:[])).catch(e=>setError(e.message)); } setSystemOpen((o:boolean)=>!o); }}>{systemOpen ? 'Hide':'Show'} Values</button>
        </div>
        {systemOpen && <div style={{display:'flex', flexWrap:'wrap', gap:12}}>
          {systemSettings.map((f:any)=><div key={f.key} style={{minWidth:220}}><SystemFieldRenderer f={f} /></div>)}
        </div>}
      </div>
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Installed Plugins {loading.installed && <span style={{fontSize:11, opacity:0.7}}>loading…</span>}</h3>
        {renderInstalled()}
  {openConfig && pluginSettings[openConfig] && <div style={{marginTop:12, padding:10, border:'1px solid #333', borderRadius:6, background:'#151515'}}>
          <div style={{display:'flex', justifyContent:'space-between', alignItems:'center'}}>
            <div style={{fontSize:13}}><strong>Configure {openConfig}</strong></div>
            <div style={{fontSize:12, opacity:0.7}}><button style={smallBtn} onClick={()=>{ setOpenConfig(null); }}>Close</button></div>
          </div>
          <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12, marginTop:10}}>
            {pluginSettings[openConfig].map((f:any)=>(<div key={f.key} style={{minWidth:200}}><FieldRenderer f={f} pluginName={openConfig} /></div>))}
          </div>
        </div>}
      </div>
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Sources {loading.sources && <span style={{fontSize:11, opacity:0.7}}>loading…</span>}</h3>
        {renderSources()}
      </div>
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Catalog {selectedSource && <span style={{fontSize:11, opacity:0.7}}>({selectedSource})</span>} {loading.catalog && <span style={{fontSize:11, opacity:0.7}}>loading…</span>}</h3>
        {renderCatalog()}
      </div>
    </div>
  );
};

(function expose(){
  if ((window as any).AIDebug) console.debug('[PluginSettings] exposing global');
  (window as any).AIPluginSettings = PluginSettings;
  (window as any).AIPluginSettingsMount = function(container: HTMLElement) {
    const React: any = (window as any).PluginApi?.React || (window as any).React;
    const ReactDOM: any = (window as any).ReactDOM || (window as any).PluginApi?.ReactDOM;
    if (!React || !ReactDOM) { console.error('[PluginSettings] React/DOM missing'); return; }
    ReactDOM.render(React.createElement(PluginSettings, {}), container);
  };
  try { window.dispatchEvent(new CustomEvent('AIPluginSettingsReady')); } catch {}
})();

export default PluginSettings;
