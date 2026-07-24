# Gater-on-OMERO: Per-User Pod Deployment Plan (NYU Cancer Center)

**Goal:** Deploy the Gater app for NYU Cancer Center researchers so they can visualize/gate
their OMERO data within their existing workflow. Add an "Open in Gater" button to the OMERO
UI that spawns a **dedicated per-user Gater pod** on Kubernetes.

**Why per-user pods:** Gater holds the active dataset in module-level globals
(`config/seg/zarray/channels/...` in `data_model.py`) and has no authentication. A per-user
pod (JupyterHub spawner pattern) gives each researcher an isolated process, so the
global-state limitation stops mattering.

**Immediate code gap found:** the `Dockerfile` does NOT install `omero-py`/`zeroc-ice`, yet
`data_model.py` now does `from omero.gateway import BlitzGateway`. The current image would
fail on any OMERO call. Fixing this is Phase 0.

---

## 1. Target architecture (JupyterHub spawner pattern)

```
  Researcher browser
    │ 1. logged into OMERO.web, clicks "Open in Gater" on an image
    ▼
  OMERO.web (+ Gater plugin)
    │ 2. POST {omero_image_id, user identity, session key}
    ▼
  SPAWNER / HUB  ── 3. create ──▶  Per-user GATER POD (Deployment + Service + PVC)
    - authenticates request                    - env: OMERO_SESSION, IMAGE_ID, base path
    - creates/reuses per-user pod (K8s API)     - connects to OMERO AS THAT USER
    - registers route                           - 4. streams tiles from OMERO:4064
    - culls idle pods
    │ 6. redirect user to /gater/<user>/
    ▼
  PROXY / INGRESS  ── 5. route /gater/<user>/ ──▶  GATER POD (this user only)
```

The click happens inside an authenticated OMERO session, so the user's OMERO identity (and
ideally a session key) can be passed to the spawner; the Gater pod then connects to OMERO
**as that user** (replacing the hardcoded `root`).

---

## 2. Build approach — two options

| | **Option A — JupyterHub / KubeSpawner (recommended)** | **Option B — Custom spawner microservice** |
|---|---|---|
| Reuse | KubeSpawner (pods), configurable-http-proxy (routing), authenticators, idle-culler | Nothing |
| Custom code | Small: OMERO/NYU authenticator + config to launch Gater as single-user server | Large: lifecycle, routing, auth, culling, TLS |
| Effort | Medium | High |
| Risk | JupyterHub expects a notebook-ish server; needs `jupyter-server-proxy` or wrapper to front a Flask app | Own every edge case |

**Recommendation:** Option A (JupyterHub + Zero-to-JupyterHub Helm chart). Adapt it to launch
the Gater container as the single-user server and authenticate via OMERO/NYU SSO. Option B
only if NYU constraints rule out JupyterHub.

---

## 3. Component breakdown, dependencies & tools

### 3.1 OMERO.web "Open in Gater" plugin
Django app packaged as a Python module, registered via `omero.web.apps`; adds a toolbar/right-panel
button; sends selected `image_id` + user identity to the spawner. **Needs ability to install a
custom OMERO.web app + restart web service.**

### 3.2 Spawner / Hub
- Option A: JupyterHub (Helm `jupyterhub/jupyterhub`), `KubeSpawner`, custom `Authenticator`.
- Option B: FastAPI/Flask + `kubernetes` Python client creating Deployment+Service, plus idle culler.
- Both need: **ServiceAccount + RBAC Role** to create/delete pods/deployments/services in the namespace.

### 3.3 Proxy / routing
- Option A: configurable-http-proxy (bundled) → automatic per-user path routing.
- Option B: Ingress + per-user Service, or dynamic reverse proxy updated by spawner.
- Both need: **Ingress controller**, a **hostname**, **TLS cert** (cert-manager or NYU cert).

### 3.4 Per-user Gater pod
Existing Gater container, parameterized at launch (OMERO session key, image id, base path).
Needs: image in a **cluster-pullable registry** (Docker Hub workflow exists), **per-user PVC**
for gates/uploads/config, resource requests/limits.

### 3.5 Gater code changes required (regardless of A vs B)
1. **Fix Dockerfile** — add `omero-py` + `zeroc-ice` (Ice build is the fiddly part).
2. **Per-user OMERO credentials** — replace hardcoded `BlitzGateway('root','omero',host='localhost',...)`
   with env/config (`OMERO_HOST`, `OMERO_PORT`, per-user session key). Code has the
   `# TODO: make host/port/credentials configurable` hook already.
