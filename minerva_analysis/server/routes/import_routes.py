# CRUD for Datasources

# import sys
# sys.path.append('/c/Users/Sophie/minerva_analysis/')
from minerva_analysis import app, get_config_names, config_json_path, data_path, cwd_path
from minerva_analysis.server.utils import mostFrequentLongestSubstring, pre_normalization
from minerva_analysis.server.utils.wsi_to_ometiff import wsi_to_ometiff
from minerva_analysis.server.utils.qptiff_to_ometiff import qptiff_to_ometiff
from minerva_analysis.server.utils.omero_csv_to_mcmicro import is_omero_csv, omero_csv_to_mcmicro
from minerva_analysis.server.models import data_model

from flask import render_template, request, Response, jsonify
from pathlib import Path
from pathlib import PurePath

import werkzeug.datastructures as wz
import numpy as np
import pandas as pd
import shutil
import csv
import json
import orjson
import os
from os import walk
import io


# Extensions that need conversion before the OME-TIFF pipeline
_WSI_EXTENSIONS = {'.svs', '.ndpi'}
_QPTIFF_EXTENSIONS = {'.qptiff'}
_ALL_SOURCE_EXTENSIONS = _WSI_EXTENSIONS | _QPTIFF_EXTENSIONS
_NATIVE_EXTENSIONS = {'.tif', '.tiff', '.ome.tif', '.ome.tiff'}


def _convert_channel_to_ometiff(input_path, output_dir):
    """Convert a non-OME-TIFF channel image to OME-TIFF if needed.

    Args:
        input_path: Path to the input channel file (.svs, .ndpi, .qptiff, or .tif).
        output_dir: Directory to write the converted file into.

    Returns:
        Path to the converted .ome.tif file, or the original path if already
        a native TIFF format.
    """
    input_path = Path(input_path)
    ext = input_path.suffix.lower()

    # Already a native TIFF — no conversion needed
    # Check for .ome.tif/.ome.tiff (double suffix) first
    if ''.join(input_path.suffixes[-2:]).lower() in ('.ome.tif', '.ome.tiff'):
        return input_path
    if ext in _NATIVE_EXTENSIONS:
        return input_path

    output_path = Path(output_dir) / (input_path.stem + '.ome.tif')

    if ext in _WSI_EXTENSIONS:
        wsi_to_ometiff(input_path, output_path=output_path)
    elif ext in _QPTIFF_EXTENSIONS:
        qptiff_to_ometiff(input_path, output_path=output_path)
    else:
        raise ValueError(
            f"Unsupported channel image format '{ext}'. "
            f"Supported: {', '.join(sorted(_ALL_SOURCE_EXTENSIONS | _NATIVE_EXTENSIONS))}"
        )

    return output_path


total_tasks = 100
completed_task = 0
current_task = ''


@app.route('/edit_config', methods=['GET'])
def edit_config_with_request_object():
    config_name = request.args.get("config")
    return edit_config_with_config_name(config_name)


@app.route('/edit_config/<string:config_name>')
def edit_config_with_request_name(config_name):
    return edit_config_with_config_name(config_name)


@app.route('/delete/<string:config_name>')
def delete_with_datasource_name(config_name):
    global config_json_path

    path = str(data_path / config_name)
    if Path(path).exists():
        shutil.rmtree(path)
    with open(config_json_path, "r+") as configJson:
        config_data = json.load(configJson)
        del config_data[config_name]
        configJson.seek(0)  # <--- should reset file position to the beginning.
        json.dump(config_data, configJson, indent=4)
        configJson.truncate()
    return render_template("index.html", data={'datasource': '', 'datasources': get_config_names()})


