from minerva_analysis import app
from flask import make_response, render_template, request, Response, jsonify, abort, send_file
import io
from PIL import Image
from minerva_analysis import data_path, get_config
from minerva_analysis.server.models import data_model
from pathlib import Path
from time import time
import pandas as pd
import gzip
import json
import orjson
import os
import threading
from os import walk
from flask_sqlalchemy import SQLAlchemy


@app.route('/init_database', methods=['GET'])
def init_database():
    datasource = request.args.get('datasource')
    data_model.init(datasource)
    resp = jsonify(success=True)
    return resp


@app.route('/config')
def serve_config():
    return get_config()


@app.route('/get_nearest_cell', methods=['GET'])
def get_nearest_cell():
    x = float(request.args.get('point_x'))
    y = float(request.args.get('point_y'))
    datasource = request.args.get('datasource')
    resp = data_model.query_for_closest_cell(x, y, datasource)
    return serialize_and_submit_json(resp)


# Gets a row based on the index
@app.route('/get_database_row', methods=['GET'])
def get_database_row():
    datasource = request.args.get('datasource')
    row = int(request.args.get('row'))
    resp = data_model.get_row(row, datasource)
    return serialize_and_submit_json(resp)


@app.route('/get_channel_names', methods=['GET'])
def get_channel_names():
    datasource = request.args.get('datasource')
    shortnames = bool(request.args.get('shortNames'))
    resp = data_model.get_channel_names(datasource, shortnames)
    return serialize_and_submit_json(resp)


@app.route('/get_phenotypes', methods=['GET'])
def get_phenotypes():
    datasource = request.args.get('datasource')
    resp = data_model.get_phenotypes(datasource)
    return serialize_and_submit_json(resp)


@app.route('/get_color_scheme', methods=['GET'])
def get_color_scheme():
    datasource = request.args.get('datasource')
    refresh = request.args.get('refresh') == 'true'
    resp = data_model.get_color_scheme(datasource, refresh)
    return serialize_and_submit_json(resp)


@app.route('/get_neighborhood', methods=['GET'])
def get_neighborhood():
    x = float(request.args.get('point_x'))
    y = float(request.args.get('point_y'))
    max_distance = float(request.args.get('max_distance'))
    datasource = request.args.get('datasource')
    resp = data_model.get_neighborhood(x, y, datasource, r=max_distance)
    return serialize_and_submit_json(resp)

@app.route('/get_naive_states', methods=['GET'])
def get_naive_states():
    # test of triggering docker module
    dirname = os.path.dirname(__file__)
    os.system('docker run --rm -v' + dirname + '/data:/data labsyspharm/naivestates:1.7.0 /app/main.R -i /data/unmicst-163.csv')
    os.path.join(os.getcwd() / data_path / "data"  / "umicst-162-models.csv")


@app.route('/get_num_cells_in_circle', methods=['GET'])
def get_num_cells_in_circle():
    datasource = request.args.get('datasource')
    x = float(request.args.get('point_x'))
    y = float(request.args.get('point_y'))
    r = float(request.args.get('radius'))
    resp = data_model.get_number_of_cells_in_circle(x, y, datasource, r=r)
    return serialize_and_submit_json(resp)


@app.route('/get_all_cells/<dtype>/', methods=['GET'])
def get_all_cells(dtype):
    datasource = request.args.get('datasource')
    data_type = int if 'integer' == dtype else float
    start_keys = list(request.args.get('start_keys').split(','))
    resp = data_model.get_all_cells(datasource, start_keys, data_type)
    content = gzip.compress(resp.tobytes('C'))
    response = make_response(content)
    response.headers.set('Content-Type', 'application/octet-stream')
    response.headers['Content-length'] = len(content)
    response.headers['Content-Encoding'] = 'gzip'
    return response


