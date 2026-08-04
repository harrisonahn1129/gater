# Gater ⇄ OMERO Integration — Security & Privacy Review Briefing

**Prepared for:** Information Security, NYU Langone Health
**Prepared by:** NYU VIDA Lab / Gater-OMERO integration project
**Date:** 2026-07-30
**Code reviewed:** branch `feature/omero_gater_deployment`, commit `709c37faa` (tag `v1.36_gater`)
plus uncommitted working-tree changes, and the sibling deployment scaffolds `omero-gater/`
(OMERO.web plugin) and `gater-spawner/` (Kubernetes spawner).

---

## 0. Read this first — deployment status

**Nothing described here is currently deployed or network-exposed.** The system runs today only
on a developer laptop against a local, disposable OMERO stack (`docker-compose.yml`) containing
test data. There are no NYU users, no NYU data, and no NYU infrastructure involved yet.

This document is therefore a **pre-deployment review**: it describes what the system does today,
what the deployment design intends, and which controls are present, designed-but-unimplemented,
or absent — so that security requirements can be set **before** a pilot rather than after.

Every claim below was verified by reading the code on the date above. Where the project's own
design documents describe something that is **not yet implemented**, that is stated explicitly
(this matters: the design documents read as if the per-user identity model already exists — it
does not).

---

## 1. What the two systems are