def edit_config_with_config_name(config_name):
    data = {}
    global config_json_path
    with open(config_json_path, "r+") as configJson:
        config_csv = json.load(configJson)
        config_data = config_csv[config_name]
        data['datasetName'] = config_name
        # test_data['channelFileNames'] = ['channel_01', 'channel_02']
        data['csvName'] = config_data['featureData'][0]['src'].split("/")[-1]
        if 'celltypeData' in config_data['featureData'][0]:
            data['celltypeData'] = config_data['featureData'][0]['celltypeData']

        if 'shapes' in config_data:
            data['shapes'] = config_data['shapes']

        if 'activeChannel' in config_data:
            data['activeChannel'] = config_data['activeChannel']

        if 'normalization' in config_data['featureData'][0]:
            data['normalization'] = config_data['featureData'][0]['normalization']

        if 'isTransformed' in config_data['featureData'][0]:
            data['isTransformed'] = config_data['featureData'][0]['isTransformed']

        if 'clusterData' in config_data:
            data['normCsvName'] = config_data['clusterData']

        if 'maxLevel' in config_data:
            data['maxLevel'] = config_data['maxLevel']
        if 'height' in config_data:
            data['height'] = config_data['height']

        if 'width' in config_data:
            data['width'] = config_data['width']

        if 'segmentation' in config_data:
            data['segmentation'] = config_data['segmentation']

        if 'channelFile' in config_data:
            data['channelFile'] = config_data['channelFile']

        if 'num_channels' in config_data:
            data['num_channels'] = config_data['num_channels']

        if 'tileHeight' in config_data:
            data['tileHeight'] = config_data['tileHeight']

        if 'tileWidth' in config_data:
            data['tileWidth'] = config_data['tileWidth']

        csvHeaders = []
        channelFileNames = []
        if 'idField' in config_data['featureData'][0]:
            data['idField'] = True
            elem = {}
            elem['fullName'] = config_data['featureData'][0]['idField']
            elem['displayName'] = config_data['featureData'][0]['idField']
            csvHeaders.append(elem)
            channelFileNames = ['ID']
        else:
            data['idField'] = False;
        # add x cord
        elem = {}
        elem['fullName'] = config_data['featureData'][0]['xCoordinate']
        elem['displayName'] = config_data['featureData'][0]['xCoordinate']
        csvHeaders.append(elem)
        # add y cord
        elem = {}
        elem['fullName'] = config_data['featureData'][0]['yCoordinate']
        elem['displayName'] = config_data['featureData'][0]['yCoordinate']
        csvHeaders.append(elem)
        # add cell type
        if 'celltypeData' in config_data['featureData'][0]:
            elem = {}
            elem['fullName'] = config_data['featureData'][0]['celltype']
            elem['displayName'] = config_data['featureData'][0]['celltype']
            csvHeaders.append(elem)

        # Start with the required channels
        if 'celltypeData' in config_data['featureData'][0]:
            channelFileNames.extend(['Area', 'X Position', 'Y Position', 'Cell Type'])
        else:
            channelFileNames.extend(['Area', 'X Position', 'Y Position'])

        for i in range(len(config_data['imageData'])):
            elem = config_data['imageData'][i]
            channelName = elem['src'].split("/")[-2]
            header = {}
            header['fullName'] = elem['fullname']
            header['displayName'] = elem['name']
            # Special handling for label channel
            if i == 0:
                data['labelName'] = channelName
                if data['idField']:
                    csvHeaders.insert(1, header)
                else:
                    csvHeaders.insert(0, header)
            else:
                channelFileNames.append(channelName)
                csvHeaders.append(header)

        data['csvHeader'] = csvHeaders
        header_full_names = [elem['displayName'] for elem in csvHeaders]
        data['substring'] = mostFrequentLongestSubstring.find_substring(header_full_names)
        data['channelFileNames'] = channelFileNames
        data['datasources'] = [key for key in config_csv.keys()]
        return render_template('channel_match.html', data=data)