@app.route('/get_gated_cell_ids', methods=['GET'])
def get_gated_cell_ids():
    datasource = request.args.get('datasource')
    filter = json.loads(request.args.get('filter'))
    start_keys = list(request.args.get('start_keys').split(','))
    resp = data_model.get_gated_cells(datasource, filter, start_keys)
    return serialize_and_submit_json(resp)


@app.route('/get_gated_cell_ids_custom', methods=['GET'])
def get_gated_cell_ids_custom():
    datasource = request.args.get('datasource')
    filter = json.loads(request.args.get('filter'))
    start_keys = list(request.args.get('start_keys').split(','))
    resp = data_model.get_gated_cells_custom(datasource, filter, start_keys)
    return serialize_and_submit_json(resp)

@app.route('/get_channel_cell_ids', methods=['GET'])
def get_channel_cell_ids():
    datasource = request.args.get('datasource')
    filter = json.loads(request.args.get('filter'))
    resp = data_model.get_channel_cells(datasource, filter)
    return serialize_and_submit_json(resp)


@app.route('/get_database_description', methods=['GET'])
def get_database_description():
    datasource = request.args.get('datasource')
    resp = data_model.get_datasource_description(datasource)
    return serialize_and_submit_json(resp)


@app.route('/get_channel_gmm', methods=['GET'])
def get_channel_gmm():
    channel = request.args.get('channel')
    datasource = request.args.get('datasource')
    resp = data_model.get_channel_gmm(channel, datasource)
    return serialize_and_submit_json(resp)

@app.route('/get_gating_gmm', methods=['POST'])
def get_gating_gmm():
    post_data = json.loads(request.data)
    channel = post_data['channel']
    datasource = post_data['datasource']
    selection_ids = post_data['selection_ids']
    resp = data_model.get_gating_gmm(channel, datasource, selection_ids)
    return serialize_and_submit_json(resp)

@app.route('/upload_gates', methods=['POST'])
def upload_gates():
    file = request.files['file']
    if file.filename.endswith('.csv') == False:
        abort(422)
    datasource = request.form['datasource']
    save_path = data_path / datasource
    if save_path.is_dir() == False:
        abort(422)

    filename = 'uploaded_gates.csv'
    file.save(Path(save_path / filename))
    resp = jsonify(success=True)
    return resp

# NOTE: the old '/upload_channels' route (local file -> uploaded_channels.csv on
# the server's disk) was replaced by the OMERO CSV routes below. Channel CSVs
# now live as attachments on the OMERO image, which also removes a per-datasource
# disk write from the pod (see FLAG 5, deployment_overview.md).

@app.route('/get_rect_cells', methods=['GET'])
def get_rect_cells():
    # Parse (rect - [x, y, r], channels [string])
    datasource = request.args.get('datasource')
    rect = [float(x) for x in request.args.get('rect').split(',')]
    channels = request.args.get('channels')

    # Retrieve cells - FIXME: Too slow - jam is stalling image loading
    resp = data_model.get_rect_cells(datasource, rect, channels)
    print('Neighborhood size:', len(resp))
    return serialize_and_submit_json(resp)


@app.route('/get_ome_metadata', methods=['GET'])
def get_ome_metadata():
    datasource = request.args.get('datasource')
    resp = data_model.get_ome_metadata(datasource)
    if resp:
        resp = resp.json()
    # OME-Types handles jsonify itself, so skip the orjson conversion
    response = app.response_class(
        response=resp,
        mimetype='application/json'
    )
    return response


# NOTE: '/download_gating_csv' (which streamed the gating CSVs back as browser
# downloads) was replaced by '/save_gating_csv_to_omero' below -- the gating
# panel now writes both CSVs to the OMERO image instead.

@app.route('/save_gating_list', methods=['POST'])
def save_gating_list():
    post_data = json.loads(request.data)

    datasource = post_data['datasource']
    filter = post_data['filter']
    channels = post_data['channels']
    lassos = post_data['lassos']

    data_model.save_gating_list(datasource, filter, channels, lassos)

    resp = jsonify(success=True)
    return resp