3. **Base-path awareness** — app must work under `/gater/<user>/` (Flask routes, static asset
   URLs, client-side absolute paths). Classic proxied-Flask breakage point.
4. **Launch-time dataset selection** — accept OMERO image id at startup so the pod opens directly
   on the chosen image instead of the upload form.
5. **Per-pod auth guard** — reject requests lacking a valid token so users can't reach each
   other's pods by guessing URLs.

---

## 4. Authentication design (drives everything)

| Model | How it works | Precondition |
|---|---|---|
| **A. Trust OMERO session** (simplest) | Button visible only to logged-in OMERO user; OMERO passes signed token/session key to spawner; pod connects to OMERO as that user | OMERO.web can hand out a usable session key / signed identity |
| **B. NYU SSO everywhere (OIDC/SAML)** | Both OMERO and hub authenticate against NYU IdP; one login covers both | NYU issues OIDC client / SAML SP AND OMERO wired to same IdP |
| **C. NYU SSO for hub + separate OMERO login** | NYU SSO to reach hub; Gater pod needs OMERO creds separately | NYU SSO ≠ OMERO auth; requires credential mapping |

**The key fork:** "does NYU auth cover OMERO, or are they separate?" decides B (best UX) vs A/C.
Get this answer first.

---

## 5. Storage & data movement
- **Imagery:** stays in OMERO, streamed live via BlitzGateway (tile path already implemented). No copy for visualization.
- **Per-user working data** (gates, uploaded CSVs, saved config): **per-user PVC** so it survives pod culling. Decide retention policy.
- **"Move data local ⇄ OMERO":** clarify "local" = pod (ephemeral) vs browser (download/upload).
  Writing results back into OMERO (annotations/attachments) is a **new feature** to scope separately.

---

## 6. Dependencies / tools / resources — summary

| Category | Items |
|---|---|
| Cluster access | Namespace RBAC to create Deployments/Services/PVCs; ServiceAccount for spawner; quota headroom |
| Orchestration | Helm; JupyterHub (z2jh) or custom spawner (`kubernetes` py client) |
| Networking | Ingress controller, DNS hostname, TLS cert, pod→OMERO:4064 path |
| Images | Gater image (fixed Dockerfile w/ omero-py) in cluster-pullable registry |
| Storage | StorageClass for per-user PVCs; per-user sizing |
| OMERO side | Install OMERO.web app; OMERO version/permissions; session-key or SSO integration |
| Identity | NYU IdP details (OIDC client or SAML metadata); OMERO↔NYU account mapping |
| Gater code | Dockerfile fix, configurable OMERO creds, base-path, launch-time image id, per-pod auth guard |
| Compliance | HIPAA/PHI review, audit logging, data-at-rest encryption |

---

## 7. ★ Items to confirm with researcher / NYU IT (blocking = ★)

### A. Authentication & identity (highest priority)
1. ★ Does NYU use a single SSO (Shibboleth/SAML or Azure AD/OIDC), and does OMERO authenticate
   against that same NYU identity — or do researchers have separate OMERO accounts? (Decides Model A/B/C.)
2. ★ Can NYU IT issue an OIDC client (id/secret + endpoints) or SAML SP registration for our hub?
   Who owns the request and what's the lead time?
3. Is there a mapping between NYU username and OMERO username (same string)?
4. Can OMERO.web hand the plugin a per-user session key to open BlitzGateway as that user? Or only an admin/service account?

### B. OMERO server
5. ★ What OMERO version, and is it OMERO.web (so we can install a custom app)? Who administers it?
6. ★ Are we allowed to install a custom OMERO.web app + restart the web service? Self-hosted by lab or NYU central IT?
7. Is OMERO reachable from the K8s cluster on port 4064 (and 4063)? Firewall/VPN between them?
8. OMERO permissions model — group visibility (private/read-only/read-annotate)? (Per-user vs service account.)
9. Is OMERO in the same NYU network zone as the Kubernetes cluster?

### C. Kubernetes / infrastructure
10. ★ What does NYU Kubernetes access permit in your namespace — create Deployments, Services, PVCs,
    RBAC Roles/ServiceAccounts? (Spawner needs pod-creation rights.)
11. ★ Is there an Ingress controller, and can you get a hostname + TLS cert (e.g. `gater.med.nyu.edu`)?
12. Resource quotas (CPU/RAM/GPU/storage) on the namespace, and how many concurrent researchers? (Sizing.)
13. Which container registry can the cluster pull from — public Docker Hub or NYU-internal (image-pull secrets)?
14. Is JupyterHub already available/blessed at NYU centrally? (Might reuse it.)
15. What StorageClass for per-user PVCs, and is data-at-rest encrypted?