### OMERO / OMERO.web (existing, NYU-side)
[OMERO](https://www.openmicroscopy.org/omero/) is a standard open-source bioimage data
management platform used widely in research imaging. It has three tiers:

- **OMERO.server** — the data tier: stores original images, metadata, annotations and
  attachments; enforces users, groups and a permission model; speaks a binary protocol (Ice) on
  TCP **4064**.
- **PostgreSQL + a filesystem repository** — persistence behind the server.
- **OMERO.web** — a **Django** web application providing the browser UI (the "webclient"), which
  authenticates users and holds their session.

OMERO.web is extensible through a documented plugin mechanism (`omero.web.apps` +
`right_plugins`) that loads a third-party Django app into the same process. **We never modify or
run the OMERO server or its database.** In the target deployment OMERO is NYU-owned and external
to anything we operate.

### Gater (the application we are extending)
Gater is a cell-level microscopy image viewer and **gating** tool (a fork of Harvard LSP's
Minerva Analysis). A researcher loads a multiplexed tissue image plus a per-cell quantification
table and a segmentation mask, adjusts per-channel colors and intensity ranges, and draws
thresholds/lassos to select cell populations ("gating").

Technically: a **Python 3.9 Flask** backend serving image tiles and cell queries, and a
**JavaScript/WebGL (OpenSeadragon + ViaWebGL)** frontend.

**Its original threat model is "one scientist, one laptop."** It ships as a downloadable
Windows/macOS executable and is normally run at `http://localhost:8000/`. It has:
- **no authentication and no authorization** — none, on any route;
- **no multi-tenancy** — it holds exactly one dataset in memory in module-level global variables;
- **an import interface that accepts arbitrary server-side filesystem paths**, which is
  reasonable for a desktop tool and is not reasonable for a network service.

Everything in §5 (Findings) follows from repurposing this single-user desktop application into a
shared, network-reachable, clinical-adjacent service.

### What our project adds
1. **Data-format integration (complete).** Gater previously read only one pipeline's output
   format. It can now consume OMERO's formats, and — critically — **stream image tiles live from
   OMERO instead of copying the image**, which for a 17–43 GB slide is the difference between a
   copy and no copy.
2. **A deployment design (partially built).** An "Open in Gater" button inside OMERO.web that
   launches a **private, per-user Gater pod** on Kubernetes, so the "no auth, single dataset in
   globals" limitation is contained by giving each researcher their own process.

---

## 2. Architecture and trust boundaries

### 2.1 Target design (what needs review)

```
 ┌─ NYU-owned, existing ─────────────────────────────────────────────┐
 │  Researcher's browser                                             │
 │      │ (1) authenticated OMERO.web login (NYU's existing control) │
 │      ▼                                                            │
 │  OMERO.web (Django)  +  omero-gater plugin  ← we ship this plugin │
 │  OMERO.server : 4064 (Ice)   PostgreSQL   image repository        │
 └──────┬────────────────────────────────────────────────────────────┘
        │ (2) TRUST BOUNDARY CROSSING:
        │     the plugin reads the researcher's LIVE OMERO session UUID
        │     and POSTs it as JSON to the spawner
        ▼
 ┌─ new, cluster-internal ───────────────────────────────────────────┐
 │  gater-spawner (FastAPI, holds a Kubernetes ServiceAccount)       │
 │      │ (3) creates/patches a Deployment+Service+Ingress per user; │
 │      │     the session UUID is injected as a plaintext env var ←  │
 │      │     it could use single-use short-TTL instead to deliver   │
 │      │     the credential                                         │
 │      ▼                                                            │
 │  Per-user Gater pod (Flask; no authentication of its own)         │
 └──────┬────────────────────────────────────────────────────────────┘
        │ (4) pod connects to OMERO:4064 and acts AS the researcher
        │ (5) downloads the quantification table + segmentation mask
        │     to pod disk; streams image tiles on demand
        ▼
   Researcher's browser ← raw 16-bit pixel tiles + cell-level data
        (reached over a public hostname at  https://<host>/user/<username>/ )
```

### 2.2 The three properties that generate every risk

1. **Gater has no authentication or authorization of its own.** All 55 of its HTTP routes are
   open. In the target design, isolation comes *entirely* from the per-user pod boundary plus
   whatever the ingress enforces. See SR-05, SR-06.
2. **An OMERO session UUID is a bearer credential**, and this design deliberately moves it out of
   OMERO.web, across the network, into a Kubernetes object, and into a container's environment.
   This is the central design decision to review. See SR-02, SR-03.
3. **Cell-level data is written to pod disk**, and state written back into OMERO is currently
   attributed to a **service account, not the researcher**. Retention, ownership and deletion are
   all unresolved. See SR-07, SR-12.

---

## 3. Data inventory — what crosses each boundary

| Data | Sensitivity | Where it goes | Form | Persisted? |
|---|---|---|---|---|
| Multiplexed tissue image pixels | Research imaging; PHI status **TBD** (§7 Q1) | OMERO → pod → browser | Raw 16-bit per-channel tiles, PNG-encoded, uncompressed | **No** — streamed, never written to disk; bounded LRU cache in RAM (default 256 tiles) |
| Quantification table (one row per cell: centroid coordinates + per-marker intensities + morphology) | Derived research data | OMERO attachment → **pod disk** → pod RAM → browser (subsets) | CSV; loaded whole into a pandas DataFrame + a BallTree index | **Yes**, pod disk, for the pod's lifetime |
| Segmentation mask (per-pixel cell label IDs) | Derived research data | OMERO attachment → **pod disk** (converted to pyramidal OME-TIFF) → browser | Label IDs carried losslessly in RGB bytes, PNG `compress_level=0` | **Yes**, pod disk, for the pod's lifetime |
| Render/config state (channel colors, intensity ranges, gate thresholds, lasso polygons) | Low — analysis preferences, but reveals what was analyzed | Browser → pod → **written back into OMERO** as JSON FileAnnotations, and to a **pod-local SQLite DB** | JSON on OMERO; **Python pickle** in SQLite | **Yes**, in OMERO (indefinitely) and pod SQLite |
| OMERO session UUID | **Credential** | OMERO.web → spawner (HTTP JSON) → Kubernetes Deployment spec → pod environment | Plaintext | **Yes**, in the K8s object/etcd for the pod's lifetime |
| Object identifiers (image id, attachment ids, dataset name) | Low, but identifying | OMERO.web → browser URL → Gater host | **URL query parameters** across origins | Browser history, `Referer`, proxy logs |

**Not transferred:** OMERO passwords (by design — the session-joining model exists specifically
to avoid handling them); the original image files; anything from OMERO's database directly.

---

## 4. Identity and credential model — current vs. designed

This is the most important section, and the one where our own design documents are misleading if
read alone.

### 4.1 What the code does **today**

`minerva_analysis/server/models/data_model.py:101–138` (`_ensure_omero_connection`) opens **one
process-wide connection** using **static credentials from environment variables**:

```python
host     = os.environ.get('OMERO_HOST', 'localhost')
port     = int(os.environ.get('OMERO_PORT', '4064'))
user     = os.environ.get('OMERO_USER', 'root')        # ← default
password = os.environ.get('OMERO_PASSWORD', 'omero')   # ← default
_omero_conn = BlitzGateway(user, password, host=host, port=port)
```

**Consequence if deployed as-is:** every user's pod authenticates to OMERO as a **single shared
identity — by default the OMERO administrator (`root`)** — and OMERO's per-user/per-group
permission model is bypassed entirely. Any user of any pod could read any image in OMERO.

### 4.2 What the design intends (`deployment_overview.md`, "Identity handshake")

OMERO sessions can be joined by UUID. The intended flow: the OMERO.web plugin reads the
researcher's live session UUID via `conn.getEventContext().sessionUuid`, hands it to the spawner,
and the pod calls `client.joinSession(uuid)` — connecting **as that researcher**, with no
password ever leaving OMERO.web, and with OMERO's own permission model applying to every read.

This is a sound design and is the correct target. **It is not implemented.** The plugin sends the
UUID (`omero-gater/omero_gater/views.py`, `open_in_gater`), the spawner injects it as
`OMERO_SESSION` (`gater-spawner/spawner/k8s.py`, `ensure_user_app`), the spawner README documents
it — but **the Gater application never reads that environment variable.** Verified: no occurrence
of `OMERO_SESSION` anywhere in the application source.

### 4.3 Implications for review

- Any assessment must treat "runs as OMERO root" as the **current** behavior.
- Implementing `joinSession` is the single highest-priority change and is a precondition for any
  pilot with real data.
- Even once implemented, the model gives the pod **the researcher's full OMERO privileges for the
  pod's lifetime**, not a capability scoped to the one image being viewed. Whether that is
  acceptable is a policy question for NYU (§7 Q4).

---

## 5. Findings register

Each finding states what was verified, where, and the condition under which it becomes
exploitable. "Rating" = risk **if the system were deployed as-is with real data**; nothing is
deployed today.

Legend — **Status:** `Present` (in code now) · `Design-only` (documented, not implemented) ·
`Absent` (no control exists). **Origin:** `Upstream` (inherited from the Minerva/Gater codebase)
· `Ours` (introduced by this project) · `Deployment` (in the new deployment scaffolds).

### 5.1 Identity & credentials

| ID | Finding | Rating | Status | Origin |
|---|---|---|---|---|
| **SR-01** | **Gater authenticates to OMERO with static shared credentials defaulting to `root`/`omero`.** Per-user `joinSession` is designed but not implemented. Every pod would read OMERO as an administrator, bypassing OMERO's permission model. *Evidence:* `data_model.py:101–138`; no `OMERO_SESSION` reference in the app. | **High** | Present | Ours |
| **SR-02** | **The researcher's OMERO session UUID is handled as a bearer token and stored in plaintext in a Kubernetes Deployment spec** (as a container env var), making it readable by anyone with `get`/`describe` on deployments or pods in that namespace, and persisted in etcd. *Evidence:* `gater-spawner/spawner/k8s.py` `ensure_user_app`. | **High** | Present (in scaffold) | Deployment |
| **SR-03** | **The spawner's `POST /launch` endpoint has no authentication.** It accepts an arbitrary `user` and `omero_session` from any caller that can reach it, and creates Kubernetes workloads in response. The design mitigates this by network placement only — and **no NetworkPolicy exists** in `gater-spawner/deploy/` (which contains only `rbac.yaml` and `spawner-deployment.yaml`). *Evidence:* `spawner/app.py` `launch`. | **High** | Present (in scaffold) | Deployment |
| **SR-04** | A joined OMERO session **inherits the original session's `timeToIdle`/`timeToLive`**, and the design calls `enableKeepAlive(60)`. This can extend a researcher's session lifetime beyond what OMERO's session policy intends. Requires NYU's configured timeout values to assess. | Medium | Design-only | Ours |
| **SR-16** | The **same weak dev credentials are both committed to the local dev compose files and hard-coded as application fallback defaults** (`root`/`omero`). A missing or misspelled env var in production therefore fails *open* to administrator credentials rather than failing closed. *Evidence:* `data_model.py:130–131`; `docker-compose.yml`, `docker-compose.gater.yml`. | **High** | Present | Upstream/Ours |

### 5.2 Access control & multi-tenancy

| ID | Finding | Rating | Status | Origin |
|---|---|---|---|---|
| **SR-05** | **Gater implements no authentication or authorization on any route.** 55 Flask routes, zero access control; no session handling, no decorators, no tokens. Isolation depends entirely on network placement. *Evidence:* full route inventory across `page_routes.py`, `data_routes.py`, `import_routes.py`; no `login_required`/session usage anywhere. | **High** | Absent | Upstream |
| **SR-06** | **No per-pod authorization guard.** The design routes users by URL path (`https://<host>/user/<username>/`) with **no path rewrite and no authentication**. Anyone who can reach the hostname and guess a username reaches that user's pod — which is already connected to OMERO as them and has their dataset loaded in memory. The project's own plan lists this as required work; it is not built. *Evidence:* `spawner/k8s.py` `_apply_ingress`. | **High** | Absent | Deployment |
| **SR-07** | **State written back into OMERO is attributed to the service account, not the researcher.** Gater writes JSON FileAnnotations onto OMERO Images under `gater.vida.nyu/*` namespaces using the env credentials (today `root`), and discovery queries are not scoped by owner or group. This corrupts provenance and, once the "list saved configs" feature is used, can expose one user's analysis names to another. *Evidence:* `data_model.py:370` (`put_omero_json`), `436` (`get_omero_json`); tracked internally as FLAG 7. | Medium | Present | Ours |

### 5.3 Application attack surface (pre-existing, amplified by network exposure)

These are inherited from the upstream single-user desktop application. They are low-consequence
on a laptop and materially different once the app is reachable over a network.

| ID | Finding | Rating | Status | Origin |
|---|---|---|---|---|
| **SR-08** | **Unauthenticated destructive endpoint.** `GET /delete/<config_name>` builds a path by joining a user-controlled URL segment to the data directory and calls `shutil.rmtree()` on it, with **no authentication and no validation that the name is a known datasource**. Because it is a `GET`, it is reachable by CSRF, link prefetch, or a crawler. The Flask `<string:>` converter permits `..`, so the deletion is not confined to the data directory. *Evidence:* `import_routes.py:85–99`. | **High** | Present | Upstream |
| **SR-09** | **Unauthenticated filesystem enumeration.** `POST /check_path_existence` and `POST /check_file_existence` accept an **arbitrary absolute path** in the request body and return whether it exists; `POST /get_mc_csv_file_list`, `POST /get_mc_segmentation_file_list` and `POST /check_mc_output_folder` list or test directory contents at an arbitrary path. Together these are a filesystem existence oracle and directory-enumeration primitive against the pod. *Evidence:* `import_routes.py:619`, `652`, `699`, `726`, `739`. | Medium | Present | Upstream |
| **SR-10** | **The local-file import surface remains enabled in the deployed configuration.** `GET/POST /upload` and the MCMICRO import UI exist to ingest data from server-side paths — functionality with no purpose in the OMERO workflow, which reaches the app through `/open_from_omero`. *Evidence:* `import_routes.py:212`. Recommended control: disable the local-import routes when running in OMERO mode. | Medium | Present | Upstream |
| **SR-11** | **Python `pickle` deserialization of stored state.** Render state is stored as pickled objects in the pod-local SQLite DB and read back with `pickle.loads`; the spatial index is loaded from a `ball_tree.pickle` file in the datasource directory. Pickle deserialization is arbitrary code execution if the store is ever attacker-influenced. Currently bounded because both are pod-local and short-lived — this bound **disappears** if a shared or persistent volume is introduced. *Evidence:* `data_model.py:534, 756, 1202, 1276`. | Medium (High if a PVC is added) | Present | Upstream |

### 5.4 Data handling & privacy

| ID | Finding | Rating | Status | Origin |
|---|---|---|---|---|
| **SR-12** | **Cell-level quantification data and the segmentation mask are written to pod disk** and retained for the pod's lifetime; the intended "no local persistence" property is **not met**. There is currently **no PersistentVolumeClaim**, so data is destroyed with the pod — accidentally safer, but it means no retention policy has actually been designed, and adding a PVC (which is planned, for usability) would create one without one. *Evidence:* `import_routes.py` `open_from_omero_run`; tracked internally as FLAG 5. | Medium | Present | Ours |
| **SR-13** | **No audit logging.** There is no record of who opened which image, when, or what was exported. The project's own plan raises this as a requirement to confirm; nothing implements it. Note the application also offers CSV export of gated cell populations (`/download_gating_csv`) with no logging. | **High** (if PHI) | Absent | Both |
| **SR-14** | **OMERO object identifiers travel cross-origin as URL query parameters.** The OMERO.web page opens a new tab on a different host passing `name`, `image_id`, `quant` and `seg` in the query string, exposing them to browser history, `Referer` headers and any intermediate proxy logs. Dataset names are user-chosen and may themselves be descriptive. *Evidence:* `omero-gater/omero_gater/views.py` `open_in_gater`. | Low–Medium | Present | Deployment |

### 5.5 Infrastructure, availability & supply chain

| ID | Finding | Rating | Status | Origin |
|---|---|---|---|---|
| **SR-15** | **Supply chain is not pinned or scanned.** The Gater image builds `FROM python:3.9.15` (Python 3.9 is end-of-life), installs the Zeroc Ice binding from an **unpinned GitHub release URL** (no hash verification), and mixes pinned and unpinned PyPI dependencies. The plugin image derives from `openmicroscopy/omero-web-standalone:5` (floating tag). No SBOM, no image scanning in the pipeline. *Evidence:* `gater/Dockerfile`; `omero-gater/Dockerfile`. | Medium | Present | Both |
| **SR-17** | **Spawner RBAC is correctly scoped** — a namespaced Role granting CRUD on deployments/services/ingresses and read-only on pods, with no cluster-wide permissions. *However*, because `/launch` is unauthenticated (SR-03), this workload-creation capability is effectively delegated to any caller that can reach the service. *Evidence:* `gater-spawner/deploy/rbac.yaml`. | (see SR-03) | Present | Deployment |
| **SR-18** | **Resource-exhaustion and availability issues.** `/launch` being unauthenticated means an unauthenticated caller can create pods for arbitrary usernames (each requesting up to 2 CPU / 4 GiB). Separately, every `/launch` patches the Deployment's environment, forcing a **full pod rollout**, while the plugin redirects immediately with no readiness wait — so the first click reliably lands on a not-ready pod. *Evidence:* `spawner/k8s.py` `ensure_user_app`; `views.py` `open_in_gater`. | Medium | Present | Deployment |
| **SR-19** | **Idle culling is time-since-launch (TTL), not true idle.** An actively working researcher is culled after the timeout (default 1 h) unless they re-click; conversely there is no reaping based on actual inactivity. Documented honestly in the spawner README. *Evidence:* `spawner/culler.py`. | Low | Present | Deployment |
| **SR-20** | The progress bar uses **Server-Sent Events**, which requires proxy response buffering to be disabled for `text/event-stream` on the ingress — an infrastructure configuration that must be reviewed and approved. | Low | Present | Ours |

### 5.6 Positive findings (controls and properties that reduce risk)

Stated for balance; each was verified.

- **Image pixel data is never copied out of OMERO.** In OMERO mode the slide is streamed
  tile-by-tile and the configuration explicitly carries no local channel file. For a 17–43 GB
  slide this eliminates a large data-copy exposure that the alternative design would create.
- **Tile reads are memory-bounded.** A prior defect allowed an unbounded read that could
  allocate gigabytes and cause an out-of-memory kill; it was fixed by reading in aligned
  sub-blocks with a configurable cap (`GATER_OMERO_MAX_RAW_TILE`, default 2048), producing
  bit-identical output with bounded peak memory. *Evidence:* `data_model.py:54, 187–277`.
- **The password-free identity design is the right target.** Joining an existing session avoids
  ever transferring OMERO credentials, and delegates authorization to OMERO itself (once SR-01
  is fixed).
- **Spawner RBAC is namespace-scoped and least-privilege** in its permission set (SR-17).
- **Annotation writes are create-before-delete**, so a failed cleanup cannot destroy a
  researcher's saved state.
- **No production secrets are committed.** The only credentials in the repository are the
  throwaway defaults of the local development stack. (They are still a problem as *fallback
  defaults in code* — see SR-16.)
- **The system is not deployed**, so all of the above are pre-deployment findings rather than
  live exposures.

---

## 6. Control status summary

| Control | Status |
|---|---|
| User authentication to reach the application | **Absent** — relies entirely on network placement (SR-05, SR-06) |
| Per-user authorization / tenant isolation | **Absent** — per-pod boundary only, with a guessable URL (SR-06) |
| Authentication of the OMERO.web → spawner call | **Absent** (SR-03) |
| Acting under the researcher's own OMERO identity | **Designed, not implemented** — currently a shared admin account (SR-01) |
| Credential storage protections (Secret, TTL, rotation) | **Absent** — plaintext env var (SR-02) |
| Network segmentation (NetworkPolicy) | **Absent** — recommended in prose, no manifest (SR-03) |
| TLS / ingress hardening | **Not yet designed** — assumed from NYU infrastructure |
| Audit logging | **Absent** (SR-13) |
| Data retention / deletion policy | **Absent** — undefined (SR-12) |
| Encryption at rest for pod working data | **Not addressed** — depends on NYU StorageClass |
| Kubernetes RBAC least privilege | **Present** and correctly scoped (SR-17) |
| Resource limits per user pod | **Present** — 2 CPU / 4 GiB limits configured |
| Image scanning / SBOM / dependency pinning | **Absent** (SR-15) |
| Memory-safety bound on the tile path | **Present** (positive finding) |

---

## 7. Questions we need answered by NYU

These are decisions we cannot make. Several may change the architecture materially, so we would
like them resolved before building further.

**Data classification (highest priority)**
1. Is the imaging and derived cell-level data in NYU's OMERO **PHI / patient-identifiable**? Is
   it de-identified, and to what standard? Does this work fall under **HIPAA** and/or an **IRB
   protocol**?
2. If PHI: what are the resulting requirements for encryption at rest, audit logging, access
   review, data residency (on-prem-only?), and node/namespace placement?
3. What are the **retention and deletion** requirements for working copies of the quantification
   table and segmentation mask on the pod, and for analysis state written back into OMERO?

**Identity and authorization**
4. Is it acceptable for a Gater pod to hold **the researcher's full OMERO privileges** for the
   pod's lifetime (the `joinSession` model), or do you require a capability scoped to a single
   image?
5. What are OMERO's configured session `timeToIdle` / `timeToLive` values, and is extending a
   session via keep-alive acceptable? (SR-04)
6. Does NYU require **institutional SSO** in front of the Gater hostname, or is "the button only
   exists inside an authenticated OMERO session" sufficient as the authentication boundary?
7. Do NYU usernames map 1:1 to OMERO usernames?

**Infrastructure**
8. Can the `omero-gater` plugin be installed into NYU's **existing** OMERO.web (a `pip install`
   plus a service restart), or must a parallel OMERO.web instance be stood up? *(This is our top
   blocker; it determines the entire deployment shape.)*