@app.route('/upload', methods=['GET', 'POST'])
def upload_file_page():
    global total_tasks
    global completed_task
    global current_task
    total_tasks = 1
    completed_task = 0
    current_task = "Uploading"
    datasetName = None
    csvName = ''
    celltypeName = ''
    channelFileNames = ['ID', 'Area', 'X Position', 'Y Position']
    labelName = ''
    csvHeader = None
    if request.method == 'POST':
        try:
            if request.form['action'] == 'Upload':
                # if we have a fully user specified data upload
                if request.form.get('mcmicro_name') is None:
                    #dataset name
                    datasetName = request.form['name']

                    #label file
                    labelFile = request.form.get('label_file')
                    labelFile = labelFile.replace('"', '') # remove " characters
                    labelFile = Path(labelFile)
                    labelName = os.path.splitext(labelFile.name)[0]

                    #csv file
                    csvPath = request.form.get('csv_file');
                    csvPath = csvPath.replace('"', '') # remove " characters
                    csvPath = Path(csvPath)
                    pathsSplit = PurePath(csvPath).parts
                    csvName = pathsSplit[len(pathsSplit) - 1]

                    #channel file
                    channelFile = request.form.get('channel_file')
                    channelFile = channelFile.replace('"', '') # remove " characters
                    channelFile = Path(channelFile)

                # if a mcmicro output structure is used
                else:
                    directory = request.form['mcmicro_output_folder']
                    pathsSplit = PurePath(directory).parts
                    mcmicroDirName = pathsSplit[len(pathsSplit)-1]

                    #dataset name is optional, if not provided mcmicro name is used
                    datasetName = mcmicroDirName
                    if request.form.get('mcmicro_name') != '':
                        datasetName = request.form['mcmicro_name']

                    #label file
                    labelFile = request.form.get('segs')
                    labelFile = labelFile.replace('"', '') # remove " characters
                    labelFile = Path(labelFile)
                    labelName = os.path.splitext(labelFile.name)[0]

                    #csv file
                    csvPath = request.form.get('masks');
                    csvPath = csvPath.replace('"', '') # remove " characters
                    csvPath = Path(csvPath)
                    pathsSplit = PurePath(csvPath).parts
                    csvName = pathsSplit[len(pathsSplit) - 1]

                    #channel file
                    channelFile = request.form.get('images')
                    channelFile = channelFile.replace('"', '') # remove " characters
                    channelFile = Path(channelFile)

                # Creates file path using name input; should change this so that it just takes directory name?
                file_path = str(PurePath(Path.cwd(), data_path, datasetName))
                if not Path(file_path).exists(): # If no directory for existing name for dataset input will create one
                    Path(file_path).mkdir()

                # Convert non-OME-TIFF channel images (SVS, NDPI, QPTIFF)
                needs_conversion = channelFile.suffix.lower() in _ALL_SOURCE_EXTENSIONS
                total_tasks = 3 if needs_conversion else 2
                if needs_conversion:
                    current_task = "Converting channel image to OME-TIFF"
                    channelFile = _convert_channel_to_ometiff(channelFile, file_path)
                    completed_task += 1

                # Process CSV File

                # open original csv location
                csvFile = [open(csvPath)]
                # file path to write to on server
                serverCsvPath = str(Path(file_path) / csvName)
                f = open(serverCsvPath, 'w')
                # write to new location on server
                f.write(csvFile[0].read())
                f.close()
                csvFile[0].close()

                # Detect and convert OMERO CSV format to mcmicro format
                with open(serverCsvPath, 'r') as probe:
                    probe_header = csv.DictReader(probe).fieldnames
                if is_omero_csv(probe_header):
                    omero_csv_to_mcmicro(serverCsvPath)

                # read field names from server location (possibly converted)
                csvPath = Path(serverCsvPath)
                with open(csvPath, 'r') as infile:
                    reader = csv.DictReader(infile)
                    csvHeader = reader.fieldnames

                # Process Channel File
                current_task = "Converting OME-TIFF Channels (This Will Take a While)"
                channel_info = data_model.convertOmeTiff(channelFile, dataDirectory=file_path, isLabelImg=False)
                # If a pyramidal copy was built, use it as the channel file
                if 'channelFile' in channel_info:
                    channelFile = Path(channel_info['channelFile'])
                channelFileNames.extend(channel_info['channel_names'])
                completed_task += 1

                #Process Segmentation File
                current_task = "Converting Segmentation Mask"
                label_info = data_model.convertOmeTiff(labelFile, channelFilePath=channelFile, dataDirectory=file_path, isLabelImg=True)
                completed_task += 1
                current_task = total_tasks
                current_task = 'Complete'
                config_data = {}
                full_csv_header = []

                # now iterate fields for the config overview
                for header in csvHeader:
                    elem = {}
                    elem['fullName'] = header
                    full_csv_header.append(elem)

                config_data['csvHeader'] = full_csv_header
                header_full_names = [elem['fullName'] for elem in full_csv_header]
                config_data['substring'] = mostFrequentLongestSubstring.find_substring(header_full_names)
                config_data['datasetName'] = datasetName

                # Use the maximum pyramid depth across channels and segmentation
                # so the client can request the finest zoom levels available.
                seg_max = label_info.get('maxLevel', 1)
                config_data['maxLevel'] = max(channel_info['maxLevel'], seg_max)
                config_data['height'] = channel_info['height']
                config_data['width'] = channel_info['width']
                config_data['segmentation'] = label_info['segmentation']

                config_data['num_channels'] = channel_info['num_channels']
                config_data['tileHeight'] = channel_info['tileHeight']
                config_data['tileWidth'] = channel_info['tileWidth']

                config_data['datasetName'] = datasetName
                config_data['channelFileNames'] = channelFileNames
                config_data['csvName'] = csvName

                config_data['channelFile'] = str(channelFile)
                config_data['new'] = True
                config_data['labelName'] = labelName
                config_data['datasources'] = get_config_names()
                config_data['datasources'].append(datasetName)

                datasource = pd.read_csv(csvPath)
                # Columns that are metadata/morphology, not marker intensities.
                # Includes both mcmicro and OMERO naming conventions.
                listNotMarkers = [
                    # mcmicro conventions
                    'CellID', 'X_centroid', 'Y_centroid', 'Area',
                    'MajorAxisLength', 'MinorAxisLength', 'Eccentricity',
                    'Solidity', 'Extent', 'Orientation',
                    'column_centroid', 'row_centroid', 'phenotype',
                    # OMERO table conventions
                    'object', 'prob', 'geometry', 'centroid',
                    'Bbox_min_x', 'Bbox_min_y', 'Bbox_max_x', 'Bbox_max_y',
                    'tile_index', 'orig_object',
                    'Perimeter', 'Centroid_x', 'Centroid_y',
                    'Longest_axis', 'Convexity',
                    'Compactness_circle', 'Compactness_square',
                    'Area_convex', 'Min_rot_rect', 'Elongation',
                    'Major_axis', 'Minor_axis',
                    'Circular_diameter', 'Euler_number',
                ]
                listImageData = [name for name in header_full_names if name not in listNotMarkers]
                datasourceImageData = datasource[[*listImageData]]
                if np.mean(np.mean(datasourceImageData)) < 15:
                    config_data["isTransformed"] = True
                else:
                    config_data["isTransformed"] = False

                return render_template('channel_match.html', data=config_data)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"upload error: {e}", flush=True)
            completed_task = -1
            current_task = str(e)
            return render_template('index.html')
            # Now Edit Config.Json With my my Data
    print("Finished Updating Config.json")
    return render_template('index.html')