### D. Data governance / compliance (do not skip for a cancer center)
16. ★ Is any OMERO data PHI / patient-identifiable, and does this fall under HIPAA or an IRB protocol?
    (May impose encryption, audit logging, access control, pod placement requirements.)
17. Audit-logging requirements (who opened which image when)?
18. Data retention: should per-user gates/results persist between sessions, how long, deletion rules?

### E. Product / UX
19. Where should the button live — single image, dataset, or multiple selected images? What should Gater open on?
20. Idle timeout policy — after how long should an unused Gater pod be culled?
21. Should researchers save gating results back into OMERO (annotations/attachments), or only download locally?

---

## 8. Suggested phasing
0. **De-risk:** get §7 answers; fix Dockerfile (omero-py); prove one Gater pod connects to OMERO as a specified user and renders an image. (No spawner yet.)
1. **Manual multi-user:** deploy JupyterHub/spawner in namespace; per-user Gater pod via manual URL, with SSO/auth working.
2. **OMERO integration:** OMERO.web button → spawner handshake (image id + identity); pod opens on chosen image.
3. **Hardening:** per-pod auth guard, idle culling, PVC retention, TLS, resource limits, audit logging, compliance sign-off.

---

## 9. Top risks
- `omero-py`/`zeroc-ice` in-container build is historically painful — spike early (Phase 0).
- Base-path/URL-prefix breakage under per-user path routing = classic blank-page cause — test early.
- HIPAA/PHI could reshape the whole design — surface before building.
- NYU IT approval latency (OIDC client / Ingress hostname / RBAC) can be the long pole — request immediately.
- **Slow datasource switch (known perf issue, flagged 2026-07-23).** Selecting a previously-uploaded datasource from the Data Sources menu is slow. It is **not** a re-import from OMERO — the menu links to the plain viewer route `/<datasource>`, which calls `load_datasource()` (`data_model.py`), not `open_from_omero_run()`. Two causes: (1) **Pre-existing:** Gater holds only ONE datasource in memory (module-level globals), so every switch does a full from-scratch reload — dominated by `pd.read_csv()` of the whole quantification CSV (~100 MB for the kidney set → seconds–tens of seconds), plus ball-tree load and a coarse-plane OMERO read. (2) **Added by the OMERO render-storage work:** `get_saved_channel_list` / `get_saved_gating_list` now hit OMERO first on load (two annotation round-trips, each possibly forcing a reconnect, plus a seeding write if absent), where they previously read only local SQLite. **Levers when addressed:** cache `get_omero_json` per `(image_id, kind)` + keep seeding off the load critical path (undoes the added cost); and an LRU of the last N datasources' DataFrame+ball_tree, or a parquet/feather sidecar for faster reload than `read_csv` (tackles the pre-existing cost). Matters more per-pod where cold loads are frequent.

---

## 10. OMERO.web Integration (Phase 2 detail)

Grounded in the OMERO docs (Web.html, CreateApp.html, EditingOmeroWeb.html, omero-webtest,
Python.html, Deployment.html). OMERO.web is **Django**; extend it with a **custom app**, never by
editing core. Only `webgateway` + `api` are stable public APIs, so use the documented plugin
**hooks** (`right_plugins`/`top_links`) — NOT webclient template overrides.

### 10.1 The `omero-gater` app
One small, pip-installable Django app (scaffold via `cookiecutter-omero-webapp`):

```
omero-gater/
├── setup.py / setup.cfg              # pip-installable (like omero-webtest)
└── omero_gater/
    ├── settings.py                   # CUSTOM_SETTINGS_MAPPINGS: spawner_url, base_url, omero_host
    ├── urls.py                       # path('open/<int:image_id>/', views.open_in_gater)
    ├── views.py                      # the handoff view (below)
    └── templates/gater/webclient_plugins/
        └── right_plugin.gater.js.html   # the "Open in Gater" button
```

### 10.2 The button (right-panel plugin)
A `right_plugins` template renders "Open in Gater" whenever an image is selected. Config
(mirrors omero-webtest; format `[label, template_path, plugin_id]`):

```bash
omero config append omero.web.apps '"omero_gater"'
omero config append omero.web.ui.right_plugins \
  '["Gater", "gater/webclient_plugins/right_plugin.gater.js.html", "gater_tab"]'
omero config set omero.web.gater.spawner_url '"http://gater-hub.gater.svc:8081"'
omero config set omero.web.gater.base_url    '"https://gater.med.nyu.edu"'
omero config set omero.web.gater.omero_host  '"omero.med.nyu.edu"'
omero web restart
```

