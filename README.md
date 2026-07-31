# Gater | Minerva Analysis

![](./minerva_analysis/client/src/img/logo_with_text.svg)

## About

An [OpenSeadragon](https://openseadragon.github.io/)-based **cellular image viewing and analysis
tool**, built with a Python [Flask](http://flask.pocoo.org/) backend and a
[Node.js](https://nodejs.org/en/) JavaScript frontend.

This fork adds **OMERO integration**: Gater can consume data directly from an
[OMERO](https://www.openmicroscopy.org/omero/) server and **stream image tiles live from OMERO**
instead of copying the image. It runs in two modes:

- **Local mode** (unchanged) — a channel OME-TIFF, a segmentation mask and a quantification CSV on
  local disk, in mcmicro layout.
- **OMERO mode** — the image stays in OMERO and is streamed tile-by-tile; the quantification table
  and segmentation mask are downloaded from the image's OMERO attachments; channel colours,
  intensity ranges and gates are saved back onto the OMERO image so a session can be restored on
  any host.

Beyond the mcmicro format, the OMERO-side importers handle **QPTIFF** images (Akoya/Vectra,
including files well over 4 GB), **OME-NGFF zarr** segmentation masks, and **OMERO quantification
CSVs** (8 statistics per channel), converting each into the layout the viewer expects.

> **Full technical detail** — architecture, workflows, deployment design, open issues:
> [`deployment_overview.md`](./deployment_overview.md).
> **Security & privacy briefing:** [`security_review.md`](./security_review.md).

## Executables (for Users)

Releases are available at https://github.com/labsyspharm/gater/releases — Windows and macOS
executables that run locally with no installation.

> These upstream builds are **local mode only**. The OMERO features described above require the
> Docker image or a source checkout of this fork, since they depend on the OMERO Python bindings.

## Running as a Docker container

The image includes the OMERO Python bindings (`omero-py` and Zeroc Ice 3.6), which are
**linux/amd64-specific** — on an ARM machine (e.g. an Apple Silicon Mac) build with
`--platform linux/amd64`.

```bash
# Build
docker build --platform linux/amd64 -t gater .

# Run — local mode, with your data mounted
docker run --rm -dp 8000:8000 -v [source path]:/[target path] gater
```

- `--rm` cleans up the container when it exits
- `-v` mounts a host directory containing your data so it can be reached from the import page
  (type the mounted path, e.g. `/data/...`, into the import form)
- `-dp` publishes port 8000

Then open `http://localhost:8000/`.

### OMERO mode

Point the container at an OMERO server with environment variables:

```bash
docker run --rm -p 8000:8000 \
  -e OMERO_HOST=omero.example.edu \
  -e OMERO_PORT=4064 \
  -e OMERO_USER=<username> \
  -e OMERO_PASSWORD=<password> \
  gater
```

| Variable | Default | Purpose |
|---|---|---|
| `OMERO_HOST` | `localhost` | OMERO server hostname |
| `OMERO_PORT` | `4064` | OMERO server port |
| `OMERO_USER` | `root` | OMERO username |
| `OMERO_PASSWORD` | `omero` | OMERO password |
| `GATER_BASE_PATH` | *(empty)* | Serve under a URL prefix, e.g. `/user/jsmith`, when behind a path-based proxy |
| `GATER_OMERO_MAX_RAW_TILE` | `2048` | Cap on the raw region read per OMERO sub-block (bounds per-tile memory) |
| `GATER_TILE_CACHE_SIZE` | `256` | Tile LRU capacity (`0` disables) |

> ⚠️ `OMERO_USER`/`OMERO_PASSWORD` are a **single shared identity for the whole process**. This is
> adequate for single-user and development use only. Per-user identity (joining the researcher's
> own OMERO session) is designed but not yet implemented — see
> [`deployment_overview.md` §9](./deployment_overview.md#9-implementation-status).

A datasource is opened from OMERO through `/open_from_omero?name=&image_id=&quant=&seg=`, which
downloads the quantification CSV and segmentation attachments, then shows the channel-matching
page before opening the viewer. In the intended deployment this URL is reached by clicking
**"Open in Gater"** inside OMERO.web, provided by the companion `omero-gater` plugin.

A local OMERO + Gater `docker-compose` stack is available in the deployment workspace alongside
this repository (it is not part of this repo) for end-to-end development against a disposable
OMERO server.

## Clone and Run Codebase (for Developers)

#### 1. Check out the project

```bash
git clone https://github.com/harrisonahn1129/gater.git
cd gater
git checkout feature/omero_gater_deployment
git pull
```

*(The upstream project is https://github.com/labsyspharm/gater — its `gating` branch is the
baseline this fork builds on.)*

#### 2. Conda install

* Install [miniconda](https://conda.io/miniconda.html) or
  [conda](https://docs.conda.io/projects/conda/en/latest/user-guide/install/download.html).
* Create the environment: `conda env create -f requirements.yml`
* Activate it: `conda activate minerva_analysis`

#### 3. (OMERO features only) install the OMERO bindings

`requirements.yml` deliberately omits them, so local mode works without an Ice build. The OMERO
imports are lazy, so the app runs fine without these until you use an OMERO datasource.

```bash
# linux/amd64 + CPython 3.9 — pick the matching wheel for other platforms from
# https://github.com/glencoesoftware/zeroc-ice-py-linux-x86_64/releases
pip install https://github.com/glencoesoftware/zeroc-ice-py-linux-x86_64/releases/download/20240202/zeroc_ice-3.6.5-cp39-cp39-manylinux_2_28_x86_64.whl
pip install omero-py
```

#### 4. Start the server

```bash
python run.py          # optionally: python run.py <port>
```

Access the tool at `http://localhost:8000/`.

#### 5. (Optional) Node.js packages

Only needed if you plan to edit the JavaScript — bundled JS files are already included.

* Install [Node.js](https://nodejs.org/en/), then from `/minerva_analysis/client` run
  `npm install`.
* Run `npm run start` to bundle, or `npm run watch` while editing.

> Changes to `channelList.js`, `main.js`, `channelMatch.js` or `imageViewer.js` require a client
> rebuild before they take effect.

## Packaging/Bundling Code as Executable (for Developers)

Any tagged commit to a branch triggers a build where `tag == commit message`; the result appears
under releases. Builds take roughly 10 minutes.

**Tagging convention:** release tags look like `v{version_number}_{branch_name}` — for this fork,
`v1.36_gater`. There is no version file: **the tag is the release**.