@app.route('/progress')
def progress():
    def generate():
        global total_tasks
        global completed_task
        global current_task
        data = {}
        # Error Handling
        if current_task == -1:
            data['percentage'] = -1
            data['currentTask'] = current_task
        else:
            if total_tasks == 0:
                total_tasks = 100
            percentage = int((completed_task / total_tasks) * 100)
            data['percentage'] = percentage
            data['currentTask'] = current_task
        print("Percentage:", percentage, completed_task, total_tasks, current_task)
        yield "data:" + json.dumps(data) + "\n\n"

    return Response(generate(), mimetype='text/event-stream')


# Matches channels to CSV Headers
@app.route('/channel-test')
def channel():
    test_data = {}
    test_data['csvHeader'] = ['A488_background_none_1Nuclei', 'A555_background_none_2Nuclei', 'A555_Alexa555_18Nuclei',
                              'A647_background_none_3Nuclei', 'CATENIN_Alexa647_19Nuclei', 'CD3_Alexa555_10Nuclei',
                              'CD45RO_Alexa488_13Nuclei', 'CD45_Alexa555_30Nuclei', 'CD4_Alexa488_9Nuclei',
                              'CD8_Alexa647_11Nuclei', 'DNA2_Hoechst33342_4Nuclei', 'DNA3_Hoechst33342_8Nuclei',
                              'DNA4_Hoechst33342_12Nuclei', 'DNA5_Hoechst33342_16Nuclei', 'DNA6_Hoechst33342_20Nuclei',
                              'DNA7_Hoechst33342_24Nuclei', 'DNA8_Hoechst33342_28Nuclei', 'DNA9_Hoechst33342_32Nuclei',
                              'DNA_Hoechst33342_0Nuclei', 'ECAD_Alexa488_17Nuclei', 'FOXP3_Alexa555_14Nuclei',
                              'HES1_Alexa647_27Nuclei', 'KERATIN_Alexa555_26Nuclei', 'KI67_Alexa488_5Nuclei',
                              'MITF_Alexa488_25Nuclei', 'NGFR_Alexa647_23Nuclei', 'PD1_Alexa647_15Nuclei',
                              'PDL1_Alexa647_7Nuclei', 'S100_Alexa488_33Nuclei', 'SMA_Alexa647_35Nuclei',
                              'VEGFR2_Alexa555_34Nuclei', 'VIMENTIN_Alexa555_6Nuclei', 'cJUN_Alexa488_21Nuclei',
                              'pERKz_Alexa647_31Nuclei', 'pRB_Alexa555_22Nuclei', 'pS6_Alexa488_29Nuclei',
                              'NucleusArea', 'CellPosition_X', 'CellPosition_Y']
    test_data['datasetName'] = 'channelConfigs'
    test_data['substring'] = mostFrequentLongestSubstring.find_substring(test_data['csvHeader'])
    test_data['channelFileNames'] = ['ID', 'Area', 'X Position', 'Y Position', 'channel_01', 'channel_02']
    test_data['normCsvName'] = 'segResultsRF_norm.csv'
    test_data['csvName'] = 'segResultsRF.csv'
    test_data['labelName'] = 'nucleiLabelRF'
    test_data['new'] = True
    test_data['datasources'] = get_config_names()

    return render_template('channel_match.html', data=test_data)