### 10.3 The handoff view (brains of the OMERO side)
`@login_required()` injects `conn` — a BlitzGateway ALREADY authenticated as the researcher.
We read the image id + the user's live session UUID and hand off to the spawner:

```python
# omero_gater/views.py
from omeroweb.webclient.decorators import login_required
from django.shortcuts import redirect
import requests
from omero_gater import settings

@login_required()
def open_in_gater(request, image_id, conn=None, **kwargs):
    ec = conn.getEventContext()
    session_uuid = ec.sessionUuid          # ← linchpin: user's live session token
    username     = ec.userName
    requests.post(f"{settings.SPAWNER_URL}/launch", json={
        "user": username, "omero_session": session_uuid,
        "omero_host": settings.OMERO_HOST, "image_id": int(image_id),
    }, timeout=30)
    return redirect(f"{settings.GATER_BASE_URL}/user/{username}/?image={image_id}")
```

### 10.4 The identity handshake (retires the hardcoded-root + no-auth blockers)
OMERO sessions are shareable by UUID — the Gater pod JOINS the researcher's session (no password):

```python
# Gater pod — replaces BlitzGateway('root','omero',host='localhost',...) in data_model.py
import omero
from omero.gateway import BlitzGateway
client = omero.client(OMERO_HOST, 4064)
client.joinSession(OMERO_SESSION_UUID)     # from env, passed by spawner
conn = BlitzGateway(client_obj=client)
conn.c.enableKeepAlive(60)
```

Result: pod reads tiles AS the researcher → OMERO's own permissions apply, no password leaves
OMERO.web. **Because the button only appears inside an authenticated OMERO session, OMERO login
IS the authentication** — NYU-SSO-in-front-of-the-hub becomes optional hardening, not a
prerequisite. ⚠️ Caveat: a joined session shares the original's `timeToIdle`/`timeToLive`;
verify NYU's OMERO timeout and use keepAlive / session renewal.

### 10.5 Pixel-data path decision
- **A. Direct BlitzGateway `joinSession` (RECOMMENDED)** — pod calls `getTile` per channel
  (Gater already does this via `_get_omero_tile`). Needs omero-py/Ice in the pod; direct
  pod→OMERO:4064 path. Correct data model for Gater's per-channel raw uint16 tiles.
- **B. Proxy via webgateway/JSON API** — uses the stable public API, no Ice in pod, BUT returns
  RGB composites, not per-channel raw tiles → larger rework. Not recommended.

### 10.6 Revised end-to-end sequence
```
Select image in OMERO.web → right_plugin shows "Open in Gater" (image_id known)
  → GET /gater/open/<image_id>/  (@login_required injects conn as researcher)
  → view reads sessionUuid + image_id → POST to Spawner/JupyterHub
  → Hub ensures user's Gater pod (env: OMERO_SESSION, OMERO_HOST, IMAGE_ID)
  → redirect → https://gater.nyu.edu/user/<user>/?image=<id>
  → pod client.joinSession(uuid) → BlitzGateway → streams tiles AS the researcher
```

### 10.7 Extra confirmation items (OMERO-specific — add to §7)
22. ★ Can we `pip install` a custom app into their OMERO.web env and run `omero web restart`
    (do we have admin, or must their sysadmin apply it)?
23. ★ What are OMERO's session `timeToIdle`/`timeToLive`? (Decides joined-session survival / renewal.)
24. Is OMERO.web served the standard way (gunicorn + nginx) so the config/restart flow applies?
25. Confirm OMERO version ≥ 5.6 (required for the app/plugin mechanism).
26. Any reverse-proxy/CSP that would block the redirect from OMERO.web's domain to gater.nyu.edu
    (cross-origin/cookie considerations)?

---

## 11. OMERO-side render/config storage & session restore — progress (2026-07-24)

Goal: make **OMERO the source of truth** for a datasource's render/config so a per-user pod
can be stateless and a researcher's saved session restores on any host. Foundational for the
Phase 2 per-user-pod model (§1, §5). Fine-grained per-commit detail lives in
`OMERO_INTEGRATION_REPORT.md` §11 (Commit Log).

### 11.1 What was built

- **Storage model — JSON FileAnnotations keyed by `(image, kind, dataset_name)`.** A datasource's
  config entry **and** render state (channel colors/ranges, gating thresholds/lassos) are mirrored
  to the OMERO Image as JSON FileAnnotations under stable namespaces
  (`gater.vida.nyu/config`, `.../render-state/channel_list`, `.../render-state/gating_list`).
  The namespace is stable per *kind* (so discovery is one indexed namespace query); the annotation
  **description** carries the exact dataset name. A re-save of the same name replaces only its own
  annotation, while **different names on the same image coexist** (upload the same image under
  multiple names, each its own datasource). Helpers in `data_model.py`: `put_omero_json` /
  `get_omero_json` / `list_omero_config_names` / `inherit_render_state` (best-effort, reconnect-retry).