9. What Kubernetes namespace, RBAC, ingress controller, hostname, TLS certificate and
   StorageClass are available to us, and is the StorageClass encrypted at rest?
10. Which container registry may the cluster pull from, and what image scanning / SBOM
    requirements apply?
11. Is network access permitted from the cluster to OMERO on 4064, and what segmentation
    (NetworkPolicy) do you require around the spawner?

**Governance**
12. What audit events must be recorded (image opened, data exported, pod created), where must
    they be shipped, and for how long retained?
13. Who signs off on data use, and what is the review path for a pilot?

---

## 8. Controls we propose to implement before any pilot

Listed in the order we would build them. We would welcome your view on completeness and
priority — and on whether any of these should be preconditions for a pilot with *test* data as
well as real data.

**Must-fix before any deployment with real data**
1. **Implement per-user `joinSession`** and remove the credential fallback so the application
   **fails closed** if no session is supplied — never falling back to `root`. (SR-01, SR-16)
2. **Add a per-pod authorization guard** — a one-time token issued by the spawner, bound to the
   user, required on every request to the pod; reject anything else. (SR-06)
3. **Authenticate `/launch`** with a shared secret or mTLS, and add a **NetworkPolicy**
   restricting it to OMERO.web only. (SR-03)
4. **Stop passing the session UUID as a plaintext env var** — use a short-lived Kubernetes Secret
   (or, preferably, exchange it for a scoped, expiring token at launch). (SR-02)