@app.route('/save_config', methods=['POST'])
def save_config():
    global config_json_path
    try:
        originalData = request.json['originalData']
        datasetName = originalData['datasetName']
        csvName = originalData['csvName']
        if 'celltypeData' in originalData:
            celltypeName = originalData['celltypeData']
        idList = request.json['idField']
        headerList = request.json['headerList']

        isTransformed = originalData['isTransformed']
        transformData = request.json['transformData']
        if not isTransformed and transformData:
            print("Transforming Data")
            skip_columns = []
            if idList[2]['value'] != 'on':
                skip_columns.append(idList[0]['value'])
            for i in range(int(len(headerList) / 3)):
                column_name = headerList[i * 3]['value']
                normalize_column = headerList[i * 3 + 2]['value']
                if normalize_column != 'on':
                    skip_columns.append(column_name)
            file_path = str(Path(cwd_path, data_path, datasetName))
            csvPath = str(Path(file_path) / csvName)
            # pre_normalization.preNormalize(csvPath, normPath, skip_columns=skip_columns)
            data_model.logTransform(csvPath, skip_columns=skip_columns)
            print("Finished Transforming Data")
        # elif 'normalizeCsvName' in request.json:
        #     normCsvName = request.json['normalizeCsvName']
        # else:
        #     normCsvName = None
        if 'normalizeCsvName' in request.json:
            normCsvName = request.json['normalizeCsvName']
        else:
            normCsvName = None

        headerList = [x for x in zip(headerList[1::3], headerList[0::3])]
        channelList = originalData['channelFileNames']
        with open(config_json_path, "r+") as configJson:
            configData = json.load(configJson)
            configData[datasetName] = {}
            configData[datasetName]['shapes'] = ''
            if normCsvName:
                configData[datasetName]['clusterData'] = normCsvName
            configData[datasetName]['activeChannel'] = ''
            configData[datasetName]['featureData'] = [{}]
            configData[datasetName]['featureData'][0]['normalization'] = 'none'
            if 'celltypeData' in originalData:
                configData[datasetName]['featureData'][0]['celltypeData'] = str(data_path / datasetName / celltypeName)
                configData[datasetName]['featureData'][0]['celltype'] = headerList[3][1]['value']
            configData[datasetName]['featureData'][0]['xCoordinate'] = headerList[1][1]['value']
            configData[datasetName]['featureData'][0]['yCoordinate'] = headerList[2][1]['value']

            # If optional id field
            if 'idField' in request.json:
                channelList.pop(0)
                configData[datasetName]['featureData'][0]['idField'] = request.json['idField'][1]['value']

            if 'shapes' in originalData:
                configData[datasetName]['shapes'] = originalData['shapes']

            if 'height' in originalData:
                configData[datasetName]['height'] = originalData['height']

            if 'width' in originalData:
                configData[datasetName]['width'] = originalData['width']

            if 'maxLevel' in originalData:
                configData[datasetName]['maxLevel'] = originalData['maxLevel']

            if 'num_channels' in originalData:
                configData[datasetName]['num_channels'] = originalData['num_channels']

            if 'tileWidth' in originalData:
                configData[datasetName]['tileWidth'] = originalData['tileWidth']

            if 'tileHeight' in originalData:
                configData[datasetName]['tileHeight'] = originalData['tileHeight']

            if 'segmentation' in originalData:
                configData[datasetName]['segmentation'] = originalData['segmentation']

            if 'channelFile' in originalData:
                configData[datasetName]['channelFile'] = originalData['channelFile']

            # OMERO live-tile datasource: the channel image stays in OMERO and is
            # served from its own pyramid via omero_image_id (no local channelFile).
            if 'omero_image_id' in originalData and originalData['omero_image_id']:
                configData[datasetName]['omero_image_id'] = int(originalData['omero_image_id'])

            if 'activeChannel' in originalData:
                configData[datasetName]['activeChannel'] = originalData['activeChannel']

            if 'normalization' in originalData:
                configData[datasetName]['featureData'][0]['normalization'] = originalData['normalization']

            if isTransformed or transformData:
                configData[datasetName]['featureData'][0]['isTransformed'] = True
            else:
                configData[datasetName]['featureData'][0]['isTransformed'] = False

            configData[datasetName]['featureData'][0][
                'src'] = str(data_path / datasetName / csvName)
            # Adding the Label Channel as the First Label
            configData[datasetName]['imageData'] = [{}]
            configData[datasetName]['imageData'][0]['name'] = headerList[0][1]['value']
            configData[datasetName]['imageData'][0]['fullname'] = 'Area'
            if 'labelName' in originalData and originalData['labelName'] != '':
                configData[datasetName]['imageData'][0]['src'] = "/generated/data/" + datasetName + "/" + originalData[
                    'labelName'] + "/"
            else:
                configData[datasetName]['imageData'][0]['src'] = ''

            if 'celltypeData' in originalData:
                channelList = channelList[4:]
            else:
                channelList = channelList[3:]

            if 'celltypeData' in originalData:
                channelStart = 4
            else:
                channelStart = 3
            for i in range(len(channelList)):
                channel = channelList[i]
                channelData = {}
                channelData['src'] = "/generated/data/" + datasetName + "/" + channel + "/"
                channelData['name'] = headerList[i + channelStart][0]['value']
                channelData['fullname'] = headerList[i + channelStart][1]['value']
                configData[datasetName]['imageData'].append(channelData)
            configJson.seek(0)  # <--- should reset file position to the beginning.
            json.dump(configData, configJson, indent=4)
            configJson.truncate()
            data_model.load_datasource(datasetName, reload=True)
            resp = jsonify(success=True)
            return resp

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"save_config error: {e}", flush=True)
        resp = jsonify(success=False)
        return resp