@app.route('/get_saved_gating_list', methods=['GET'])
def get_saved_gating_list():
    datasource = request.args.get('datasource')
    resp = data_model.get_saved_gating_list(datasource)
    return serialize_and_submit_json(resp)

# --- CSV saves to OMERO ---------------------------------------------------
# Writing a CSV can be slow: the per-cell gating export covers the whole
# quantification table, which is hundreds of MB on a real slide and takes ~a
# minute. Waitress serves on a small thread pool, so several of those at once
# starve every other request (tiles included) and the app looks frozen -- and
# each one holds another full copy of the table, which is how it ends up
# OOM-killed. So: ONE CSV save at a time, process-wide. A second request is
# refused immediately rather than queued, which keeps its thread free.
#
# Holding the lock across the existence check AND the write also closes the
# race that let several concurrent saves each see "name is free" and then all
# write the same name.
_csv_save_lock = threading.Lock()
_csv_save_state = {'active': False, 'stage': '', 'name': '', 'started': 0.0}


def _csv_save_stage(stage):
    _csv_save_state['stage'] = stage


@app.route('/csv_save_status', methods=['GET'])
def csv_save_status():
    """Cheap poll target for the client's progress overlay."""
    state = dict(_csv_save_state)
    state['elapsed'] = round(time() - state['started'], 1) if state['active'] else 0
    return jsonify(state)


def _csv_save_busy_response():
    return jsonify(
        error=("Another CSV save is already in progress%s. Please wait for it "
               "to finish before starting another."
               % (' ("%s")' % _csv_save_state['name']
                  if _csv_save_state['name'] else '')),
        busy=True), 409


# --- Channel CSV on OMERO -------------------------------------------------
# These three routes replace the old local-file pair (/upload_channels +
# /download_channels_csv). Errors come back as JSON {error: "..."} so the UI can
# show the reason instead of a generic failure.

def _omero_image_id_or_error(datasource):
    """(image_id, None) or (None, json_error_response). The channel CSV lives on
    the OMERO image, so a purely local datasource has nowhere to read/write it."""
    image_id = data_model._omero_image_id_for(datasource)
    if image_id is None:
        return None, (jsonify(error=(
            "This datasource is not backed by an OMERO image, so there is no "
            "image to read channel CSVs from or save them to.")), 422)
    return image_id, None


@app.route('/save_channels_csv_to_omero', methods=['POST'])
def save_channels_csv_to_omero():
    """Save the current channel settings as a named CSV attachment on the image.

    Same content as /save_channel_list, but written as text/csv so it can be
    downloaded from OMERO.web or opened in a spreadsheet. The user names the
    file, so one image can hold several. Saving over an existing name requires
    an explicit overwrite=true: without it this returns 409 {exists: true} so
    the UI can ask. Re-checking here (not just in the browser) means a name
    created since the UI listed them still cannot be clobbered silently.
    """
    post_data = json.loads(request.data)
    datasource = post_data['datasource']
    image_id, err = _omero_image_id_or_error(datasource)
    if err:
        return err

    name = data_model.canonical_csv_name(post_data.get('csv_name'), datasource)
    if not _csv_save_lock.acquire(blocking=False):
        return _csv_save_busy_response()
    try:
        _csv_save_state.update(active=True, stage='Preparing channel list',
                               name=name, started=time())
        if not post_data.get('overwrite') and \
                data_model.omero_csv_name_exists(image_id, 'channel_csv', name):
            return jsonify(
                error='A channel CSV named "%s" already exists on this image.' % name,
                exists=True, name=name), 409

        csv = data_model.download_channels(
            datasource, post_data['map_channels'], post_data['active_channels'],
            post_data['list_colors'], post_data['list_ranges'],
            post_data['list_channels'])
        _csv_save_stage('Uploading to OMERO')
        ok = data_model.put_omero_csv(image_id, 'channel_csv', name, csv)
    finally:
        _csv_save_state.update(active=False, stage='', name='')
        _csv_save_lock.release()
    if not ok:
        return jsonify(error="Could not write the CSV to OMERO. The channel "
                             "settings were not saved."), 502
    return jsonify(success=True, rows=int(len(csv)), name=name)