5. **Disable the local-import and filesystem-inspection routes in OMERO mode**, and remove or
   guard the unauthenticated `/delete/` endpoint. (SR-08, SR-09, SR-10)

**Before a production rollout**
6. Structured **audit logging** of launch, image-open and data-export events, shipped to NYU's
   logging platform. (SR-13)
7. A defined **retention/deletion policy** for pod working data, enforced by the culler, and an
   explicit decision on whether a PVC is introduced at all. (SR-12)
8. **Supply-chain hardening**: pin the Ice wheel by hash, pin base images by digest, move off
   end-of-life Python 3.9, add image scanning and an SBOM to the build. (SR-15)
9. Replace pickle-based state storage with JSON if any shared or persistent storage is
   introduced. (SR-11)
10. Readiness handshake on launch, and a true idle-culling signal. (SR-18, SR-19)

---

## 9. Verification method

All findings were established by direct source review on 2026-07-30 of:

- **Application:** `gater/minerva_analysis/` — `__init__.py`, `server/models/data_model.py`,
  `server/routes/{page,data,import}_routes.py`, `server/models/database_model.py`,
  `server/utils/*`, and the client `main.js` / `channelList.js` / `imageViewer.js`.
- **OMERO.web plugin:** `omero-gater/` — `setup.py`, `omero_gater/{settings,urls,views}.py`,
  `templates/gater/*`, `Dockerfile`.
- **Spawner:** `gater-spawner/` — `spawner/{app,k8s,culler,config}.py`,
  `deploy/{rbac,spawner-deployment}.yaml`, `Dockerfile`, `README.md`.
- **Build/runtime:** `gater/Dockerfile`, `gater/run.py`, `docker-compose.yml`,
  `docker-compose.gater.yml`.

Where a claim rests on the *absence* of something, it was verified by exhaustive search (for
example: no occurrence of `OMERO_SESSION` in the application source; no authentication decorator
or session usage across the full route inventory; no `NetworkPolicy` manifest in the deployment
directory).

**Companion documents:** `deployment_overview.md` (full architecture, data flows and deployment
design); `README.md` (what the application is and how to run it).