@app.route('/get_mc_segmentation_file_list', methods=['POST'])
def list_tif_files_in_dir():
    # return all seg files found in the seg subfolder (mc micro specific)
    files = []
    files.append('')

    #path and type information from upload
    post_data = json.loads(request.data)
    if 'path' in post_data:
        # path = Path(post_data['path'], "segmentation");
        path = Path(post_data['path'])

        #for segmentation, mcmicro specifics
        # mask_types = ["cell", "cellRing", "cyto", "cytoRing", "nuclei", "nucleiRing"]

        if path.is_dir():
            for (dirpath, dirnames, filenames) in walk(path):
                for (file) in filenames:
                    file_split = file.split('.')
                    # if file[0] in mask_types:
                    #     files.append(file[0])
                    if (file_split[-1] == 'tif' and file_split[-2] == 'ome') or (file_split[-1] == 'tiff' and file_split[-2] == 'ome'):
                        file_path = os.path.join(dirpath, file)
                        files.append(file_path)
                # Also discover .zarr directories (OMERO OME-NGFF segmentation masks)
                for dirname in dirnames:
                    if dirname.endswith('.zarr'):
                        files.append(os.path.join(dirpath, dirname))
            print(files)
    else:
        print('error in segmentation path');
    return serialize_and_submit_json(files)

@app.route('/get_mc_csv_file_list', methods=['POST'])
def list_csv_files_in_dir():
    # return all seg files found in the seg subfolder (mc micro specific)
    files = []
    files.append('')

    #path and type information from upload
    post_data = json.loads(request.data)
    if 'path' in post_data:
        # path = Path(post_data['path'], "segmentation");
        path = Path(post_data['path'])

        #for segmentation, mcmicro specifics
        # mask_types = ["cell", "cellRing", "cyto", "cytoRing", "nuclei", "nucleiRing"]

        if path.is_dir():
            for (dirpath, dirnames, filenames) in walk(path):
                for (file) in filenames:
                    file_split = file.split('.')
                    # if file[0] in mask_types:
                    #     files.append(file[0])
                    if file_split[-1] == 'csv':
                        file_path = os.path.join(dirpath, file)
                        files.append(file_path)
            print(files)
    else:
        print('error in csv path');
    return serialize_and_submit_json(files)

@app.route('/check_mc_csv_file_existence', methods=['POST'])
def check_mc_csv_file_existence():
    # path and type information from upload
    post_data = json.loads(request.data)
    if 'path' in post_data:
        if 'mask' in post_data:
            #get path and last bit which defines the dirname
            mask = post_data['mask']
            directory = Path(post_data['path'])
            pathsSplit = PurePath(directory).parts
            mcmicroDirName = pathsSplit[len(pathsSplit) - 1]
        
            path = Path(post_data['mask'])
            if path.suffix.lower() == '.csv':
                return serialize_and_submit_json(True)

    return serialize_and_submit_json(False)

@app.route('/check_mc_output_folder', methods=['POST'])
def check_mc_output_folder():
    """Validate that an MCMICRO output folder contains .ome.tif and .csv files."""
    post_data = json.loads(request.data)
    result = {'path_exists': False, 'has_ome_tif': False, 'has_csv': False}

    if 'path' not in post_data:
        return serialize_and_submit_json(result)

    path = Path(post_data['path'])
    if not path.is_dir():
        return serialize_and_submit_json(result)

    result['path_exists'] = True

    for dirpath, dirnames, filenames in walk(str(path)):
        for f in filenames:
            name_lower = f.lower()
            if name_lower.endswith('.ome.tif') or name_lower.endswith('.ome.tiff'):
                result['has_ome_tif'] = True
            if name_lower.endswith('.csv'):
                result['has_csv'] = True
            if result['has_ome_tif'] and result['has_csv']:
                return serialize_and_submit_json(result)

    return serialize_and_submit_json(result)