@app.route('/list_omero_channel_csvs', methods=['GET'])
def list_omero_channel_csvs():
    """CSV attachments on this datasource's OMERO image, for the load picker."""
    datasource = request.args.get('datasource')
    image_id, err = _omero_image_id_or_error(datasource)
    if err:
        return err
    return serialize_and_submit_json(
        {'csvs': data_model.list_omero_csv_files(image_id, 'channel_csv')})


@app.route('/get_omero_channel_csv_values', methods=['GET'])
def get_omero_channel_csv_values():
    """Parse a chosen CSV attachment into channel records for the client."""
    datasource = request.args.get('datasource')
    ann_id = request.args.get('ann_id')
    image_id, err = _omero_image_id_or_error(datasource)
    if err:
        return err
    if not ann_id:
        return jsonify(error="No CSV attachment was selected."), 422
    try:
        records = data_model.get_omero_channel_csv_records(image_id, ann_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 422
    return serialize_and_submit_json(records)


# --- Gating CSV on OMERO --------------------------------------------------
# Mirrors the channel CSV routes. Two kinds are written from the gating
# download panel: the gate ranges (loadable back) and the per-cell encodings
# (an export only), each in its own namespace so one never replaces the other.

@app.route('/save_gating_csv_to_omero', methods=['POST'])
def save_gating_csv_to_omero():
    """Save the gating panel's CSV as a named attachment on the OMERO image.

    fullCsv=false -> gate ranges (channel/gate_start/gate_end/gate_active plus
    Lasso rows); fullCsv=true -> the per-cell encoding export, honouring the
    panel's binary/intensity choice. Overwrite semantics match the channel CSV:
    409 {exists: true} unless overwrite=true.
    """
    post_data = json.loads(request.data)
    datasource = post_data['datasource']
    image_id, err = _omero_image_id_or_error(datasource)
    if err:
        return err

    full_csv = bool(post_data.get('fullCsv'))
    kind = 'gating_cells_csv' if full_csv else 'gating_csv'
    default_stem = '%s_gated_%s' % (
        datasource, 'cell_encodings' if full_csv else 'channel_ranges')
    name = data_model.canonical_csv_name(post_data.get('csv_name'), default_stem)
    if not _csv_save_lock.acquire(blocking=False):
        return _csv_save_busy_response()
    try:
        _csv_save_state.update(
            active=True, name=name, started=time(),
            stage='Building per-cell export' if full_csv else 'Collecting gates')
        if not post_data.get('overwrite') and \
                data_model.omero_csv_name_exists(image_id, kind, name):
            return jsonify(
                error='A CSV named "%s" already exists on this image.' % name,
                exists=True, name=name), 409

        if full_csv:
            csv = data_model.download_gating_csv(
                datasource, post_data['filter'], post_data['channels'],
                post_data.get('selection_ids'),
                post_data.get('encoding') or 'binary')
        else:
            csv = data_model.download_gates(
                datasource, post_data['filter'], post_data['channels'],
                post_data.get('lassos') or {})

        _csv_save_stage('Uploading to OMERO')
        # Pass the frame, not its CSV text: put_omero_csv streams it to disk so
        # a multi-hundred-MB export is never held as str and bytes at once.
        ok = data_model.put_omero_csv(image_id, kind, name, csv)
    finally:
        _csv_save_state.update(active=False, stage='', name='')
        _csv_save_lock.release()
    if not ok:
        return jsonify(error="Could not write the CSV to OMERO. The gating "
                             "was not saved."), 502
    return jsonify(success=True, rows=int(len(csv)), name=name)


@app.route('/list_omero_gating_csvs', methods=['GET'])
def list_omero_gating_csvs():
    """CSV attachments on this image, flagged against the gate-ranges kind.

    `kind` selects which files the overwrite warning applies to; the caller
    passes 'save' when naming a file to write and omits it when picking one to
    load."""
    datasource = request.args.get('datasource')
    kind = request.args.get('kind') or 'gating_csv'
    if kind not in ('gating_csv', 'gating_cells_csv'):
        return jsonify(error="Unknown CSV kind."), 422
    image_id, err = _omero_image_id_or_error(datasource)
    if err:
        return err
    return serialize_and_submit_json(
        {'csvs': data_model.list_omero_csv_files(image_id, kind)})


@app.route('/get_omero_gating_csv_values', methods=['GET'])
def get_omero_gating_csv_values():
    """Parse a chosen CSV attachment into gating records for the client."""
    datasource = request.args.get('datasource')
    ann_id = request.args.get('ann_id')
    image_id, err = _omero_image_id_or_error(datasource)
    if err:
        return err
    if not ann_id:
        return jsonify(error="No CSV attachment was selected."), 422
    try:
        records = data_model.get_omero_gating_csv_records(image_id, ann_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 422
    return serialize_and_submit_json(records)

@app.route('/save_channel_list', methods=['POST'])
def save_channel_list():
    post_data = json.loads(request.data)

    datasource = post_data['datasource']
    map_channels = post_data['map_channels']
    active_channels = post_data['active_channels']
    list_colors = post_data['list_colors']
    list_ranges = post_data['list_ranges']
    list_channels = post_data['list_channels']

    data_model.save_channel_list(datasource, map_channels, active_channels, list_colors, list_ranges, list_channels)

    resp = jsonify(success=True)
    return resp

@app.route('/get_uploaded_gating_csv_values', methods=['GET'])
def get_gating_csv_values():
    datasource = request.args.get('datasource')
    file_path = data_path / datasource / 'uploaded_gates.csv'
    if file_path.is_file() == False:
        abort(422)
    csv = pd.read_csv(file_path)
    obj = csv.to_dict(orient='records')
    return serialize_and_submit_json(obj)

# NOTE: '/get_uploaded_channel_csv_values' (which read uploaded_channels.csv off
# the server's disk) was replaced by '/get_omero_channel_csv_values' above.

@app.route('/get_saved_channel_list', methods=['GET'])
def get_saved_channel_list():
    datasource = request.args.get('datasource')
    resp = data_model.get_saved_channel_list(datasource)
    return serialize_and_submit_json(resp)

# E.G /generated/data/melanoma/channel_00_files/13/16_18.png
@app.route('/generated/data/<string:datasource>/<string:channel>/<string:level>/<string:tile>')
def generate_png(datasource, channel, level, tile):
    now = time()
    png = data_model.generate_zarr_png(datasource, channel, level, tile)
    file_object = io.BytesIO()
    # write PNG in file-object
    Image.fromarray(png).save(file_object, 'PNG', compress_level=0)
    # move to beginning of file so `send_file()` it will read from start
    file_object.seek(0)
    return send_file(file_object, mimetype='image/PNG')

def serialize_and_submit_json(data):
    response = app.response_class(
        response=orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY),
        mimetype='application/json'
    )
    return response

@app.route('/get_cells_in_polygon', methods=['POST'])
def get_cells_in_polygon():
    post_data = json.loads(request.data)
    datasource = post_data['datasource']
    points = post_data['points']
    resp = data_model.get_cells_in_polygon(datasource, points)
    return serialize_and_submit_json(resp)

@app.route('/get_cells_in_lassos', methods=['POST'])
def get_cells_in_lassos():
    post_data = json.loads(request.data)
    datasource = post_data['datasource']
    list_lassos = post_data['list_lassos']
    resp = data_model.get_cells_in_lassos(datasource, list_lassos)
    return serialize_and_submit_json(resp)