- **Dual-write, OMERO-primary.** Saves write both local SQLite (fallback) and OMERO; reads try
  OMERO first then fall back to SQLite; seed-on-load copies local→OMERO when absent. Local
  `config.json` stays the bootstrap loader (it is also the datasource→`omero_image_id` index).
- **Session restore.** Opening a saved config reconstructs the datasource from `gater_config.json`
  (`_restore_config_entry` re-points env paths), **skips the channel-match wizard**, and lands in
  the viewer; **auto-restore-on-open** (`main.js`) then applies the saved channel/gating render state.
- **Selection UIs.** (a) OMERO.web-side: the "Open in Gater" tab lists saved configs for the image
  ("Open" dropdown → New / restore a saved name → Gater skips the wizard). (b) Gater-side wizard
  "Start from" dropdown seeds a new datasource's render state from a prior config on the same image
  (secondary fallback). (c) Panel buttons/labels relabeled Database→OMERO.

### 11.2 Version state
- **Committed baseline `v1.35_gater`:** Fix-(a) tile-OOM bound, initial render/config storage,
  restore-on-reopen, UI relabels.
- **Uncommitted (working, manually verified 2026-07-24), to commit as ~`v1.36_gater`:**
  `(image, name)` re-keying; wizard "Start from" dropdown; OMERO.web "Open" selector (omero-gater
  plugin); auto-restore-on-open; idempotent `applyChannels` rewrite (range + toggle/flash fixed).

### 11.3 Follow-up flags — OPEN items to address later

- **FLAG 1 — Channel COLOR not restored (deprioritized; "OK for now").** Restore brings back channel
  active-state + intensity ranges + gating correctly, but channel **color stays default (white)**.
  Not fully root-caused: `viewerManager.channel_add` reads `colorConnector[idx].color` and
  `imageViewer.updateChannelColors` only applies to an already-active channel — both expect a
  `d3.rgb`; fixed the connector format + fire-after-activation but color still doesn't land. Likely
  how the WebGL shader consumes the color, or the event still not landing. (`channelList.js`
  `applyChannels`; `imageViewer.updateChannelColors`; `viewerManager.channel_add`.)
- **FLAG 2 — "Load Gating from OMERO" makes the segmentation mask vanish.** Gate *values* stay
  correct on re-apply, but the seg-mask overlay disappears when the seg tab is open — a WebGL render
  desync (`imageViewer.js` `bindGatings` / `show_subset`). Need to know how the seg mask is toggled
  (channel panel vs gating overlay vs separate control) to fix.
- **FLAG 3 — Slow datasource switch (also §9).** Full-CSV reload from single-slot module globals +
  the OMERO annotation round-trips this work added. Levers: cache `get_omero_json` per `(image,kind)`
  + keep seeding off the load hot path; LRU of last-N datasources or a parquet/feather sidecar for
  faster reload than `read_csv`.
- **FLAG 4 — config.json paths are local/container-absolute.** In deployment they should point to
  **OMERO paths**. `_restore_config_entry` is the seam that re-points them (today → local downloaded
  files).
- **FLAG 5 — No-local-persist / live-stream quant+seg (stateless pod goal).** Quant CSV +
  segmentation are still downloaded and converted to disk in `open_from_omero_run` (segmentation must
  be a local pyramidal OME-TIFF for seg-tile serving). **Key experiment:** can OMERO serve the
  segmentation as a tileable pyramid (`RawPixelsStore`) like the channel image, so it is never
  downloaded? Quant CSV could be read on-demand into memory (ball tree is already an in-memory global).
- **FLAG 6 — Restore drops phenotype (celltype) + cluster data.** `_restore_config_entry` drops
  `celltypeData`/`celltype`/`clusterData` because re-open only re-downloads quant+seg. Extend to
  re-download those annotations when present.
- **FLAG 7 — Annotation ownership = env creds (root).** Writes use `OMERO_USER`/`OMERO_PASSWORD`
  (root/omero). For real per-user discovery/ownership, revisit under the per-user `joinSession` auth
  (§10.4); the discovery query should filter by owner/group so a user sees only their own datasources.
- **FLAG 8 — Legacy image-keyed annotations.** Annotations saved before the `(image, name)` re-keying
  have no description → don't match named reads; superseded on the next save. Any pre-re-keying test
  annotations should be re-saved to migrate cleanly.