@app.route('/check_file_existence', methods=['POST'])
def check_file_existence():
    # path and type information from upload
    post_data = json.loads(request.data)
    if 'path' in post_data:
        path = Path(post_data['path'])
        if path.is_file():
            return serialize_and_submit_json(True)
        # .zarr segmentation masks are directories
        if path.suffix == '.zarr' and path.is_dir():
            return serialize_and_submit_json(True)
        return serialize_and_submit_json(False)

@app.route('/check_path_existence', methods=['POST'])
def check_path_existence():
    # path and type information from upload
    post_data = json.loads(request.data)
    if 'path' in post_data:
        path = Path(post_data['path'])
        # if full path exists
        if path.is_dir():
            return serialize_and_submit_json(True)
        # if path does not exist
        return serialize_and_submit_json(False)

@app.route('/dataset_existence', methods=['POST'])
def check_dataset_exists():
    # path and type information from upload
    post_data = json.loads(request.data)
    if 'dataset_name' in post_data:
        dataset_name = Path(post_data['dataset_name'])
        # if path exists as a relative path inside the data folder
        path = Path(Path.cwd(), data_path, dataset_name);
        if not path.is_dir() or dataset_name.name == '':
            return serialize_and_submit_json(False)
    return serialize_and_submit_json(True)

@app.route('/init_datasource', methods=['GET'])
def init_datasource():
    datasource = request.args.get('datasource')
    data_model.init(datasource)
    resp = jsonify(success=True)
    return resp


def serialize_and_submit_json(data):
    response = app.response_class(
        response=orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY),
        mimetype='application/json'
    )
    return response


@app.route('/open_from_omero', methods=['GET', 'POST'])
def open_from_omero():
    """Landing page for the OMERO.web "Open in Gater" hand-off.

    Renders a lightweight progress page immediately; that page opens the shared
    /progress SSE and fetches /open_from_omero_run (the slow download/convert),
    then swaps in the config-check page when done. Reuses the same progress-bar
    mechanism as the local upload flow instead of leaving the tab blank for the
    couple of minutes the bridge takes.
    """
    global total_tasks, completed_task, current_task
    src = request.values
    datasetName = src.get('name')
    omero_image_id = src.get('image_id') or src.get('omero_image_id')
    quant_ann = src.get('quant')
    seg_ann = src.get('seg')
    missing = [k for k, v in (('name', datasetName), ('image_id', omero_image_id),
                              ('quant', quant_ann), ('seg', seg_ann)) if not v]
    if missing:
        return "Missing required params: %s" % ", ".join(missing), 400
    # Reset progress so the SSE starts at 0 (not a stale 100 from a prior run).
    total_tasks, completed_task, current_task = 6, 0, "Starting"
    return render_template('omero_loading.html', name=datasetName)


