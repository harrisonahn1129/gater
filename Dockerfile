FROM python:3.9.15

RUN apt-get update && \
    apt-get install -y python3-opencv && \
    rm -rf /var/lib/apt/lists/*

RUN python -m pip install \
    Flask==2.2.2 \
    jinja2 \
    werkzeug==2.2.2 \
    itsdangerous==2.1.2 \
    flask-sqlalchemy==3.0.2 \
    numpy==1.26.4 \
    opencv-python \
    orjson \
    pandas \
    pillow==8.1 \
    requests \
    scikit-learn==1.2.2 \
    scikit-image \
    scipy \
    tifffile==2021.4.8 \
    waitress \
    zarr==2.10 \
    ome-types \
    matplotlib \
    appdirs \
    xmlschema

# --- OMERO Python bindings (omero-py) ---
# data_model.py streams tiles live from OMERO via BlitzGateway, which requires
# omero-py + Zeroc Ice 3.6. Ice 3.6 has no official PyPI wheel and is painful to
# build from source, so use Glencoe Software's prebuilt Ice wheel.
#
# NOTE: this wheel is linux/amd64 + CPython 3.9 specific (matches this base
# image and the NYU cluster nodes; build with --platform=linux/amd64 on Apple
# Silicon). For a different platform/Python, pick the matching wheel from:
#   https://github.com/glencoesoftware/zeroc-ice-py-linux-x86_64/releases
# and verify the tag/filename below is still current.
RUN python -m pip install \
    https://github.com/glencoesoftware/zeroc-ice-py-linux-x86_64/releases/download/20240202/zeroc_ice-3.6.5-cp39-cp39-manylinux_2_28_x86_64.whl \
    && python -m pip install omero-py

COPY . /app

CMD ["python", "/app/run.py"]