@app.route('/open_from_omero_run', methods=['GET', 'POST'])
def open_from_omero_run():
    """Worker for the OMERO.web hand-off (see open_from_omero). Does the actual
    download/convert while driving the total_tasks/completed_task/current_task
    globals that the /progress SSE streams to the loading page.

    Params (GET or POST): name, image_id, quant (FileAnnotation id),
    seg (FileAnnotation id, a zipped .zarr). The image stays in OMERO and is
    served live-tile via its own pyramid (omero_image_id); only the CSV and the
    segmentation are downloaded, the seg becomes a pyramidal OME-TIFF, and the
    config-check page (channel_match.html) is rendered — like a local upload.
    """
    global total_tasks, completed_task, current_task
    import zipfile
    import tifffile as tf
    from minerva_analysis.server.utils.omero_zarr_to_ometiff import omero_zarr_to_ometiff

    src = request.values
    datasetName = src.get('name')
    omero_image_id = src.get('image_id') or src.get('omero_image_id')
    quant_ann = src.get('quant')
    seg_ann = src.get('seg')
    missing = [k for k, v in (('name', datasetName), ('image_id', omero_image_id),
                              ('quant', quant_ann), ('seg', seg_ann)) if not v]
    if missing:
        return "Missing required params: %s" % ", ".join(missing), 400
    total_tasks, completed_task, current_task = 6, 0, "Downloading quantification"

    try:
        file_path = Path(cwd_path, data_path, datasetName)
        file_path.mkdir(parents=True, exist_ok=True)

        # 1. Quantification CSV: download + (if OMERO-format) convert to mcmicro.
        current_task = "Downloading quantification"
        csvName = datasetName + '_quantification.csv'
        serverCsvPath = str(file_path / csvName)
        data_model.download_omero_annotation(quant_ann, serverCsvPath)
        completed_task = 1
        with open(serverCsvPath, 'r') as probe:
            probe_header = csv.DictReader(probe).fieldnames
        if is_omero_csv(probe_header):
            current_task = "Converting quantification"
            omero_csv_to_mcmicro(serverCsvPath)
        with open(serverCsvPath, 'r') as infile:
            csvHeader = csv.DictReader(infile).fieldnames
        completed_task = 2

        # 2. Segmentation: download zip -> unzip -> convert .zarr to pyramidal
        #    OME-TIFF (Gater computes/stores the seg pyramid).
        current_task = "Downloading segmentation"
        seg_zip = str(file_path / 'segmentation.zip')
        data_model.download_omero_annotation(seg_ann, seg_zip)
        completed_task = 3
        current_task = "Unzipping segmentation"
        with zipfile.ZipFile(seg_zip) as zf:
            zf.extractall(str(file_path))
        zarr_dirs = [p for p in file_path.iterdir()
                     if p.is_dir() and p.suffix == '.zarr']
        if not zarr_dirs:
            raise ValueError("No .zarr directory found in the segmentation zip")
        seg_zarr = zarr_dirs[0]
        seg_ometiff = file_path / (seg_zarr.stem + '.ome.tif')
        completed_task = 4
        current_task = "Building segmentation pyramid (this can take a while)"
        if not seg_ometiff.exists():
            omero_zarr_to_ometiff(seg_zarr, output_path=seg_ometiff)
        labelName = seg_zarr.stem
        seg_io = tf.TiffFile(str(seg_ometiff), is_ome=False)
        seg_max = len(seg_io.series[0].levels)
        seg_io.close()
        completed_task = 5

        # 3. Channel info from OMERO metadata (image served live-tile).
        current_task = "Reading channel metadata"
        channel_info = data_model.get_omero_channel_info(
            omero_image_id, name_prefix=datasetName)
        channelFileNames = ['ID', 'Area', 'X Position', 'Y Position']
        channelFileNames.extend(channel_info['channel_names'])

        # 4. Assemble the config-check payload (mirrors upload_file_page).
        full_csv_header = [{'fullName': h} for h in csvHeader]
        header_full_names = [e['fullName'] for e in full_csv_header]
        config_data = {
            'csvHeader': full_csv_header,
            'substring': mostFrequentLongestSubstring.find_substring(header_full_names),
            'datasetName': datasetName,
            'maxLevel': max(channel_info['maxLevel'], seg_max),
            'height': channel_info['height'],
            'width': channel_info['width'],
            'segmentation': str(seg_ometiff),
            'num_channels': channel_info['num_channels'],
            'tileHeight': channel_info['tileHeight'],
            'tileWidth': channel_info['tileWidth'],
            'channelFileNames': channelFileNames,
            'csvName': csvName,
            'channelFile': '',                      # live-tile: no local channel file
            'omero_image_id': int(omero_image_id),
            'new': True,
            'labelName': labelName,
            'datasources': get_config_names() + [datasetName],
        }

        listNotMarkers = [
            'CellID', 'X_centroid', 'Y_centroid', 'Area', 'MajorAxisLength',
            'MinorAxisLength', 'Eccentricity', 'Solidity', 'Extent',
            'Orientation', 'column_centroid', 'row_centroid', 'phenotype',
            'object', 'prob', 'geometry', 'centroid', 'Bbox_min_x',
            'Bbox_min_y', 'Bbox_max_x', 'Bbox_max_y', 'tile_index',
            'orig_object', 'Perimeter', 'Centroid_x', 'Centroid_y',
            'Longest_axis', 'Convexity', 'Compactness_circle',
            'Compactness_square', 'Area_convex', 'Min_rot_rect', 'Elongation',
            'Major_axis', 'Minor_axis', 'Circular_diameter', 'Euler_number',
        ]
        try:
            listImageData = [n for n in header_full_names if n not in listNotMarkers]
            dImg = pd.read_csv(Path(serverCsvPath))[[*listImageData]]
            config_data['isTransformed'] = bool(np.mean(np.mean(dImg)) < 15)
        except Exception:
            config_data['isTransformed'] = False

        completed_task = 6
        current_task = "Complete"
        return render_template('channel_match.html', data=config_data)
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Signal the loading page's /progress SSE to show the error state.
        completed_task = -1
        current_task = str(e)
        return "Failed to open from OMERO: %s" % e, 500

